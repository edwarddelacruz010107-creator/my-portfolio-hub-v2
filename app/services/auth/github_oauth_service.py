"""
app/services/auth/github_oauth_service.py — GitHub account linking + provisioning.

Rules enforced here (do NOT bypass from route code):

  1. Superadmins are NEVER linked to GitHub in an OAuth callback.
  2. GitHub login/signup requires a verified GitHub email address.
  3. Existing local tenant users are linked by verified email only when the
     email resolves to exactly one safe non-superadmin account.
  4. New GitHub signup provisions the same 7-day trial tenant/admin flow used
     by Google signup. It never creates a superadmin or Administrator tenant.
"""
from __future__ import annotations

import logging
import re
import secrets
from typing import Optional

from app import db
from app.utils.datetime_utils import utc_now
from app.services.billing.trial_limits import get_trial_duration_days
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


class GitHubAuthError(RuntimeError):
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


def _unique_username(email: str, login: Optional[str] = None) -> str:
    source = (login or email.split('@', 1)[0] or 'user').lower()
    base = re.sub(r'[^a-z0-9_\-]', '', source) or 'user'
    base = base[:55]
    candidate = base
    n = 1
    while User.query.filter_by(username=candidate).first():
        n += 1
        candidate = f'{base}{n}'
    return candidate


def _set_provider_linked(user: User, provider: str) -> None:
    """Preserve password login while marking that OAuth login is available."""
    current = (user.auth_provider or 'local').lower()
    if current in {'local', 'both', provider}:
        user.auth_provider = provider if current == provider and not user.password_hash else 'both'
    else:
        user.auth_provider = 'both'


def resolve_or_create_github_user(
    *,
    github_id: str,
    login: Optional[str],
    email: str,
    full_name: Optional[str],
    avatar_url: Optional[str],
    ip: str,
    user_agent: Optional[str] = None,
) -> User:
    github_id = str(github_id or '').strip()
    if not github_id:
        raise GitHubAuthError('GitHub did not return a stable account identifier.')

    email = normalize_email(email)
    if not email:
        raise GitHubAuthError(
            'GitHub did not return a verified email address. Please add or verify an email in GitHub, or use email signup.'
        )

    # 1) Already linked? Fast path.
    user = User.query.filter_by(github_id=github_id).first()
    if user:
        if user.is_superadmin:
            log_security_event('github_login_superadmin_blocked', user,
                               f'Superadmin GitHub login blocked from {ip}', 'critical')
            raise GitHubAuthError('Superadmin accounts cannot sign in with GitHub.')
        _stamp_login(user, ip, user_agent, avatar_url)
        db.session.commit()
        return user

    # 2) Existing local user by verified email? Link only when safe.
    if is_owner_shared_email(email):
        log_security_event('github_signup_owner_email_blocked', None,
                           f'Public GitHub signup attempted for platform-owner email from {ip}', 'warning')
        raise GitHubAuthError('This email is reserved for the platform owner. Please use a different email.')

    email_matches = get_email_matches(email)
    superadmin_matches = [u for u in email_matches if u.is_superadmin]
    if superadmin_matches:
        log_security_event('github_link_superadmin_blocked', superadmin_matches[0],
                           f'Attempted GitHub link to superadmin from {ip}', 'critical')
        raise GitHubAuthError('That email is bound to a superadmin account and cannot be linked to GitHub.')

    tenant_users = [u for u in email_matches if not u.is_superadmin]
    if len(tenant_users) == 1:
        user = tenant_users[0]
        if user.github_id and user.github_id != github_id:
            raise GitHubAuthError('This account is already linked to a different GitHub identity.')
        user.github_id = github_id
        user.email_verified = True
        if not user.avatar_url and avatar_url:
            user.avatar_url = avatar_url
        _set_provider_linked(user, 'github')
        _stamp_login(user, ip, user_agent, avatar_url)
        db.session.commit()
        log_activity('link_github', 'user', user.username,
                     f'GitHub linked to existing account from {ip}',
                     tenant_slug=user.tenant_slug)
        log_security_event('github_linked', user, f'GitHub linked from {ip}', 'info')
        return user

    if len(tenant_users) > 1:
        log_security_event('github_signup_duplicate_email_blocked', None,
                           f'GitHub signup rejected for duplicate email {email} from {ip}', 'warning')
        raise GitHubAuthError('That email is already used by more than one account. Please sign in with your username and password.')

    try:
        assert_public_signup_email_allowed(email)
    except EmailPolicyError as exc:
        raise GitHubAuthError(str(exc))

    # 3) New user + new tenant using the same trial/profile defaults as the
    # verified manual signup path. Never assign Administrator here.
    from app.models.tenant_data import Profile
    from app.services.tenant.onboarding_service import create_default_portfolio_for

    now = utc_now()
    from datetime import timedelta
    trial_days = get_trial_duration_days()
    trial_ends = now + timedelta(days=trial_days)

    display_name = (full_name or login or email.split('@', 1)[0]).strip()
    tenant = Tenant(
        slug=_unique_slug(display_name),
        company_name=display_name,
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

    profile = Profile(
        tenant=tenant,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        name=display_name,
        email=email,
        plan='Basic',
        free_trial_days=trial_days,
        free_trial_ends=trial_ends,
        is_available=True,
    )
    db.session.add(profile)

    user = User(
        username=_unique_username(email, login),
        email=email,
        password_hash=secrets.token_urlsafe(48),  # opaque; user can set one later via reset flow
        tenant_slug=tenant.slug,
        tenant_id=tenant.id,
        is_admin=True,
        is_superadmin=False,
        auth_provider='github',
        github_id=github_id,
        avatar_url=avatar_url,
        email_verified=True,
    )
    _stamp_login(user, ip, user_agent, avatar_url)
    db.session.add(user)
    create_default_portfolio_for(user, commit=False)
    db.session.commit()

    log_activity('register', 'user', user.username,
                 f'GitHub signup from {ip}', tenant_slug=tenant.slug)
    log_security_event('signup_github', user, f'GitHub signup from {ip}', 'info')
    logger.info('GITHUB OAUTH: provisioned user id=%s tenant=%s', user.id, tenant.slug)
    return user


def _stamp_login(user: User, ip: str, user_agent: Optional[str], avatar_url: Optional[str]) -> None:
    user.last_login = utc_now()
    user.last_login_ip = ip
    user.last_login_user_agent = user_agent
    if avatar_url and not user.avatar_url:
        user.avatar_url = avatar_url
