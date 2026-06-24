"""
app/heartbeat/__init__.py — Production-grade BetterStack Heartbeat Monitoring
==============================================================================

Overview
--------
This module provides two complementary monitoring endpoints and a background
self-ping mechanism for BetterStack Uptime (https://betterstack.com/uptime).

Endpoints
---------
  GET /heartbeat          — Lightweight public liveness probe (used by BetterStack).
  GET /api/heartbeat      — Alias for the same endpoint.
  GET /health             — Rich internal health report (DB, app version, uptime).
                            Replaces the basic /health route in app/__init__.py.

Security
--------
  Set HEARTBEAT_SECRET in .env to require a shared secret on /heartbeat.
  Pass it via:
    • Query param:  /heartbeat?token=<secret>
    • HTTP header:  X-Heartbeat-Token: <secret>
  If HEARTBEAT_SECRET is empty or unset, the endpoint is unauthenticated
  (suitable for simple BetterStack "URL monitor" pings).

Self-Ping (optional)
--------------------
  When SELF_PING_ENABLED=true, a daemon thread wakes every SELF_PING_INTERVAL
  seconds, hits /heartbeat locally, and forwards a ping to BetterStack
  (BETTERSTACK_HEARTBEAT_URL).  This confirms the app is truly alive even when
  the BetterStack → App network path has issues.

Configuration (.env)
--------------------
  BETTERSTACK_HEARTBEAT_URL   Full BetterStack heartbeat URL (required for
                               BetterStack pings).  Example:
                               https://uptime.betterstack.com/api/v1/heartbeat/xxxx
  HEARTBEAT_SECRET            Optional shared secret token.
  SELF_PING_ENABLED           true | false  (default: false)
  SELF_PING_INTERVAL          Seconds between self-pings (default: 60).
  SELF_PING_BASE_URL          Base URL for self-ping, e.g. http://localhost:5000.
                               Auto-detected from SERVER_NAME when omitted.

Design decisions
----------------
  • Blueprint-based so it's pluggable and testable.
  • /heartbeat does ONE lightweight DB probe (SELECT 1) and returns fast.
  • /health performs a richer DB query but is NOT called by BetterStack.
  • Self-ping thread is a daemon — it dies with the process cleanly.
  • All exceptions are caught; neither endpoint will ever crash the app.
  • hmac.compare_digest() used for constant-time secret comparison (no timing
    attacks on the token).
  • requests is used for HTTP calls (already in the venv transitively; we
    add it explicitly to requirements.txt).

Flask-Click command
-------------------
  flask ping-betterstack   — Manual one-shot BetterStack ping (useful in cron).
"""

from __future__ import annotations

import hmac
import logging
import os
import platform
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

# ── Blueprint ─────────────────────────────────────────────────────────────────

heartbeat_bp = Blueprint("heartbeat", __name__)

# ── In-process state (shared across threads) ─────────────────────────────────

_state: dict = {
    "start_time":        time.monotonic(),
    "last_heartbeat_at": None,   # ISO-8601 string of last successful /heartbeat hit
    "last_selfping_at":  None,   # ISO-8601 string of last successful self-ping
    "selfping_ok":       None,   # bool — last self-ping result
    "db_ok":             None,   # bool — last DB check result inside /heartbeat
}


def get_heartbeat_state() -> dict:
    """Return a snapshot of the current heartbeat state (for templates / dashboard)."""
    return dict(_state)


# ── Helper: verify secret token ──────────────────────────────────────────────

def _check_secret(required: str) -> bool:
    """
    Return True if the incoming request carries the correct secret, or if
    no secret is configured.  Uses constant-time comparison to prevent
    timing attacks.
    """
    if not required:
        return True

    provided = (
        request.headers.get("X-Heartbeat-Token")
        or request.args.get("token")
        or ""
    )
    # hmac.compare_digest requires same type; encode both
    return hmac.compare_digest(
        required.encode("utf-8"),
        provided.encode("utf-8"),
    )


# ── Helper: lightweight DB probe ─────────────────────────────────────────────

def _check_db() -> tuple[bool, str]:
    """
    Run a single cheap query (SELECT 1) to verify DB connectivity.
    Returns (ok: bool, detail: str).
    """
    try:
        from app import db
        db.session.execute(db.text("SELECT 1"))
        db.session.remove()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        logger.error("Heartbeat DB check failed: %s", exc)
        return False, str(exc)


