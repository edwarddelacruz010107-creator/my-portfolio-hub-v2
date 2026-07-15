"""Complete signup lifecycle for PendingSignup-based registration.

This module owns the pre-verification signup model, OTP delivery, and the
atomic transition from PendingSignup -> Tenant/User/Profile/Portfolio.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Optional, cast

from app import db
from app.models import PendingSignup, Tenant, User
from app.security import log_security_event
from app.services.tenant.onboarding_service import ensure_onboarding_workspace
from app.services.auth.otp_service import generate_otp
from app.services.auth.email_policy import (
    EmailPolicyError,
    assert_public_signup_email_allowed,
    normalize_email,
)
from app.utils import log_activity
from app.utils.datetime_utils import ensure_utc_aware, utc_expiry, utc_now
from app.services.billing.trial_limits import get_trial_duration_days
from app.services.billing.trial_history import ensure_trial_subscription_record

logger = logging.getLogger(__name__)

DEFAULT_OTP_TTL_MINUTES = 3
PENDING_SIGNUP_LIFETIME_HOURS = 24
OTP_RESEND_COOLDOWN_SECONDS = 60
MAX_OTP_ATTEMPTS = 5


class PendingSignupError(ValueError):
    pass


def _hash_otp(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalized_pending_email(email: str | None) -> str:
    return normalize_email(email)


def _pending_query_for_email(email: str):
    return PendingSignup.query.filter(PendingSignup.email == _normalized_pending_email(email))


def _is_pending_expired(pending: PendingSignup) -> bool:
    return bool(getattr(pending, 'is_expired', True))


def cleanup_expired_pending_signups(*, commit: bool = True) -> int:
    """Delete expired PendingSignup rows using Python-side UTC normalization.

    SQLite often returns naive datetimes even when SQLAlchemy columns declare
    timezone=True. Loading the rows and using PendingSignup.is_expired avoids
    aware/naive SQL comparison drift and keeps old rows from crashing cleanup.
    """
    deleted = 0
    for pending in PendingSignup.query.filter_by(email_verified=False).all():
        if _is_pending_expired(pending):
            db.session.delete(pending)
            deleted += 1
    if commit and deleted:
        db.session.commit()
    elif commit:
        # keep callers that expect a clean transaction boundary deterministic
        db.session.flush()
    if deleted:
        logger.info('Cleanup: removed %d expired pending signup(s)', deleted)
    return deleted


def get_latest_pending_signup_by_email(email: str | None) -> PendingSignup | None:
    normalized = _normalized_pending_email(email)
    if not normalized:
        return None
    return (
        _pending_query_for_email(normalized)
        .filter_by(email_verified=False)
        .order_by(PendingSignup.created_at.desc(), PendingSignup.id.desc())
        .first()
    )


def cleanup_duplicate_pending_signups_for_email(
    email: str | None,
    *,
    keep_id: int | None = None,
    commit: bool = True,
) -> int:
    """Keep only one active pending signup per email.

    Old bugs could leave multiple active rows.  Keep the newest active row by
    default (or keep_id when the caller is already working with a row), delete
    expired rows, and remove older active duplicates for the same normalized
    email.  Users/tenants are never touched.
    """
    normalized = _normalized_pending_email(email)
    if not normalized:
        return 0

    rows = list(
        _pending_query_for_email(normalized)
        .filter_by(email_verified=False)
        .order_by(PendingSignup.created_at.desc(), PendingSignup.id.desc())
        .all()
    )
    if not rows:
        return 0

    active: list[PendingSignup] = []
    deleted = 0
    for row in rows:
        if _is_pending_expired(row):
            db.session.delete(row)
            deleted += 1
        else:
            active.append(row)

    keeper: PendingSignup | None = None
    if keep_id is not None:
        keeper = next((row for row in active if row.id == keep_id), None)
    if keeper is None and active:
        keeper = active[0]

    for row in active:
        if keeper is not None and row.id != keeper.id:
            db.session.delete(row)
            deleted += 1

    if commit and deleted:
        db.session.commit()
    elif commit:
        db.session.flush()
    if deleted:
        logger.info('Cleanup: removed %d duplicate/expired pending signup(s) for email=%s', deleted, normalized)
    return deleted


def get_active_pending_signup_by_email(email: str | None) -> PendingSignup | None:
    normalized = _normalized_pending_email(email)
    if not normalized:
        return None
    cleanup_duplicate_pending_signups_for_email(normalized, commit=True)
    pending = get_latest_pending_signup_by_email(normalized)
    if pending is None or pending.is_expired:
        return None
    return pending


def get_pending_signup_resend_cooldown_remaining(pending_signup: PendingSignup | None) -> int:
    """Return backend-authoritative resend cooldown remaining in seconds.

    Uses UTC normalization so SQLite naive datetimes and Postgres aware
    datetimes behave the same. This value is safe to pass to the template;
    it never exposes OTP data.
    """
    if pending_signup is None:
        return 0
    try:
        last_sent_at = ensure_utc_aware(getattr(pending_signup, 'last_otp_sent_at', None))
        if last_sent_at is None:
            return 0
        elapsed = (utc_now() - last_sent_at).total_seconds()
        return max(0, OTP_RESEND_COOLDOWN_SECONDS - int(elapsed))
    except Exception:
        logger.debug(
            'get_pending_signup_resend_cooldown_remaining: failed for pending_signup_id=%s',
            getattr(pending_signup, 'id', None),
            exc_info=True,
        )
        return 0


def get_pending_signup_otp_remaining_seconds(pending_signup: PendingSignup | None) -> int:
    """Return seconds until the current signup OTP expires."""
    if pending_signup is None:
        return 0
    try:
        expires_at = ensure_utc_aware(getattr(pending_signup, 'otp_expires_at', None))
        if expires_at is None:
            return 0
        return max(0, int((expires_at - utc_now()).total_seconds()))
    except Exception:
        logger.debug(
            'get_pending_signup_otp_remaining_seconds: failed for pending_signup_id=%s',
            getattr(pending_signup, 'id', None),
            exc_info=True,
        )
        return 0


def _resolve_signup_otp_ttl_minutes(value: int | None = None) -> int:
    if value is not None:
        return max(1, int(value))
    try:
        from app.services.auth.signup_otp_email_service import get_signup_otp_ttl_minutes

        return get_signup_otp_ttl_minutes(DEFAULT_OTP_TTL_MINUTES)
    except Exception:
        logger.debug('Could not resolve signup OTP TTL, using default', exc_info=True)
        return DEFAULT_OTP_TTL_MINUTES


def _find_active_pending_username_conflict(username: str, email: str) -> PendingSignup | None:
    username = (username or '').strip()
    normalized_email = _normalized_pending_email(email)
    if not username:
        return None
    rows = list(
        PendingSignup.query.filter(
            PendingSignup.username == username,
            PendingSignup.email_verified.is_(False),
        )
        .order_by(PendingSignup.created_at.desc(), PendingSignup.id.desc())
        .all()
    )
    for row in rows:
        if row.is_expired:
            db.session.delete(row)
            continue
        if normalize_email(row.email) != normalized_email:
            return row
    return None


def create_or_refresh_pending_signup(
    username: str,
    full_name: str,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    otp_ttl_minutes: int | None = None,
    expires_hours: int = PENDING_SIGNUP_LIFETIME_HOURS,
) -> tuple[PendingSignup, str | None, str]:
    """Create or safely refresh a pending signup.

    Returns (pending, raw_otp, action), where action is one of:
    created, refreshed, cooldown, replaced_expired.
    """
    email = normalize_email(email)
    username = (username or '').strip()
    full_name = (full_name or '').strip()

    if not username or not email or not full_name or not password:
        raise PendingSignupError('All signup fields are required.')

    if User.query.filter_by(username=username).first():
        raise PendingSignupError('That username is already taken.')
    try:
        assert_public_signup_email_allowed(email)
    except EmailPolicyError as exc:
        raise PendingSignupError(str(exc))

    now = utc_now()
    otp_ttl_minutes = _resolve_signup_otp_ttl_minutes(otp_ttl_minutes)
    had_expired_for_email = False
    for row in _pending_query_for_email(email).filter_by(email_verified=False).all():
        if row.is_expired:
            had_expired_for_email = True
            db.session.delete(row)
    username_conflict = _find_active_pending_username_conflict(username, email)
    if username_conflict is not None:
        db.session.rollback()
        raise PendingSignupError('That username is already tied to another pending signup. Please use a different username or finish that signup first.')

    db.session.flush()
    existing = get_active_pending_signup_by_email(email)

    if existing is not None:
        remaining = get_pending_signup_resend_cooldown_remaining(existing)
        if remaining > 0:
            # Re-registering with the same pending email must not bypass the
            # resend cooldown or spam a fresh OTP. Keep the current pending
            # signup intact and let the route redirect back to verification.
            logger.info(
                'Pending signup refresh blocked by cooldown email=%s id=%s remaining=%s',
                email,
                existing.id,
                remaining,
            )
            return existing, None, 'cooldown'

        raw_otp = generate_otp()
        existing.username = username
        existing.full_name = full_name
        existing.set_password(password)
        existing.ip_address = ip_address
        existing.user_agent = user_agent
        existing.email_verified = False
        existing.created_at = ensure_utc_aware(existing.created_at) or now
        existing.expires_at = utc_expiry(hours=expires_hours)
        existing.set_otp(raw_otp, otp_ttl_minutes)
        db.session.add(existing)
        db.session.commit()
        cleanup_duplicate_pending_signups_for_email(email, keep_id=existing.id, commit=True)
        logger.info('Pending signup refreshed for email=%s id=%s', email, existing.id)
        return existing, raw_otp, 'refreshed'

    raw_otp = generate_otp()

    pending = PendingSignup(
        email=email,
        username=username,
        full_name=full_name,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=now,
        expires_at=utc_expiry(hours=expires_hours),
    )
    pending.set_password(password)
    pending.set_otp(raw_otp, otp_ttl_minutes)
    db.session.add(pending)
    db.session.commit()
    action = 'replaced_expired' if had_expired_for_email else 'created'
    logger.info('Pending signup %s for email=%s id=%s', action, email, pending.id)
    return pending, raw_otp, action


def create_pending_signup(
    username: str,
    full_name: str,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    otp_ttl_minutes: int | None = None,
    expires_hours: int = PENDING_SIGNUP_LIFETIME_HOURS,
) -> tuple[PendingSignup, str]:
    """Compatibility wrapper for older callers.

    New signup routes should use create_or_refresh_pending_signup so they can
    surface created/refreshed/replaced_expired messages, but this wrapper keeps
    existing imports working.
    """
    pending, raw_otp, _action = create_or_refresh_pending_signup(
        username=username,
        full_name=full_name,
        email=email,
        password=password,
        ip_address=ip_address,
        user_agent=user_agent,
        otp_ttl_minutes=otp_ttl_minutes,
        expires_hours=expires_hours,
    )
    if raw_otp is None:
        remaining = get_pending_signup_resend_cooldown_remaining(pending)
        raise PendingSignupError(f'Please wait {remaining} seconds before requesting another code.')
    return pending, raw_otp

def issue_pending_signup_otp(
    pending_signup: PendingSignup,
    ip_address: str | None = None,
    user_agent: str | None = None,
    otp_ttl_minutes: int | None = None,
) -> str:
    if pending_signup is None:
        raise PendingSignupError('Signup session not found.')
    if pending_signup.is_expired:
        raise PendingSignupError('Signup session has expired. Please start again.')

    remaining = get_pending_signup_resend_cooldown_remaining(pending_signup)
    if remaining > 0:
        logger.info(
            'Pending signup OTP resend blocked by cooldown pending_signup_id=%s remaining=%s',
            pending_signup.id,
            remaining,
        )
        raise PendingSignupError(f'Please wait {remaining} seconds before requesting another code.')

    otp_ttl_minutes = _resolve_signup_otp_ttl_minutes(otp_ttl_minutes)
    raw_otp = generate_otp()
    pending_signup.set_otp(raw_otp, otp_ttl_minutes)
    pending_signup.ip_address = ip_address
    pending_signup.user_agent = user_agent
    db.session.add(pending_signup)
    db.session.commit()
    logger.info(
        'Pending signup OTP refreshed pending_signup_id=%s ttl_minutes=%s',
        pending_signup.id,
        otp_ttl_minutes,
    )
    return raw_otp


def resend_pending_signup_otp(
    pending_signup: PendingSignup,
    ip_address: str | None = None,
    user_agent: str | None = None,
    otp_ttl_minutes: int | None = None,
) -> bool:
    """Send a fresh pending-signup OTP without invalidating the old one first.

    The old resend flow committed the new OTP before delivery. In production,
    a transient email-provider failure meant the old code stopped working and
    the user got a cooldown even though no new email arrived. Here delivery is
    attempted first; only an accepted send replaces the stored OTP/cooldown.
    """
    if pending_signup is None:
        raise PendingSignupError('Signup session not found.')
    if pending_signup.is_expired:
        raise PendingSignupError('Signup session has expired. Please start again.')

    remaining = get_pending_signup_resend_cooldown_remaining(pending_signup)
    if remaining > 0:
        logger.info(
            'Pending signup OTP resend blocked by cooldown pending_signup_id=%s remaining=%s',
            pending_signup.id,
            remaining,
        )
        raise PendingSignupError(f'Please wait {remaining} seconds before requesting another code.')

    otp_ttl_minutes = _resolve_signup_otp_ttl_minutes(otp_ttl_minutes)
    raw_otp = generate_otp()
    if not send_pending_signup_otp(pending_signup, raw_otp):
        logger.error(
            'Pending signup OTP resend delivery failed before DB refresh pending_signup_id=%s',
            pending_signup.id,
        )
        return False

    pending_signup.set_otp(raw_otp, otp_ttl_minutes)
    pending_signup.ip_address = ip_address
    pending_signup.user_agent = user_agent
    db.session.add(pending_signup)
    db.session.commit()
    logger.info(
        'Pending signup OTP resent and refreshed pending_signup_id=%s ttl_minutes=%s',
        pending_signup.id,
        otp_ttl_minutes,
    )
    return True


def send_pending_signup_otp(pending_signup: PendingSignup, raw_otp: str) -> bool:
    try:
        from app.services.auth.signup_otp_email_service import (
            get_signup_otp_ttl_minutes,
            send_signup_verification_otp,
        )

        result = send_signup_verification_otp(
            recipient_email=pending_signup.email,
            username=pending_signup.username,
            otp=raw_otp,
            ttl_minutes=get_signup_otp_ttl_minutes(DEFAULT_OTP_TTL_MINUTES),
            context='pending_signup',
        )
        if not result.ok:
            logger.error(
                'send_pending_signup_otp: delivery failed pending_signup_id=%s provider_candidates=%s error=%s',
                pending_signup.id,
                result.provider_hint,
                result.error,
            )
        return bool(result.ok)
    except Exception:
        logger.exception('send_pending_signup_otp: delivery failed for pending_signup_id=%s', pending_signup.id)
        return False


def verify_pending_signup_otp(pending_signup: PendingSignup, raw_otp: str) -> tuple[bool, str]:
    if pending_signup is None:
        return False, 'Signup session not found.'
    if pending_signup.is_expired:
        return False, 'Signup session has expired. Please start again.'

    ok, err = pending_signup.verify_otp(raw_otp.strip(), max_attempts=MAX_OTP_ATTEMPTS)
    if not ok:
        if pending_signup.otp_attempts >= MAX_OTP_ATTEMPTS:
            db.session.add(pending_signup)
            db.session.commit()
            return False, 'Too many failed attempts. Request a new code.'
        db.session.add(pending_signup)
        db.session.commit()
        return False, err

    pending_signup.otp_hash = ''
    pending_signup.email_verified = True
    db.session.add(pending_signup)
    db.session.commit()
    return True, ''


def complete_pending_signup(
    pending_signup: PendingSignup,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    if pending_signup is None:
        raise PendingSignupError('Signup session not found.')
    if pending_signup.is_expired:
        raise PendingSignupError('Signup session has expired. Please start again.')
    if not pending_signup.email_verified:
        raise PendingSignupError('Email verification is required before creating the account.')

    username = pending_signup.username
    email = pending_signup.email
    full_name = pending_signup.full_name
    now = utc_now()

    if User.query.filter_by(username=username).first():
        raise PendingSignupError('That username has already been taken. Please start again.')
    try:
        assert_public_signup_email_allowed(email)
    except EmailPolicyError as exc:
        raise PendingSignupError(str(exc))

    from app.services.auth.registration_service import _slugify, _unique_tenant_slug

    tenant_slug = _unique_tenant_slug(full_name)
    trial_days = get_trial_duration_days()
    trial_ends = now + timedelta(days=trial_days)

    try:
        tenant = Tenant(
            slug=tenant_slug,
            company_name=full_name,
            email=email,
            contact_email=email,
            status='active',
            plan='starter',
            subscription_state='trial',
            subscription_status='trial',
            plan_name='starter',
            trial_started_at=now,
            trial_ends_at=trial_ends,
            grace_period_ends_at=trial_ends + timedelta(days=3),
        )
        db.session.add(tenant)
        db.session.flush()
        ensure_trial_subscription_record(tenant, commit=False)

        user = User(
            username=username,
            email=email,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            is_admin=True,
            is_superadmin=False,
            auth_provider='local',
            email_verified=True,
            last_login_ip=ip_address,
            last_login_user_agent=user_agent,
        )
        user.password_hash = pending_signup.password_hash
        db.session.add(user)
        db.session.flush()

        ensure_onboarding_workspace(user, display_name=full_name, commit=False)
        db.session.delete(pending_signup)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    log_activity('register', 'user', user.username,
                 f'Local signup from {ip_address}', tenant_slug=tenant.slug)
    log_security_event('signup_local', user, f'Signup from {ip_address}', 'info')
    logger.info('REGISTER: completed signup user id=%s tenant=%s with empty portfolio', user.id, tenant.slug)
    return user
