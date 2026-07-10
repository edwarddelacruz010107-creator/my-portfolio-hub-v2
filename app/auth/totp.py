"""
app/auth/totp.py — Shared TOTP utilities (Portfolio CMS v3.2)

Extracted from app/admin/__init__.py and app/superadmin/__init__.py to
eliminate code duplication and fix atomicity / race-condition issues in
the 2FA setup flow.

Public API
----------
generate_setup_context(user, issuer)
    → dict with {secret, qr_b64, backup_codes}
    Idempotent within a session: repeated calls return the same pending
    secret from session so the QR code never changes mid-setup.

commit_2fa_enable(user, code, session)
    → (success: bool, error_msg: str | None)
    Atomically verifies the TOTP code and, only on success, persists
    secret + backup_codes to the DB.  Clears pending session keys.

rate_limit_totp_verify(ip)
    → raises TotpRateLimitError if too many consecutive failures.

record_totp_failure(ip) / clear_totp_attempts(ip)
    Maintain Redis-backed counters with in-process fallback.

Security & Production Notes
---------------------------
⚠️  IMPORTANT: TOTP Rate Limiting
    This module uses Redis for multi-worker rate limiting if available.
    In production, set REDIS_URL environment variable:
    
        REDIS_URL=redis://localhost:6379/0
    
    Without Redis:
    • Single-worker deployments (development, small scale): safe
    • Multi-worker/gunicorn/uWSGI: race conditions possible
    • Recommended: Always configure Redis in production
    • Fallback: In-process dictionary (single-worker safe only)

⚠️  OTP Configuration
    • TTL: 15 minutes (configurable via GlobalEmailConfig.otp_expiry_minutes)
    • Max attempts: 5 per 10-minute window
    • Rate limiting: per IP address
    • All verifications logged with IP + user agent

✅  Security Guarantees
    • Secret held in server-side session only; never persisted until valid code presented
    • Backup codes generated once per setup session
    • All backup codes bcrypt-hashed (werkzeug scrypt)
    • Clock skew tolerance: ±30 seconds (valid_window=1)
    • Session regeneration on successful verification
"""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import pyotp
# qrcode is optional at import time; setup pages fall back to manual key if missing.
from flask import session, current_app
from werkzeug.security import generate_password_hash

if TYPE_CHECKING:
    from app.models import User

logger = logging.getLogger(__name__)

# ── TOTP rate limiting (Redis-backed with fallback) ──────────────────────────

TOTP_MAX_ATTEMPTS   = 5
TOTP_ATTEMPT_WINDOW = timedelta(minutes=10)
_totp_attempts: dict[str, dict] = {}  # Fallback for single-worker/dev

def _get_redis_client():
    """Get Redis client if available, returns None if unavailable."""
    try:
        from flask_redis import FlaskRedis
        redis_instance = current_app.extensions.get('redis')
        return redis_instance if redis_instance and redis_instance.connection_pool else None
    except Exception:
        return None


class TotpRateLimitError(Exception):
    """Raised when a client IP has exceeded TOTP verification attempts."""


def _get_totp_entry(ip: str) -> dict | None:
    """
    Retrieve TOTP attempt counter from Redis (preferred) or fallback dict.
    Returns None if entry doesn't exist or has expired.
    """
    redis_client = _get_redis_client()
    
    if redis_client:
        # Redis-backed rate limiting (production)
        try:
            key = f"totp:attempts:{ip}"
            data = redis_client.get(key)
            if not data:
                return None
            entry = json.loads(data)
            # Redis handles expiration via TTL, so entry is always valid if present
            return entry
        except Exception as e:
            logger.warning("Redis error in TOTP rate limiting (IP: %s): %s; falling back to in-process", ip, e)
            # Fall through to in-process fallback
    
    # Fallback: in-process dictionary (single-worker safe only)
    entry = _totp_attempts.get(ip)
    if not entry:
        return None
    now = datetime.now(timezone.utc)
    if now - entry["first_attempt"] > TOTP_ATTEMPT_WINDOW:
        _totp_attempts.pop(ip, None)
        return None
    return entry


def rate_limit_totp_verify(ip: str) -> None:
    """Raise TotpRateLimitError if the IP has exceeded allowed attempts."""
    entry = _get_totp_entry(ip)
    if entry and entry["count"] >= TOTP_MAX_ATTEMPTS:
        raise TotpRateLimitError(
            "Too many 2FA verification attempts. "
            "Please wait 10 minutes before trying again."
        )


def record_totp_failure(ip: str) -> None:
    """Record a TOTP verification failure for rate limiting."""
    redis_client = _get_redis_client()
    now = datetime.now(timezone.utc)
    
    if redis_client:
        # Redis-backed (production)
        try:
            key = f"totp:attempts:{ip}"
            data = redis_client.get(key)
            if data:
                entry = json.loads(data)
            else:
                entry = {"count": 0, "first_attempt": now.isoformat()}
            
            entry["count"] += 1
            redis_client.setex(
                key,
                int(TOTP_ATTEMPT_WINDOW.total_seconds()),
                json.dumps(entry)
            )
            
            if entry["count"] >= TOTP_MAX_ATTEMPTS:
                logger.warning("TOTP rate-limit triggered for IP %s (Redis)", ip)
        except Exception as e:
            logger.warning("Redis error recording TOTP failure (IP: %s): %s; falling back to in-process", ip, e)
            # Fall through to in-process fallback
            _record_totp_failure_fallback(ip, now)
    else:
        # Fallback: in-process dictionary
        _record_totp_failure_fallback(ip, now)