# ── /heartbeat — primary BetterStack probe ───────────────────────────────────

@heartbeat_bp.route("/heartbeat", methods=["GET"])
@heartbeat_bp.route("/api/heartbeat", methods=["GET"])
def heartbeat():
    """
    Lightweight liveness probe.  BetterStack pings this every N seconds.

    Returns 200 when healthy, 401 on bad token, 503 on DB failure.
    Response is always JSON so monitoring tools can parse it.
    """
    # ── 1. Auth check ─────────────────────────────────────────────────────────
    required_secret: str = current_app.config.get("HEARTBEAT_SECRET", "") or ""
    if not _check_secret(required_secret):
        logger.warning(
            "Heartbeat token mismatch from %s",
            request.remote_addr,
        )
        return jsonify(status="unauthorized", timestamp=_now_iso()), 401

    # ── 2. DB probe ───────────────────────────────────────────────────────────
    db_ok, db_detail = _check_db()
    _state["db_ok"] = db_ok

    if not db_ok:
        return (
            jsonify(
                status="unhealthy",
                timestamp=_now_iso(),
                checks={"database": db_detail},
            ),
            503,
        )

    # ── 3. Record heartbeat time and return ───────────────────────────────────
    now_iso = _now_iso()
    _state["last_heartbeat_at"] = now_iso

    logger.debug("Heartbeat OK at %s", now_iso)
    return jsonify(status="healthy", timestamp=now_iso), 200




# ── /expire-tenants — batch expiry sweep ─────────────────────────────────────

