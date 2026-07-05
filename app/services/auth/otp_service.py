"""
app/services/otp_service.py — OTP lifecycle management (v3.9)

Responsibilities:
  • Generate cryptographically secure 6-digit OTPs
  • Persist hashed OTP to PasswordResetOTP table
  • Verify submitted OTP with attempt-count enforcement
  • Invalidate / purge OTPs after use or expiry

Security properties:
  • Raw OTP never stored — SHA-256 hash only
  • Max 5 wrong attempts before OTP is voided
  • Expiry enforced in DB (expires_at) and at verify time
  • Tenant isolation enforced for user_type='tenant'/'admin'

v3.9 CHANGES — Tenant-aware OTP rate limiting:
  • check_tenant_otp_rate_limit(tenant_id, user_type) — enforces
    5 OTP requests per 15 minutes per tenant, independently of the
    global Flask-Limiter IP-based limit at the route layer.
  • Rate limit state is kept in an in-process cache (dict + timestamps).
    For multi-worker deployments the existing Redis-backed Flask-Limiter
    at the route layer remains the authoritative cross-worker limit;
    this layer adds a fast in-process guard for single-worker and a
    defence-in-depth layer for multi-worker.
  • Rate limit state is never persisted to DB — it resets on worker
    restart, which is acceptable (short TTL).
"""
import logging
import secrets
import string
import time
from collections import defaultdict
from datetime import timedelta

from app import db
from app.utils.datetime_utils import utc_expiry, utc_now
from app.models.core import PasswordResetOTP, GlobalEmailConfig

logger = logging.getLogger(__name__)

OTP_LENGTH       = 6
MAX_ATTEMPTS     = 5
DEFAULT_TTL_MIN  = 10

# Tenant-aware rate limiting config
_TENANT_OTP_LIMIT   = 5    # max OTP requests per window
_TENANT_OTP_WINDOW  = 900  # 15 minutes in seconds

# In-process rate limit store: {(tenant_id, user_type): [timestamp, ...]}
_tenant_otp_timestamps: dict = defaultdict(list)


def _get_ttl() -> int:
    """Return OTP TTL in minutes from GlobalEmailConfig, falling back to default."""
    try:
        cfg = GlobalEmailConfig.get(fresh=True)
        return max(1, cfg.otp_expiry_minutes or DEFAULT_TTL_MIN)
    except Exception:
        return DEFAULT_TTL_MIN


def generate_otp() -> str:
    """Return a cryptographically secure 6-digit numeric OTP."""
    return ''.join(secrets.choice(string.digits) for _ in range(OTP_LENGTH))


def check_tenant_otp_rate_limit(
    tenant_id: int | None,
    user_type: str,
) -> tuple[bool, str]:
    """
    Tenant-aware OTP rate limit: max _TENANT_OTP_LIMIT requests per
    _TENANT_OTP_WINDOW seconds per (tenant_id, user_type) pair.

    Superadmin (tenant_id=None) is never rate-limited here — its limit
    is enforced at the route layer by Flask-Limiter.

    Returns (allowed: bool, reason: str).
      allowed=True  → request should proceed
      allowed=False → request should be rejected with generic message
    """
    if tenant_id is None:
        return True, ''

    key = (tenant_id, user_type)
    now = time.monotonic()
    window_start = now - _TENANT_OTP_WINDOW

    # Evict timestamps older than the window
    _tenant_otp_timestamps[key] = [
        ts for ts in _tenant_otp_timestamps[key] if ts > window_start
    ]

    count = len(_tenant_otp_timestamps[key])
    if count >= _TENANT_OTP_LIMIT:
        logger.warning(
            '[TenantOTPRateLimit] tenant_id=%s user_type=%s requests=%d limit=%d window=%ds — BLOCKED',
            tenant_id, user_type, count, _TENANT_OTP_LIMIT, _TENANT_OTP_WINDOW,
        )
        return False, (
            f'Too many OTP requests for this account. '
            f'Please wait before trying again.'
        )

    _tenant_otp_timestamps[key].append(now)
    logger.debug(
        '[TenantOTPRateLimit] tenant_id=%s user_type=%s requests=%d/%d allowed',
        tenant_id, user_type, count + 1, _TENANT_OTP_LIMIT,
    )
    return True, ''


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
        expires_at = utc_expiry(minutes=ttl),
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
    This prevents cross-tenant OTP collisions and reset hijacking.
    """
    query = PasswordResetOTP.query.filter_by(
        user_type=user_type,
        user_id=user_id,
        used=False,
    )
    if tenant_id is not None:
        # SECURITY: always scope by tenant to prevent cross-tenant OTP reuse
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
    logger.info('OTP verified: type=%s user_id=%s tenant_id=%s', user_type, user_id, tenant_id)
    return True, ''


def cleanup_expired_otps() -> int:
    """Delete all expired OTP records. Intended for periodic cleanup job."""
    now = utc_now()
    deleted = PasswordResetOTP.query.filter(
        PasswordResetOTP.expires_at < now
    ).delete(synchronize_session=False)
    db.session.commit()
    logger.info('OTP cleanup: removed %d expired records', deleted)
    return deleted
