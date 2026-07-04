"""Complete signup lifecycle for PendingSignup-based registration.

This module owns the pre-verification signup model, OTP delivery, and the
atomic transition from PendingSignup -> Tenant/User/Profile/Portfolio.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, cast

from app import db
from app.models import PendingSignup, Tenant, User
from app.security import log_security_event
from app.services.email.email_service import send_email_verification_otp as _send_verification_otp
from app.services.tenant.onboarding_service import create_default_portfolio_for
from app.services.auth.otp_service import generate_otp
from app.utils import log_activity

logger = logging.getLogger(__name__)

DEFAULT_OTP_TTL_MINUTES = 10
PENDING_SIGNUP_LIFETIME_HOURS = 24
OTP_RESEND_COOLDOWN_SECONDS = 60
MAX_OTP_ATTEMPTS = 5


class PendingSignupError(ValueError):
    pass


def _hash_otp(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_pending_signup(
    username: str,
    full_name: str,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    otp_ttl_minutes: int = DEFAULT_OTP_TTL_MINUTES,
    expires_hours: int = PENDING_SIGNUP_LIFETIME_HOURS,
) -> tuple[PendingSignup, str]:
    email = (email or '').strip().lower()
    username = (username or '').strip()
    full_name = (full_name or '').strip()

    if not username or not email or not full_name or not password:
        raise PendingSignupError('All signup fields are required.')

    if User.query.filter_by(username=username).first():
        raise PendingSignupError('That username is already taken.')
    if User.query.filter_by(email=email, is_superadmin=False).first():
        raise PendingSignupError('An account with that email already exists.')

    now = datetime.now(timezone.utc)
    existing = PendingSignup.query.filter(
        PendingSignup.email == email,
        PendingSignup.expires_at > now,
    ).first()
    if existing:
        raise PendingSignupError(
            'A signup is already pending for that email. Check your inbox or wait until it expires.'
        )

    pending = PendingSignup(
        email=email,
        username=username,
        full_name=full_name,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=now,
        expires_at=now + timedelta(hours=expires_hours),
    )
    pending.set_password(password)

    raw_otp = generate_otp()
    pending.set_otp(raw_otp, otp_ttl_minutes)
    db.session.add(pending)
    db.session.commit()
    logger.info('Pending signup created for email=%s id=%s', email, pending.id)
    return pending, raw_otp


def issue_pending_signup_otp(
    pending_signup: PendingSignup,
    ip_address: str | None = None,
    user_agent: str | None = None,
    otp_ttl_minutes: int = DEFAULT_OTP_TTL_MINUTES,
) -> str:
    if pending_signup is None:
        raise PendingSignupError('Signup session not found.')
    if pending_signup.is_expired:
        raise PendingSignupError('Signup session has expired. Please start again.')

    now = datetime.now(timezone.utc)
    if pending_signup.last_otp_sent_at and (now - pending_signup.last_otp_sent_at).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
        remaining = OTP_RESEND_COOLDOWN_SECONDS - int((now - pending_signup.last_otp_sent_at).total_seconds())
        raise PendingSignupError(f'Please wait {remaining} seconds before requesting another code.')

    raw_otp = generate_otp()
    pending_signup.set_otp(raw_otp, otp_ttl_minutes)
    pending_signup.ip_address = ip_address
    pending_signup.user_agent = user_agent
    db.session.add(pending_signup)
    db.session.commit()
    return raw_otp


def send_pending_signup_otp(pending_signup: PendingSignup, raw_otp: str) -> bool:
    try:
        return _send_verification_otp(
            recipient_email=pending_signup.email,
            username=pending_signup.username,
            otp=raw_otp,
        )
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
    now = datetime.now(timezone.utc)

    if User.query.filter_by(username=username).first():
        raise PendingSignupError('That username has already been taken. Please start again.')
    if User.query.filter_by(email=email, is_superadmin=False).first():
        raise PendingSignupError('An account with that email already exists. Please sign in instead.')

    from app.services.auth.registration_service import _slugify, _unique_tenant_slug

    tenant_slug = _unique_tenant_slug(full_name)
    trial_ends = now + timedelta(days=7)

    with db.session.begin():
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

        create_default_portfolio_for(user, commit=False)
        db.session.delete(pending_signup)

    log_activity('register', 'user', user.username,
                 f'Local signup from {ip_address}', tenant_slug=tenant.slug)
    log_security_event('signup_local', user, f'Signup from {ip_address}', 'info')
    logger.info('REGISTER: completed signup user id=%s tenant=%s', user.id, tenant.slug)
    return user


def cleanup_expired_pending_signups() -> int:
    now = datetime.now(timezone.utc)
    deleted = cast(int, PendingSignup.query.filter(PendingSignup.expires_at < now).delete(synchronize_session=False))
    db.session.commit()
    logger.info('Cleanup: removed %d expired pending signup(s)', deleted)
    return deleted