@heartbeat_bp.route("/expire-tenants", methods=["POST"])
def expire_tenants():
    """
    Batch expiry sweep endpoint (v3.3).

    Iterates all Profile rows, calls enforce_expiry() on each, and returns
    a JSON summary.  Requires either:
      • HEARTBEAT_SECRET header/query param (same as /heartbeat), OR
      • A superadmin session cookie (for browser-based invocation).

    Designed to be called by:
      • Cron job:  curl -X POST /expire-tenants?token=<secret>
      • BetterStack scheduled monitor
      • Manual superadmin trigger from dashboard

    Returns 200 with {expired: N, checked: N} even if nothing changed.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    # Auth: require secret OR superadmin session
    required_secret = get_heartbeat_state().get('secret', '')
    authed_by_secret = _check_secret(required_secret)

    authed_by_session = False
    try:
        from flask_login import current_user
        authed_by_session = (
            current_user.is_authenticated and current_user.is_superadmin
        )
    except Exception:
        pass

    if required_secret and not authed_by_secret and not authed_by_session:
        return jsonify(error="Unauthorized"), 401

    try:
        from app.models.portfolio import Profile
        from app import db

        profiles = Profile.query.all()
        checked = len(profiles)
        expired_slugs = []

        for profile in profiles:
            try:
                if profile.enforce_expiry(commit=False):
                    expired_slugs.append(
                        profile.tenant.slug if profile.tenant else str(profile.id)
                    )
            except Exception as exc:
                _log.warning("expire_tenants: error on profile %s: %s", profile.id, exc)

        if expired_slugs:
            try:
                db.session.commit()
                _log.info(
                    "expire_tenants: suspended %d tenant(s): %s",
                    len(expired_slugs), ", ".join(expired_slugs),
                )
            except Exception as exc:
                db.session.rollback()
                _log.exception("expire_tenants: commit failed: %s", exc)
                return jsonify(error="DB commit failed", detail=str(exc)), 500

        return jsonify(
            status="ok",
            checked=checked,
            expired=len(expired_slugs),
            suspended=expired_slugs,
        ), 200

    except Exception as exc:
        _log.exception("expire_tenants sweep failed: %s", exc)
        return jsonify(error="Internal error", detail=str(exc)), 500


# ── /health — rich internal health report ────────────────────────────────────

def _check_tenant_db():
    """
    FIX (health-tenant-bind): the original /health check only ever queried
    db.engine (the default/core bind). In this dual-database architecture,
    Profile/Skill/Project/Testimonial/Service all live on the 'tenant' bind
    (TENANT_DATABASE_URL) -- a physically separate Postgres instance whose
    migrations are NOT applied by the single-chain `flask db upgrade` (see
    AUDIT note in migrations/env.py). A core-only health check reports
    "healthy" even when the tenant DB is completely unmigrated, which is
    exactly the failure mode that produced
    `psycopg2.errors.UndefinedTable: relation "profile" does not exist`
    in production with no advance warning. We now explicitly verify the
    tenant bind is reachable AND that its required tables exist.
    """
    from app import db

    result = {"status": "error", "detail": "not checked", "missing_tables": []}
    required_tables = ["profile", "skills", "projects", "testimonials", "services"]

    try:
        # Flask-SQLAlchemy 3.x compatible: use bind_key parameter instead of bind
        engine = db.get_engine(bind_key='tenant')
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"tenant bind unavailable: {exc}"
        return result

    try:
        with engine.connect() as conn:
            from sqlalchemy import inspect as sa_inspect
            inspector = sa_inspect(engine)
            existing = set(inspector.get_table_names())
            missing = [t for t in required_tables if t not in existing]
            result["missing_tables"] = missing
            if missing:
                result["status"] = "error"
                result["detail"] = (
                    f"tenant DB reachable but missing required tables: "
                    f"{', '.join(missing)} -- tenant-bound migrations have "
                    f"not been applied."
                )
            else:
                result["status"] = "ok"
                result["detail"] = "reachable, all required tables present"
    except Exception as exc:  # noqa: BLE001
        result["detail"] = f"connection failed: {exc}"

    return result


def _check_redis():
    """
    Report actual Redis rate-limit storage status: 'ok' (connected),
    'fallback' (degraded to memory:// after a failed pre-flight check —
    see resolve_limiter_storage_uri() in app/__init__.py), or
    'not_configured' (no REDIS_URL set at all, e.g. local dev).
    """
    configured = os.environ.get("REDIS_URL", "")
    resolved = current_app.config.get("RATELIMIT_STORAGE_URI_RESOLVED", "memory://")

    if not configured:
        return {"status": "not_configured", "detail": "REDIS_URL not set"}
    if resolved.startswith("redis"):
        return {"status": "ok", "detail": "connected"}
    return {
        "status": "fallback",
        "detail": "REDIS_URL configured but unreachable at startup; "
                   "using in-memory rate limiting (not multi-worker-safe)",
    }


@heartbeat_bp.route("/health", methods=["GET"])
def health():
    """
    Detailed health report for internal use, Kubernetes liveness/readiness
    probes, Docker HEALTHCHECK, and dashboards.

    HIGH-04 FIX: Requires Bearer token matching HEARTBEAT_SECRET, OR
    an authenticated superadmin session. Public callers get only {"status":"ok"}.
    """
    import os as _os
    from flask import request as _req, jsonify as _json
    from flask_login import current_user as _cu

    # Allow Docker HEALTHCHECK / internal callers with valid bearer token
    auth_header = _req.headers.get("Authorization", "")
    heartbeat_secret = _os.environ.get("HEARTBEAT_SECRET", "")
    bearer_valid = (
        heartbeat_secret
        and auth_header == f"Bearer {heartbeat_secret}"
    )
    superadmin_session = (
        hasattr(_cu, "is_authenticated")
        and _cu.is_authenticated
        and getattr(_cu, "is_superadmin", False)
    )

    if not bearer_valid and not superadmin_session:
        return _json({"status": "ok"}), 200

    from app import db

    uptime_seconds = time.monotonic() - _state["start_time"]

    db_ok = False
    db_version = "unknown"
    db_detail  = "not checked"

    try:
        dialect = db.engine.dialect.name
        if dialect == 'sqlite':
            result = db.session.execute(db.text("SELECT sqlite_version(), datetime('now')")).one()
            db_version = f"SQLite {result[0]}"
            db_detail  = str(result[1])
        else:
            result = db.session.execute(
                db.text("SELECT version(), NOW() AT TIME ZONE 'UTC' AS utc_now")
            ).one()
            db_version = result[0].split(" ")[0]
            db_detail  = str(result[1])
        db.session.remove()
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.error("Health DB check failed: %s", exc)
        db_detail = str(exc)

    tenant_db = _check_tenant_db()
    redis_status = _check_redis()

    overall_ok = db_ok and tenant_db["status"] == "ok"
    status_code = 200 if overall_ok else 503

    # Heartbeat state snapshot
    hb_state = get_heartbeat_state()

    payload = {
        "status":      "ok" if overall_ok else "degraded",
        "timestamp":   _now_iso(),
        "uptime_seconds": round(uptime_seconds, 1),
        "python":      platform.python_version(),
        "platform":    platform.system(),
        "environment": os.environ.get("FLASK_ENV", "production"),
        "checks": {
            "database_core": {
                "status":  "ok" if db_ok else "error",
                "version": db_version,
                "detail":  db_detail,
            },
            "database_tenant": tenant_db,
            "redis": redis_status,
        },
        "heartbeat": {
            "last_hit":        hb_state.get("last_heartbeat_at"),
            "last_selfping":   hb_state.get("last_selfping_at"),
            "selfping_ok":     hb_state.get("selfping_ok"),
        },
    }

    if not overall_ok:
        logger.error("HEALTH CHECK DEGRADED: %s", payload["checks"])

    return jsonify(payload), status_code


# ── Self-ping background thread ───────────────────────────────────────────────

class _SelfPingThread(threading.Thread):
    """
    Daemon thread that periodically:
      1. GETs /heartbeat on localhost to confirm the app is responding.
      2. Forwards a ping to the BetterStack heartbeat URL.

    Both steps are best-effort and never raise — a failure is logged but
    does not affect request handling.
    """

    def __init__(
        self,
        *,
        base_url: str,
        betterstack_url: str,
        secret: str,
        interval: int,
    ) -> None:
        super().__init__(name="heartbeat-selfping", daemon=True)
        self._base_url        = base_url.rstrip("/")
        self._betterstack_url = betterstack_url
        self._secret          = secret
        self._interval        = interval
        self._stop_event      = threading.Event()

    def run(self) -> None:  # noqa: C901
        logger.info(
            "Self-ping thread started — interval=%ds base_url=%s",
            self._interval,
            self._base_url,
        )
        # Small initial delay so the server is ready before the first ping
        self._stop_event.wait(timeout=5)

        while not self._stop_event.is_set():
            try:
                self._ping_self()
            except Exception:  # noqa: BLE001
                logger.exception("Self-ping loop error (ignored)")

            self._stop_event.wait(timeout=self._interval)

    def _ping_self(self) -> None:
        """Hit the local /heartbeat endpoint."""
        import requests  # imported lazily to avoid import-time issues

        url     = f"{self._base_url}/heartbeat"
        headers = {}
        if self._secret:
            headers["X-Heartbeat-Token"] = self._secret

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            ok   = resp.status_code == 200
            _state["last_selfping_at"] = _now_iso()
            _state["selfping_ok"]      = ok

            if ok:
                logger.info("Self-ping OK (HTTP %s)", resp.status_code)
                self._ping_betterstack()
            else:
                logger.warning(
                    "Self-ping FAILED: HTTP %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except requests.RequestException as exc:
            _state["selfping_ok"] = False
            logger.error("Self-ping request error: %s", exc)

    def _ping_betterstack(self) -> None:
        """Forward a ping to BetterStack to reset the heartbeat timer there."""
        if not self._betterstack_url:
            return

        import requests

        try:
            resp = requests.get(self._betterstack_url, timeout=10)
            if resp.status_code == 200:
                logger.debug("BetterStack heartbeat ping sent (HTTP 200)")
            else:
                logger.warning(
                    "BetterStack heartbeat ping returned HTTP %s",
                    resp.status_code,
                )
        except requests.RequestException as exc:
            logger.error("BetterStack ping failed: %s", exc)

    def stop(self) -> None:
        """Signal the thread to exit on the next interval."""
        self._stop_event.set()


# Module-level reference so we can stop the thread on app teardown
_selfping_thread: Optional[_SelfPingThread] = None


# ── Public factory ────────────────────────────────────────────────────────────

def init_heartbeat(app) -> None:
    """
    Register the heartbeat blueprint and (optionally) start the self-ping thread.

    Call this from create_app() *after* all other blueprints are registered:

        from app.heartbeat import init_heartbeat
        init_heartbeat(app)
    """
    global _selfping_thread  # noqa: PLW0603

    # Register the blueprint (exempt from CSRF — monitoring pings are GET-only)
    # Guard against double-registration: Werkzeug's stat reloader calls create_app()
    # twice in the same process (parent + child). heartbeat_bp is a module-level
    # singleton, so the second call would raise ValueError. Check first.
    from app import csrf
    csrf.exempt(heartbeat_bp)
    if 'heartbeat' not in app.blueprints:
        app.register_blueprint(heartbeat_bp)
        logger.info("Heartbeat blueprint registered (/heartbeat, /api/heartbeat, /health)")
    else:
        logger.debug("Heartbeat blueprint already registered — skipping (reloader re-import)")

    # ── Self-ping thread ──────────────────────────────────────────────────────
    self_ping_enabled = _str_to_bool(
        app.config.get("SELF_PING_ENABLED", os.environ.get("SELF_PING_ENABLED", "false"))
    )

    if not self_ping_enabled:
        logger.debug("Self-ping disabled (SELF_PING_ENABLED != true)")
        return

    betterstack_url: str = (
        app.config.get("BETTERSTACK_HEARTBEAT_URL")
        or os.environ.get("BETTERSTACK_HEARTBEAT_URL", "")
    )
    secret: str = (
        app.config.get("HEARTBEAT_SECRET")
        or os.environ.get("HEARTBEAT_SECRET", "")
        or ""
    )
    interval: int = int(
        app.config.get("SELF_PING_INTERVAL")
        or os.environ.get("SELF_PING_INTERVAL", "60")
    )
    base_url: str = (
        app.config.get("SELF_PING_BASE_URL")
        or os.environ.get("SELF_PING_BASE_URL", "")
        or _guess_base_url(app)
    )

    if not base_url:
        logger.warning(
            "Self-ping enabled but SELF_PING_BASE_URL is not set and could not "
            "be auto-detected.  Set SELF_PING_BASE_URL=http://localhost:5000."
        )
        return

    _selfping_thread = _SelfPingThread(
        base_url=base_url,
        betterstack_url=betterstack_url,
        secret=secret,
        interval=interval,
    )
    _selfping_thread.start()
    logger.info(
        "Self-ping thread started (interval=%ds, base=%s, BetterStack=%s)",
        interval,
        base_url,
        betterstack_url or "not configured",
    )

    # Stop the thread gracefully on app teardown (Gunicorn SIGTERM, etc.)
    @app.teardown_appcontext
    def _stop_selfping(_exc):  # noqa: ANN001
        pass  # Thread is a daemon — OS cleans it up; no explicit action needed.


# ── CLI command ───────────────────────────────────────────────────────────────

def register_cli(app) -> None:
    """Register the 'flask ping-betterstack' CLI command."""
    import click

    @app.cli.command("ping-betterstack")
    @click.option("--url", envvar="BETTERSTACK_HEARTBEAT_URL", help="BetterStack URL")
    def ping_betterstack_cmd(url: Optional[str]) -> None:
        """Send a one-shot ping to BetterStack heartbeat URL."""
        import requests

        target = url or os.environ.get("BETTERSTACK_HEARTBEAT_URL", "")
        if not target:
            click.echo(
                "ERROR: BETTERSTACK_HEARTBEAT_URL is not set.  "
                "Pass --url or set the env var.",
                err=True,
            )
            return

        try:
            resp = requests.get(target, timeout=10)
            if resp.status_code == 200:
                click.echo(f"✓  Ping sent to BetterStack (HTTP {resp.status_code})")
            else:
                click.echo(f"✗  Unexpected HTTP {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as exc:
            click.echo(f"✗  Request failed: {exc}", err=True)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _str_to_bool(value: str) -> bool:
    """Convert env-var string to bool."""
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _guess_base_url(app) -> str:
    """
    Try to auto-detect the app's base URL from SERVER_NAME or default to
    http://localhost:5000 in development.
    """
    server_name: str = app.config.get("SERVER_NAME", "") or ""
    if server_name:
        scheme = "https" if not app.debug else "http"
        return f"{scheme}://{server_name}"
    if app.debug:
        return "http://localhost:5000"
    return ""