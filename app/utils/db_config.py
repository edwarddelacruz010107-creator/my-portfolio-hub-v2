# app/utils/db_config.py
# ─────────────────────────────────────────────────────────────────────────────
# Single source of truth for database URL resolution.
# Used by: migrations/env.py, app/__init__.py, CLI scripts.
#
# DUAL-DB ARCHITECTURE (v5.0):
#   CORE_DATABASE_URL   — tenants, users, billing, subscriptions, activity logs
#   TENANT_DATABASE_URL — profile, skills, projects, testimonials, services
#
# Migration support: DIRECT_* variants use port 5432 (bypasses PgBouncer),
#   which Alembic requires for session-level SET commands.
#
# [FIX 2026-07-02] ENVIRONMENT-AWARE RESOLUTION — split dev vs prod priority.
#   Previously CORE_DATABASE_URL outranked DEV_CORE_DATABASE_URL in this file,
#   while config.py's DevelopmentConfig never even looked at CORE_DATABASE_URL
#   (it checks DEV_CORE_DATABASE_URL, else falls back to a local SQLite file).
#   Result: a local .env holding production creds (needed for deploys) caused
#   `flask db upgrade` to silently migrate PRODUCTION while the running app
#   stayed on local SQLite, untouched. In development, this module now
#   mirrors config.py's DevelopmentConfig fallback EXACTLY (same env var,
#   same SQLite path) so Alembic and the running app can never diverge again.
#   Production/staging behavior (DIRECT_* → CORE_DATABASE_URL → DATABASE_URL)
#   is unchanged.
# ─────────────────────────────────────────────────────────────────────────────

import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Project root — three levels up from app/utils/db_config.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _fix_scheme(url: str) -> str:
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def normalize_db_url(url: str) -> str:
    if not url:
        raise ValueError("Empty database URL")

    url = _fix_scheme(url)
    parsed = urlparse(url)

    port = parsed.port or (5432 if parsed.scheme.startswith("postgres") else None)
    netloc = parsed.hostname or ""

    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{parsed.hostname}"

    if port:
        netloc += f":{port}"

    rebuilt = parsed._replace(netloc=netloc)
    return urlunparse(rebuilt)


def _is_development() -> bool:
    """Same signal config.py's get_config() uses to select DevelopmentConfig."""
    return os.environ.get("FLASK_ENV", "development").strip().lower() in (
        "development", "dev", "",
    )


def _dev_sqlite_fallback(filename: str) -> str:
    """
    Local SQLite path — must stay byte-for-byte identical to the paths
    DevelopmentConfig builds in config.py (storage/<filename>), so that when
    no DEV_*_DATABASE_URL is set, Alembic and the running Flask app resolve
    to the exact same file.
    """
    db_file = _PROJECT_ROOT / "storage" / filename
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_file.resolve()}".replace("\\", "/")


def get_database_url() -> str:
    """
    Core database URL for Alembic migrations and core DB access.

    DEVELOPMENT (FLASK_ENV unset or 'development'):
      1. DEV_CORE_DATABASE_URL
      2. Local SQLite at storage/portfolio_core_dev.db (matches
         config.py's DevelopmentConfig fallback exactly)
      CORE_DATABASE_URL / DATABASE_URL are intentionally NOT consulted here —
      a production URL sitting in a local .env (kept there for convenience
      when deploying) can no longer cause a dev migration to hit production.

    PRODUCTION / STAGING (FLASK_ENV=production, etc.):
      1. DIRECT_CORE_DATABASE_URL (port-5432 direct connection, for Alembic)
      2. DIRECT_DATABASE_URL      (legacy; pre-v5.0 direct-URL name)
      3. CORE_DATABASE_URL        (production dual-db — primary)
      4. DATABASE_URL             (last-resort legacy fallback)
    """
    if _is_development():
        dev_url = os.getenv("DEV_CORE_DATABASE_URL")
        if dev_url:
            return normalize_db_url(dev_url)
        return _dev_sqlite_fallback("portfolio_core_dev.db")

    url = (
        os.getenv("DIRECT_CORE_DATABASE_URL")
        or os.getenv("DIRECT_DATABASE_URL")
        or os.getenv("CORE_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )

    if not url:
        raise RuntimeError(
            "No core database URL configured. "
            "Set CORE_DATABASE_URL (production) or DEV_CORE_DATABASE_URL (development)."
        )

    return normalize_db_url(url)


def get_tenant_database_url() -> str:
    """
    Tenant database URL (profile, skills, projects, testimonials, services).

    DEVELOPMENT (FLASK_ENV unset or 'development'):
      1. DEV_TENANT_DATABASE_URL
      2. Local SQLite at storage/portfolio_tenant_dev.db (matches
         config.py's DevelopmentConfig fallback exactly)
      TENANT_DATABASE_URL is intentionally NOT consulted here — same
      production-safety reasoning as get_database_url().

    PRODUCTION / STAGING:
      1. DIRECT_TENANT_DATABASE_URL (port-5432 direct connection, for migrations)
      2. TENANT_DATABASE_URL        (production)
      3. Falls back to core DB URL as single-DB fallback
    """
    if _is_development():
        dev_url = os.getenv("DEV_TENANT_DATABASE_URL")
        if dev_url:
            return normalize_db_url(dev_url)
        return _dev_sqlite_fallback("portfolio_tenant_dev.db")

    url = (
        os.getenv("DIRECT_TENANT_DATABASE_URL")
        or os.getenv("TENANT_DATABASE_URL")
    )

    if not url:
        # Single-DB fallback for dev/test environments without dual-DB
        return get_database_url()

    return normalize_db_url(url)
