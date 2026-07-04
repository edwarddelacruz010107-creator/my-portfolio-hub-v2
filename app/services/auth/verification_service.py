"""
app/services/auth/verification_service.py — Email verification tokens.

Uses the same sha256-of-raw-token scheme already used by
app/services/auth/password_reset_service.py (see _hash_token in that file).
Raw token travels in the URL; only the hash lives in the DB.

Delivery: this service asks the existing communication layer for a send.
If no send function is configured in your environment, the raw URL is
logged at INFO so devs can copy it. Do NOT log tokens in production.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from flask import current_app, url_for

from app import db
from app.models import User

logger = logging.getLogger(__name__)

_TTL = timedelta(hours=24)


class VerificationError(ValueError):
    pass


# ═════════════════════════════════════════════════════════════════════════════
# OTP-based email verification (v3.10)
#
# Reuses the existing PasswordResetOTP engine (app/services/auth/otp_service.py)
# with user_type='email_verify' — same hashed-storage, 5-attempt-lockout,
# tenant-rate-limited machinery already proven for password reset. No new
# table, no migration. Additive to the token-link functions above; those are
# left in place (unused by the new flow, not deleted) so nothing that already
# depends on verify_token()/issue_verification_for() breaks.
# ═════════════════════════════════════════════════════════════════════════════

_EMAIL_VERIFY_USER_TYPE = 'email_verify'


class OTPRateLimitedError(VerificationError):
    """Raised when tenant-scoped OTP request rate limit is hit."""


def issue_email_verification_otp(
    user: User,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """
    Generate + persist a hashed 6-digit OTP for `user` and return the raw
    code (caller is responsible for emailing it and committing the session).

    Raises OTPRateLimitedError if the tenant has exceeded 5 requests/15min.
    """
    from app.services.auth.otp_service import (
        check_tenant_otp_rate_limit, create_otp_record,
    )

    allowed, reason = check_tenant_otp_rate_limit(user.tenant_id, _EMAIL_VERIFY_USER_TYPE)
    if not allowed:
        raise OTPRateLimitedError(reason)

    raw_otp = create_otp_record(
        user_type=_EMAIL_VERIFY_USER_TYPE,
        user_id=user.id,
        email=user.email,
        tenant_id=user.tenant_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.session.commit()
    return raw_otp


def verify_email_verification_otp(user: User, raw_otp: str) -> None:
    """
    Verify `raw_otp` for `user`. Raises VerificationError with a
    user-facing message on failure. On success, marks the user verified
    and commits.
    """
    from app.services.auth.otp_service import verify_otp

    ok, err = verify_otp(
        user_type=_EMAIL_VERIFY_USER_TYPE,
        user_id=user.id,
        raw_otp=raw_otp,
        tenant_id=user.tenant_id,
    )
    if not ok:
        db.session.commit()  # persist attempt-count increment / record deletion
        raise VerificationError(err)

    user.email_verified = True
    db.session.commit()


def send_email_verification_otp(user: User, raw_otp: str) -> bool:
    """Best-effort send via the tested MailerSend/SMTP provider chain."""
    from app.services.auth.otp_service import DEFAULT_TTL_MIN
    from app.models.core import GlobalEmailConfig
    from app.services.email.email_service import send_email_verification_otp as _send

    try:
        ttl = max(1, GlobalEmailConfig.get(fresh=True).otp_expiry_minutes or DEFAULT_TTL_MIN)
    except Exception:
        ttl = DEFAULT_TTL_MIN

    try:
        return _send(
            recipient_email=user.email,
            username=user.username,
            otp=raw_otp,
            ttl_minutes=ttl,
        )
    except Exception:
        logger.exception('send_email_verification_otp: dispatch failed for user_id=%s', user.id)
        return False


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_verification_for(user: User) -> str:
    raw = secrets.token_urlsafe(32)
    user.email_verification_token   = _hash(raw)
    user.email_verification_expires = datetime.now(timezone.utc) + _TTL
    db.session.commit()
    return raw


def verify_token(raw_token: str) -> User:
    if not raw_token:
        raise VerificationError('Missing verification token.')
    user = User.query.filter_by(email_verification_token=_hash(raw_token)).first()
    if not user:
        raise VerificationError('Invalid or already-used verification link.')
    exp = user.email_verification_expires
    if exp is None:
        raise VerificationError('Invalid verification link.')
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        raise VerificationError('This verification link has expired. Request a new one.')
    user.email_verified              = True
    user.email_verification_token    = None
    user.email_verification_expires  = None
    db.session.commit()
    return user


def send_verification_email(user: User, raw_token: str) -> bool:
    """
    Best-effort send. Reuses the tenant mail plumbing when available,
    otherwise logs the URL (dev fallback).
    """
    url = url_for('auth.verify_email', token=raw_token, _external=True)
    subject = 'Verify your email'
    body = (
        f'Hi {user.username},\n\n'
        f'Confirm your email to activate your account:\n\n{url}\n\n'
        'This link expires in 24 hours. If you did not sign up, ignore this email.\n'
    )
    # Try existing email service surface. Adjust the import path if your
    # environment uses a different mailer facade — this is intentionally
    # defensive so signup never crashes on a missing mailer.
    try:
        from app.services.communication import send_email  # type: ignore
        send_email(to=user.email, subject=subject, body=body)
        return True
    except Exception:
        pass
    try:
        from app.services.email import send_email  # type: ignore
        send_email(to=user.email, subject=subject, body=body)
        return True
    except Exception:
        pass

    logger.warning(
        'VERIFY EMAIL: no mailer wired up; verification URL for %s = %s',
        user.email, url,
    )
    return False
