"""
app/services/auth/registration_service.py — Local (email + password) signup.

Responsibilities:
  • Create a User row in core_db
  • Create a fresh Tenant for the new signup (SaaS onboarding)
  • Never grant admin over the DEFAULT tenant
  • Never set is_superadmin=True
  • Issue an email verification token
  • Emit audit + security log entries

This service is intentionally the ONLY code path for local signups. Do not
create users directly from route handlers.
"""
from __future__ import annotations

import re
import secrets
import hashlib
import logging
from datetime import timedelta
from typing import Optional

from app import db
from app.utils.datetime_utils import utc_now
from app.models import Profile, Tenant, User
from app.security import log_security_event
from app.services.auth.email_policy import (
    EmailPolicyError,
    assert_public_signup_email_allowed,
    normalize_email,
)
from app.utils import log_activity

logger = logging.getLogger(__name__)

_VERIFICATION_TTL = timedelta(hours=24)
_SLUG_ALPHABET     = re.compile(r'[^a-z0-9-]')


class RegistrationError(ValueError):
    """Raised when a signup cannot proceed (dup email, bad input, etc.)."""


def _slugify(base: str) -> str:
    s = _SLUG_ALPHABET.sub('-', base.lower().strip()).strip('-')
    return s[:80] or 'tenant'


def _unique_tenant_slug(base_hint: str) -> str:
    slug = _slugify(base_hint)
    # 'default' and other reserved slugs are protected at the tenant blueprint.
    from app.tenant_security import RESERVED_SLUGS
    candidate = slug
    n = 1
    while candidate in RESERVED_SLUGS or Tenant.query.filter_by(slug=candidate).first():
        n += 1
        candidate = f'{slug}-{n}'
        if n > 500:
            candidate = f'{slug}-{secrets.token_hex(4)}'
            break
    return candidate


def _unique_username(email: str) -> str:
    base = email.split('@', 1)[0].lower()
    base = _SLUG_ALPHABET.sub('', base) or 'user'
    candidate = base
    n = 1
    while User.query.filter_by(username=candidate).first():
        n += 1
        candidate = f'{base}{n}'
    return candidate


def _issue_verification_token(user: User) -> str:
    raw = secrets.token_urlsafe(32)
    user.email_verification_token   = hashlib.sha256(raw.encode()).hexdigest()
    user.email_verification_expires = utc_now() + _VERIFICATION_TTL
    return raw


def register_local_user(
    *,
    username: str,
    full_name: str,
    email: str,
    password: str,
    ip: str,
    user_agent: Optional[str] = None,
) -> User:
    """
    Create a fresh local user + owned tenant. Returns the new User.

    Never assigns superadmin. Never joins the DEFAULT tenant.
    """
    username = (username or '').strip()
    email = normalize_email(email)
    full_name = (full_name or '').strip()
    if not username:
        raise RegistrationError('Please choose a username.')
    if len(username) < 3 or len(username) > 64 or ' ' in username:
        raise RegistrationError('Username must be 3–64 characters and cannot contain spaces.')
    if User.query.filter_by(username=username).first():
        raise RegistrationError('That username is already taken.')
    if not email or '@' not in email:
        raise RegistrationError('A valid email is required.')
    if not full_name:
        raise RegistrationError('Please provide your full name.')
    try:
        assert_public_signup_email_allowed(email)
    except EmailPolicyError as exc:
        raise RegistrationError(str(exc))

    now = utc_now()
    trial_ends = now + timedelta(days=7)

    tenant = Tenant(
        slug=_unique_tenant_slug(full_name),
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

    # SuperAdmin → All Tenants lists Profile rows (app/superadmin/routes/
    # tenants.py queries profile_repository, not Tenant), not Tenant rows.
    # The superadmin's own "Create New Tenant" flow already creates a
    # Profile at creation time (see app/superadmin/routes/tenants.py), but
    # self-service signup did not — so a tenant who registered themselves
    # was invisible in SuperAdmin until they happened to visit their own
    # Admin → Profile page (which lazily creates one). Creating it here
    # closes that gap: self-service signups now show up immediately.
    profile = Profile(
        tenant=tenant,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        name=full_name,
        email=email,
        plan='Basic',
    )
    if profile.tenant_id is None:
        profile.tenant_id = tenant.id
        profile.tenant_slug = tenant.slug
    db.session.add(profile)

    user = User(
        username=username,
        email=email,
        tenant_slug=tenant.slug,
        tenant_id=tenant.id,
        is_admin=True,
        is_superadmin=False,          # HARD: signup never creates a superadmin
        auth_provider='local',
        email_verified=False,
        last_login_ip=ip,
        last_login_user_agent=user_agent,
    )
    user.password = password           # setter → password_hash
    # NOTE (v3.10): token-link verification (_issue_verification_token) is no
    # longer issued here — signup verification moved to OTP. The function
    # and the /auth/verify-email/<token> route are left in place (unused by
    # this path) rather than deleted; OTP issuance now happens in the route
    # layer (routes_signup.register()) right after this call returns, so the
    # email-send + rate-limit concerns stay out of the registration service.

    db.session.add(user)
    db.session.commit()

    log_activity('register', 'user', user.username,
                 f'Local signup from {ip}', tenant_slug=tenant.slug)
    log_security_event('signup_local', user, f'Signup from {ip}', 'info')
    logger.info('REGISTER: created user id=%s tenant=%s', user.id, tenant.slug)
    return user
