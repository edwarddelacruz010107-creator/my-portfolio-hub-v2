"""
app/services/otp_service.py — OTP lifecycle management (v3.8)

Responsibilities:
  • Generate cryptographically secure 6-digit OTPs
  • Persist hashed OTP to PasswordResetOTP table
  • Verify submitted OTP with attempt-count enforcement
  • Invalidate / purge OTPs after use or expiry

Security properties:
  • Raw OTP never stored — SHA-256 hash only
  • Max 5 wrong attempts before OTP is voided
  • Expiry enforced in DB (expires_at) and at verify time
  • Tenant isolation enforced for user_type='tenant'
"""
import logging
import secrets
import string
from datetime import datetime, timezone, timedelta

from app import db
from app.models.portfolio import PasswordResetOTP, GlobalEmailConfig

logger = logging.getLogger(__name__)

OTP_LENGTH       = 6
MAX_ATTEMPTS     = 5
DEFAULT_TTL_MIN  = 10


def _get_ttl() -> int:
    """Return OTP TTL in minutes from GlobalEmailConfig, falling back to default."""
    try:
        cfg = GlobalEmailConfig.get()
        return max(1, cfg.otp_expiry_minutes or DEFAULT_TTL_MIN)
    except Exception:
        return DEFAULT_TTL_MIN


def generate_otp() -> str:
    """Return a cryptographically secure 6-digit numeric OTP."""
    return ''.join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


def create_otp_record(
    user_type: str,
    user_id: int,
    email: str,
    tenant_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """
    Purge previous OTPs for this user, generate a new OTP,
    persist a hashed record, and return the *raw* OTP for emailing.

    Caller must commit the session after this call.
    """
    # Purge old records first (one active OTP per user at a time)
    PasswordResetOTP.purge_old(user_type, user_id)

    raw_otp = generate_otp()
    ttl     = _get_ttl()

    record = PasswordResetOTP(
        user_type  = user_type,
        user_id    = user_id,
        tenant_id  = tenant_id,
        email      = email,
        otp_hash   = PasswordResetOTP.hash_otp(raw_otp),
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl),
        ip_address = ip_address,
        user_agent = user_agent,
    )
    db.session.add(record)
    logger.info(
        'OTP created: type=%s user_id=%s tenant_id=%s ip=%s ttl=%dm',
        user_type, user_id, tenant_id, ip_address, ttl,
    )
    return raw_otp


def verify_otp(
    user_type: str,
    user_id: int,
    raw_otp: str,
    tenant_id: int | None = None,
) -> tuple[bool, str]:
    """
    Verify the submitted OTP.

    Returns (True, '') on success.
    Returns (False, reason) on failure.

    On success: marks the record `used=True` (caller must commit).
    On too many attempts: deletes the record.

    Tenant isolation: if tenant_id is supplied the record must match.
    """
    query = PasswordResetOTP.query.filter_by(
        user_type=user_type,
        user_id=user_id,
        used=False,
    )
    if tenant_id is not None:
        query = query.filter_by(tenant_id=tenant_id)

    record = query.order_by(PasswordResetOTP.created_at.desc()).first()

    if not record:
        return False, 'No active OTP found. Please request a new one.'

    if record.is_expired:
        db.session.delete(record)
        return False, 'OTP has expired. Please request a new one.'

    record.attempts += 1

    if record.attempts > MAX_ATTEMPTS:
        db.session.delete(record)
        logger.warning(
            'OTP brute-force: type=%s user_id=%s tenant_id=%s',
            user_type, user_id, tenant_id,
        )
        return False, 'Too many failed attempts. Request a new OTP.'

    if not record.verify(raw_otp.strip()):
        remaining = MAX_ATTEMPTS - record.attempts
        return False, f'Incorrect OTP. {remaining} attempt(s) remaining.'

    record.used = True
    logger.info('OTP verified: type=%s user_id=%s', user_type, user_id)
    return True, ''


def cleanup_expired_otps() -> int:
    """Delete all expired OTP records. Intended for periodic cleanup job."""
    now = datetime.now(timezone.utc)
    deleted = PasswordResetOTP.query.filter(
        PasswordResetOTP.expires_at < now
    ).delete(synchronize_session=False)
    db.session.commit()
    logger.info('OTP cleanup: removed %d expired records', deleted)
    return deleted
