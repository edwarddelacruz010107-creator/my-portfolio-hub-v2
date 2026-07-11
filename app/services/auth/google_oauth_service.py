"""
app/services/auth/google_oauth_service.py — Google account linking + provisioning.

Rules enforced here (do NOT bypass from route code):

  1. Superadmins are NEVER linked to Google in an OAuth callback.
     A superadmin who signs in with Google gets an error, not a session.

  2. If a local user already exists with the same email:
       • If it is a superadmin → refuse (rule 1).
       • Otherwise → link google_id + set email_verified=True, PRESERVE
         tenant_id, tenant_slug, is_admin, subscriptions, billing state.
     No duplicate account is ever created for the same verified email.

  3. If no user exists with that email → provision a brand-new tenant + user
     via the registration service (auth_provider='google', email_verified=True,
     avatar_url populated). This is the SaaS onboarding path.

Returns the User to log in. Never calls login_user() itself — the route does.
"""
from __future__ import annotations

import logging
import re
import secrets
from typing import Optional

from werkzeug.security import generate_password_hash

from app import db
from app.utils.datetime_utils import utc_now
from app.services.billing.trial_limits import get_trial_duration_days
from app.services.billing.trial_history import ensure_trial_subscription_record
from app.models import User
from app.models.core import Tenant
from app.security import log_security_event
from app.services.auth.email_policy import (
    EmailPolicyError,
    assert_public_signup_email_allowed,
    get_email_matches,
    is_owner_shared_email,
    normalize_email,
)
from app.utils import log_activity

logger = logging.getLogger(__name__)


class GoogleAuthError(RuntimeError):
    pass


_SLUG_RE = re.compile(r'[^a-z0-9-]')


def _slugify(s: str) -> str:
    s = _SLUG_RE.sub('-', (s or '').lower()).strip('-')
    return s[:80] or 'tenant'


def _unique_slug(hint: str) -> str:
    from app.tenant_security import RESERVED_SLUGS
    base = _slugify(hint)
    candidate = base
    n = 1
    while candidate in RESERVED_SLUGS or Tenant.query.filter_by(slug=candidate).first():
        n += 1
        candidate = f'{base}-{n}'
        if n > 500:
            return f'{base}-{secrets.token_hex(4)}'
    return candidate


def _unique_username(email: str) -> str:
    base = _SLUG_RE.sub('', email.split('@', 1)[0].lower()) or 'user'
    candidate = base
    n = 1
    while User.query.filter_by(username=candidate).first():
        n += 1
        candidate = f'{base}{n}'
    return candidate


def resolve_or_create_google_user(
    *,
    google_sub: str,
    email: str,
    email_verified: bool,
    full_name: Optional[str],
    avatar_url: Optional[str],
    ip: str,
    user_agent: Optional[str] = None,
) -> User:
    email = normalize_email(email)
    if not email:
        raise GoogleAuthError('Google did not return an email address.')
    if not email_verified:
        raise GoogleAuthError(
            'Google reports this email as unverified. '
            'Please verify it with Google, then try again.'
        )

    # 1) Already linked? Fast path.
    user = User.query.filter_by(google_id=google_sub).first()
    if user:
        if user.is_superadmin:
            log_security_event('google_login_superadmin_blocked', user,
                               f'Superadmin Google login blocked from {ip}', 'critical')
            raise GoogleAuthError('Superadmin accounts cannot sign in with Google.')
        _stamp_login(user, ip, user_agent, avatar_url)
        return user

    # 2) Existing local user by email? Link only when it maps to exactly one
    # non-superadmin account. The owner shared email is reserved and cannot be
    # used through public Google signup. Never create a duplicate Google account.
    if is_owner_shared_email(email):
        log_security_event('google_signup_owner_email_blocked', None,
                           f'Public Google signup attempted for platform-owner email from {ip}', 'warning')
        raise GoogleAuthError('This email is reserved for the platform owner. Please use a different email.')

    email_matches = get_email_matches(email)
    superadmin_matches = [u for u in email_matches if u.is_superadmin]
    if superadmin_matches:
        log_security_event('google_link_superadmin_blocked', superadmin_matches[0],
                           f'Attempted Google link to superadmin from {ip}', 'critical')
        raise GoogleAuthError('That email is bound to a superadmin account and cannot be linked to Google.')

    tenant_users = [u for u in email_matches if not u.is_superadmin]
    if len(tenant_users) == 1:
        user = tenant_users[0]
        if user.google_id and user.google_id != google_sub:
            raise GoogleAuthError('This account is already linked to a different Google identity.')
        user.google_id      = google_sub
        user.email_verified = True
        if not user.avatar_url and avatar_url:
            user.avatar_url = avatar_url
        if not user.password_hash:
            user.auth_provider = 'google'
        else:
            user.auth_provider = 'both'
        _stamp_login(user, ip, user_agent, avatar_url)
        db.session.commit()
        log_activity('link_google', 'user', user.username,
                     f'Google linked to existing account from {ip}',
                     tenant_slug=user.tenant_slug)
        log_security_event('google_linked', user, f'Google linked from {ip}', 'info')
        return user
    if len(tenant_users) > 1:
        log_security_event('google_signup_duplicate_email_blocked', None,
                           f'Google signup rejected for duplicate email {email} from {ip}', 'warning')
        raise GoogleAuthError('That email is already used by more than one account. Please sign in with your username and password.')

    try:
        assert_public_signup_email_allowed(email)
    except EmailPolicyError as exc:
        raise GoogleAuthError(str(exc))

    # 3) New user + new tenant using the same trial/profile defaults as the
    # verified manual signup path. Never assign Administrator here.
    from app.models.tenant_data import Profile
    from app.services.tenant.onboarding_service import create_default_portfolio_for

    now = utc_now()
    from datetime import timedelta
    trial_days = get_trial_duration_days()
    trial_ends = now + timedelta(days=trial_days)

    tenant = Tenant(
        slug=_unique_slug(full_name or email.split('@', 1)[0]),
        company_name=full_name or email.split('@', 1)[0],
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

    profile = Profile(
        tenant=tenant,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        name=full_name or email.split('@', 1)[0],
        email=email,
        plan='Basic',
        free_trial_days=trial_days,
        free_trial_ends=trial_ends,
        is_available=True,
    )
    db.session.add(profile)

    user = User(
        username=_unique_username(email),
        email=email,
        password_hash=generate_password_hash(secrets.token_urlsafe(64)),
        local_password_enabled=False,
        oauth_setup_required=True,
        tenant_slug=tenant.slug,
        tenant_id=tenant.id,
        is_admin=True,
        is_superadmin=False,
        auth_provider='google',
        google_id=google_sub,
        avatar_url=avatar_url,
        email_verified=True,
    )
    _stamp_login(user, ip, user_agent, avatar_url)
    db.session.add(user)
    create_default_portfolio_for(user, commit=False)
    db.session.commit()

    log_activity('register', 'user', user.username,
                 f'Google signup from {ip}', tenant_slug=tenant.slug)
    log_security_event('signup_google', user, f'Google signup from {ip}', 'info')
    logger.info('GOOGLE OAUTH: provisioned user id=%s tenant=%s', user.id, tenant.slug)
    return user


def _stamp_login(user: User, ip: str, user_agent: Optional[str], avatar_url: Optional[str]) -> None:
    user.last_login            = utc_now()
    user.last_login_ip         = ip
    user.last_login_user_agent = user_agent
    if avatar_url and not user.avatar_url:
        user.avatar_url = avatar_url