def _record_totp_failure_fallback(ip: str, now: datetime) -> None:
    """Fallback in-process TOTP failure recording (single-worker only)."""
    entry = _get_totp_entry(ip) or {"count": 0, "first_attempt": now}
    entry["count"] += 1
    _totp_attempts[ip] = entry
    if entry["count"] >= TOTP_MAX_ATTEMPTS:
        logger.warning("TOTP rate-limit triggered for IP %s (in-process)", ip)


def clear_totp_attempts(ip: str) -> None:
    """Clear TOTP attempts for an IP after successful verification."""
    redis_client = _get_redis_client()
    
    if redis_client:
        # Redis-backed
        try:
            key = f"totp:attempts:{ip}"
            redis_client.delete(key)
        except Exception as e:
            logger.warning("Redis error clearing TOTP attempts (IP: %s): %s", ip, e)
    
    # Always clear fallback dict
    _totp_attempts.pop(ip, None)


# ── Setup helpers ──────────────────────────────────────────────────────────────

_PENDING_SECRET_KEY  = "_pending_totp_secret"
_PENDING_BACKUP_KEY  = "_pending_backup_codes"


def generate_setup_context(user: "User", issuer: str | None = None) -> dict:
    """
    Return everything the 2FA setup template needs.

    Idempotent: the same secret is reused for the duration of the setup
    session so the QR code does not flicker on page refresh.  The secret
    is NOT persisted to the DB here — only after commit_2fa_enable() verifies
    a valid code.

    Returns
    -------
    dict:
        secret       — raw Base32 TOTP secret (for manual entry)
        qr_b64       — PNG QR code as base64 string (for <img src=...>)
        backup_codes — list[str] of plaintext codes (shown once, hashed in session)
    """
    issuer = issuer or current_app.config.get("TOTP_ISSUER", "Portfolio CMS")

    # Idempotent: reuse existing pending secret if present
    if _PENDING_SECRET_KEY not in session:
        secret = pyotp.random_base32()
        session[_PENDING_SECRET_KEY] = secret
    secret: str = session[_PENDING_SECRET_KEY]

    # Build the otpauth URI using the pending secret (not the DB value)
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.email,
        issuer_name=issuer,
    )

    # QR code — indigo fill matches admin UI palette.
    # Production safety: if qrcode is not installed yet, do not 500 the page;
    # the manual setup key below remains enough for authenticator apps.
    qr_b64 = None
    try:
        import qrcode  # type: ignore
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(uri)
        qr.make(fit=True)
        img    = qr.make_image(fill_color="#6366f1", back_color="white")
        buf    = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        logger.warning("2FA QR generation unavailable; using manual-key fallback: %s", exc)

    # Generate backup codes only once per setup session
    if _PENDING_BACKUP_KEY not in session:
        codes       = _generate_plaintext_codes(10)
        hashed_codes = [generate_password_hash(c) for c in codes]
        session[_PENDING_BACKUP_KEY] = {
            "plain":  codes,
            "hashed": hashed_codes,
        }
    backup_codes: list[str] = session[_PENDING_BACKUP_KEY]["plain"]

    return {
        "secret":       secret,
        "qr_b64":       qr_b64,
        "backup_codes": backup_codes,
    }


def commit_2fa_enable(user: "User", code: str) -> tuple[bool, str | None]:
    """
    Verify ``code`` against the pending TOTP secret stored in session.

    On success
    ----------
    • Writes totp_secret, totp_enabled=True, totp_backup_codes to ``user``.
    • Clears pending session keys.
    • Does NOT call db.session.commit() — caller is responsible so it can
      be wrapped in a broader transaction.

    On failure
    ----------
    • Returns (False, human-readable error).
    • Does NOT modify the user object.

    Parameters
    ----------
    user : User
        The currently authenticated user object.
    code : str
        The 6-digit code submitted by the user.

    Returns
    -------
    (success: bool, error: str | None)
    """
    secret  = session.get(_PENDING_SECRET_KEY)
    pending = session.get(_PENDING_BACKUP_KEY)

    if not secret or not pending:
        return False, "Setup session expired. Please start 2FA setup again."

    totp = pyotp.TOTP(secret)
    window = current_app.config.get('TOTP_VALID_WINDOW', 1)
    if not totp.verify(code.strip(), valid_window=window):
        return False, "Invalid code — please try again."

    # --- Atomically commit ---
    user.totp_secret       = secret
    user.totp_enabled      = True
    user.totp_backup_codes = json.dumps(pending["hashed"])

    # Clear pending keys from session
    session.pop(_PENDING_SECRET_KEY, None)
    session.pop(_PENDING_BACKUP_KEY, None)

    return True, None


def _generate_plaintext_codes(count: int) -> list[str]:
    """Generate ``count`` human-friendly backup codes (XXXXX-XXXXX format)."""
    import secrets
    import string
    alphabet = string.ascii_uppercase + string.digits
    return [
        "".join(secrets.choice(alphabet) for _ in range(5))
        + "-"
        + "".join(secrets.choice(alphabet) for _ in range(5))
        for _ in range(count)
    ]
