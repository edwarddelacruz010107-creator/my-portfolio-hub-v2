"""
app/tenant_security.py — v3.7 Multi-Tenant Security Core

SINGLE SOURCE OF TRUTH for:
  • RESERVED_SLUGS enforcement (creation, update, validation)
  • resolve_active_tenant()  — canonical tenant resolution used by ALL modules
  • session HMAC signing / validation
  • session_tenant_valid()   — validates session tenant signature
  • stamp_session_tenant()   — signs and writes tenant into session
  • TenantGuard.validate()   — full per-request tenant/session/user consistency check

Usage in blueprints (replaces all duplicated _active_tenant_slug() calls):
    from app.tenant_security import resolve_active_tenant, stamp_session_tenant, \
                                    session_tenant_valid, RESERVED_SLUGS

Logging:
    All security events use structured log_security_event() so they appear
    consistently in the audit trail.

HMAC scheme:
    sig = HMAC-SHA256(key=SECRET_KEY, msg=f"{user_id}:{tenant_slug}:{session_created_at}")
    Stored as session['_tsig']
    Validated on every authenticated admin request.
    Any mismatch → logout + clear session.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from flask import session, g, current_app, request
from flask_login import current_user, logout_user

logger = logging.getLogger(__name__)

# ── RESERVED_SLUGS — single canonical set ────────────────────────────────────
# This is the only definition in the codebase.  All modules import from here.
RESERVED_SLUGS: frozenset[str] = frozenset({
    "admin",
    "auth",
    "login",
    "logout",
    "superadmin",
    "static",
    "api",
    "default",
    "www",
    "assets",
    "dashboard",
    "health",
    "heartbeat",
    "favicon",
    "favicon.ico",
    "robots",
    "robots.txt",
    "sitemap",
    "sitemap.xml",
    "billing",
    "webhook",
    "webhooks",
    "contact",
    "setup",
    "system",
})

_DEFAULT_TENANT_SLUG = "default"


# ── Slug validation helpers ───────────────────────────────────────────────────

def is_reserved_slug(slug: str) -> bool:
    """Return True if slug is reserved and cannot be used as a tenant identifier."""
    if not slug:
        return True
    return slug.strip().lower() in RESERVED_SLUGS


def validate_slug(slug: str) -> tuple[bool, str]:
    """
    Validate a proposed tenant slug.
    Returns (is_valid, error_message).
    """
    import re
    if not slug:
        return False, "Slug is required."
    slug = slug.strip().lower()
    if is_reserved_slug(slug):
        return False, f'"{slug}" is a reserved system path and cannot be used as a tenant slug.'
    if len(slug) < 2:
        return False, "Slug must be at least 2 characters."
    if len(slug) > 80:
        return False, "Slug must be 80 characters or fewer."
    if not re.match(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$|^[a-z0-9]{2}$', slug):
        return False, "Slug must contain only lowercase letters, numbers, and hyphens, and must start/end with a letter or number."
    return True, ""


# ── HMAC session signing ──────────────────────────────────────────────────────

def _make_tsig(user_id: int, tenant_slug: str, created_at: str) -> str:
    """Compute HMAC-SHA256 session tenant signature."""
    secret = current_app.config.get("SECRET_KEY", "").encode("utf-8")
    msg = f"{user_id}:{tenant_slug}:{created_at}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def stamp_session_tenant(user_id: int, tenant_slug: str) -> None:
    """
    Write tenant_slug into session and store an HMAC signature.
    Call after every successful login and after 2FA verification.
    """
    now = datetime.now(timezone.utc).isoformat()
    sig = _make_tsig(user_id, tenant_slug, now)
    session["tenant_slug"]    = tenant_slug
    session["_tsig"]          = sig
    session["_tsig_created"]  = now
    session["_tsig_user_id"]  = user_id
    logger.info(
        "SESSION: stamped tenant=%r for user_id=%s at %s",
        tenant_slug, user_id, now,
    )


def session_tenant_valid() -> bool:
    """
    Validate the HMAC signature on session['tenant_slug'].
    Returns True if signature matches; False otherwise.
    Called by TenantGuard on every authenticated request.
    """
    if not current_user.is_authenticated:
        return True  # Not applicable for unauthenticated requests

    sig        = session.get("_tsig")
    created_at = session.get("_tsig_created")
    sig_uid    = session.get("_tsig_user_id")
    tenant     = session.get("tenant_slug")

    if not sig or not created_at or sig_uid is None or not tenant:
        # Missing signature — treat as invalid for authenticated users.
        # This handles:
        #   (a) sessions created before v3.7 (no sig yet)
        #   (b) manual session tampering
        # Re-stamp on the fly if user/tenant are consistent, else force re-auth.
        return False

    # User ID must match the signed user ID
    try:
        if int(sig_uid) != int(current_user.id):
            logger.warning(
                "SESSION SIG: user_id mismatch — session signed for uid=%s, "
                "but current_user.id=%s. Possible session fixation.",
                sig_uid, current_user.id,
            )
            return False
    except (TypeError, ValueError):
        return False

    expected = _make_tsig(int(current_user.id), tenant, created_at)
    valid = hmac.compare_digest(sig, expected)

    if not valid:
        _log_security(
            "session_sig_invalid",
            f"HMAC mismatch for user_id={current_user.id} tenant={tenant!r}",
            "critical",
        )
    return valid


# ── Canonical tenant resolution ───────────────────────────────────────────────

def resolve_active_tenant() -> str:
    """
    THE SINGLE SOURCE OF TRUTH for active tenant slug resolution.

    Resolution priority
    ───────────────────
    1. current_user.tenant_slug  — DB-authoritative for non-superadmin users.
       Always correct even after cookie-restore where session may be stale.
    2. session['tenant_slug']    — For superadmins (tenant-switching) and
       bootstrap cases where current_user is not yet loaded.
    3. g.tenant_slug             — Set by tenant_bp URL preprocessor for
       public portfolio routes.
    4. 'default'                 — Final fallback. Never returns None.

    Rules:
      • Non-superadmin users: ALWAYS return current_user.tenant_slug from DB.
        Session cannot override this. This is the core isolation guarantee.
      • Superadmin: trust session (explicit tenant-switching).
      • Session value is HMAC-validated before use (for authenticated requests).

    Returns:
        str — always a non-empty string, always 'default' as last resort.
    """
    # Priority 1: DB-authoritative for non-superadmin
    if current_user.is_authenticated and not current_user.is_superadmin:
        slug = getattr(current_user, "tenant_slug", None)
        if slug:
            # Also correct session if it drifted
            if session.get("tenant_slug") != slug:
                logger.info(
                    "TENANT: correcting session tenant %r → %r for user id=%s",
                    session.get("tenant_slug"), slug, current_user.id,
                )
                stamp_session_tenant(current_user.id, slug)
            return slug
        # Misconfigured user — fall through to session/default
        logger.warning(
            "TENANT: non-superadmin user id=%s has no tenant_slug on User model",
            current_user.id,
        )

    # Priority 2: Session (superadmin or unauthenticated bootstrap)
    slug = session.get("tenant_slug")
    if slug:
        return slug

    # Priority 3: g.tenant_slug (tenant blueprint URL preprocessor)
    slug = getattr(g, "tenant_slug", None)
    if slug:
        return slug

    # Priority 4: final defensive fallback
    return _DEFAULT_TENANT_SLUG


# ── Security event logging ────────────────────────────────────────────────────

def _get_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.remote_addr or "unknown")[:45]


def _log_security(event_type: str, description: str, severity: str = "info") -> None:
    """Structured security event log with context."""
    user_id = getattr(current_user, "id", None) if current_user.is_authenticated else None
    username = getattr(current_user, "username", "anonymous")
    tenant = resolve_active_tenant() if current_user.is_authenticated else session.get("tenant_slug", "?")

    msg = (
        f"[SECURITY:{event_type.upper()}] "
        f"user={username!r} uid={user_id} tenant={tenant!r} "
        f"ip={_get_ip()} | {description}"
    )
    if severity == "critical":
        logger.critical(msg)
    elif severity == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)


# ── TenantGuard: per-request validation middleware ────────────────────────────

class TenantGuard:
    """
    Called by app.before_request (registered in create_app).
    Performs full tenant/session/user consistency check on every request.

    Checks:
      1. If authenticated: HMAC session signature valid
      2. Non-superadmin: session tenant matches user.tenant_slug
      3. If signature invalid: logout and force re-auth

    Does NOT redirect — callers handle the response.
    Returns None if all checks pass, or a (message, severity) tuple if action needed.
    """

    @staticmethod
    def validate() -> Optional[tuple[str, str]]:
        """
        Validate current request's tenant/session integrity.
        Returns None if OK, or (issue_description, severity) if a problem was found.
        After returning non-None, caller should logout_user() and redirect.
        """
        if not current_user.is_authenticated:
            return None

        # Skip for static/health/heartbeat paths
        _SKIP = ("/static/", "/heartbeat", "/favicon", "/robots", "/health")
        try:
            if any(request.path.startswith(p) for p in _SKIP):
                return None
        except RuntimeError:
            return None

        # Skip for the 2FA verification endpoint when a pending-2FA session
        # exists.  At this point login_user() has NOT been called yet — the
        # user is authenticated only in the sense that Flask-Login restored
        # them from a previous cookie.  If the user just completed credentials
        # and is heading to verify_2fa, forcing logout here would break the
        # entire 2FA flow.
        _2fa_pending = session.get("_2fa_user_id")
        _2fa_endpoints = {"auth.verify_2fa", "tenant.auth_2fa"}
        try:
            if _2fa_pending and request.endpoint in _2fa_endpoints:
                return None
        except RuntimeError:
            pass

        # Check 1: HMAC signature
        if not session_tenant_valid():
            user_tenant = getattr(current_user, "tenant_slug", _DEFAULT_TENANT_SLUG) or _DEFAULT_TENANT_SLUG

            # If no sig exists at all (pre-v3.7 session), re-stamp rather than force logout
            if not session.get("_tsig"):
                _log_security(
                    "session_restamped",
                    f"No HMAC sig in session — restamping for user_id={current_user.id} tenant={user_tenant!r}",
                    "info",
                )
                stamp_session_tenant(current_user.id, user_tenant)
                return None

            # Signature present but invalid → force logout
            _log_security(
                "session_sig_invalid_logout",
                f"Invalid HMAC sig — forcing logout for user_id={current_user.id}",
                "critical",
            )
            return (
                "Session integrity check failed. Please sign in again.",
                "critical",
            )

        # Check 2: Non-superadmin session tenant must match DB tenant
        if not current_user.is_superadmin:
            user_tenant = getattr(current_user, "tenant_slug", None) or _DEFAULT_TENANT_SLUG
            session_tenant = session.get("tenant_slug")
            if session_tenant and session_tenant != user_tenant:
                _log_security(
                    "tenant_mismatch_corrected",
                    f"Session tenant {session_tenant!r} != user.tenant_slug {user_tenant!r} — correcting",
                    "warning",
                )
                stamp_session_tenant(current_user.id, user_tenant)

        return None
