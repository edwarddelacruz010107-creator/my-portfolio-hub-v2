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
from datetime import datetime, timezone
from typing import Optional

from app import db
from app.models import User
from app.models.core import Tenant
from app.security import log_security_event
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
    email = (email or '').strip().lower()
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

    # 2) Existing local user by email? Link (unless superadmin).
    user = User.query.filter_by(email=email).first()
    if user:
        if user.is_superadmin:
            log_security_event('google_link_superadmin_blocked', user,
                               f'Attempted Google link to superadmin from {ip}', 'critical')
            raise GoogleAuthError('That email is bound to a superadmin account and cannot be linked to Google.')
        user.google_id      = google_sub
        user.email_verified = True
        if not user.avatar_url and avatar_url:
            user.avatar_url = avatar_url
        # keep auth_provider='local' — the user still has a password.
        # If they had no password, mark provider='google'.
        if not user.password_hash:
            user.auth_provider = 'google'
        _stamp_login(user, ip, user_agent, avatar_url)
        db.session.commit()
        log_activity('link_google', 'user', user.username,
                     f'Google linked to existing account from {ip}',
                     tenant_slug=user.tenant_slug)
        log_security_event('google_linked', user, f'Google linked from {ip}', 'info')
        return user

    # 3) New user + new tenant.
    tenant = Tenant(
        slug=_unique_slug(full_name or email.split('@', 1)[0]),
        company_name=full_name or email.split('@', 1)[0],
        email=email,
        contact_email=email,
        status='active',
        plan='Basic',
    )
    db.session.add(tenant)
    db.session.flush()

    user = User(
        username=_unique_username(email),
        email=email,
        password_hash=secrets.token_urlsafe(48),  # opaque; user can set one later via reset flow
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
    db.session.commit()

    log_activity('register', 'user', user.username,
                 f'Google signup from {ip}', tenant_slug=tenant.slug)
    log_security_event('signup_google', user, f'Google signup from {ip}', 'info')
    logger.info('GOOGLE OAUTH: provisioned user id=%s tenant=%s', user.id, tenant.slug)
    return user


def _stamp_login(user: User, ip: str, user_agent: Optional[str], avatar_url: Optional[str]) -> None:
    user.last_login            = datetime.now(timezone.utc)
    user.last_login_ip         = ip
    user.last_login_user_agent = user_agent
    if avatar_url and not user.avatar_url:
        user.avatar_url = avatar_url
