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
    Maintain in-process counters (upgrade to Redis for multi-worker).

Security notes
--------------
• Secret is held in server-side session only; never committed until
  a valid code is presented (atomicity fix).
• Backup codes generated once per setup session.
• valid_window=1 tolerates ±30 s clock skew (one OTP period).
• Backup codes are individually bcrypt-hashed (werkzeug scrypt).
"""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import pyotp
import qrcode
from flask import session, current_app
from werkzeug.security import generate_password_hash

if TYPE_CHECKING:
    from app.models import User

logger = logging.getLogger(__name__)

# ── In-process TOTP rate limiting ─────────────────────────────────────────────
# Replace with Redis-backed storage in multi-worker deployments.

TOTP_MAX_ATTEMPTS   = 5
TOTP_ATTEMPT_WINDOW = timedelta(minutes=10)
_totp_attempts: dict[str, dict] = {}


class TotpRateLimitError(Exception):
    """Raised when a client IP has exceeded TOTP verification attempts."""


def _get_totp_entry(ip: str) -> dict | None:
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
    now   = datetime.now(timezone.utc)
    entry = _get_totp_entry(ip) or {"count": 0, "first_attempt": now}
    entry["count"] += 1
    _totp_attempts[ip] = entry
    if entry["count"] >= TOTP_MAX_ATTEMPTS:
        logger.warning("TOTP rate-limit triggered for IP %s", ip)


def clear_totp_attempts(ip: str) -> None:
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

    # QR code — indigo fill matches admin UI palette
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
