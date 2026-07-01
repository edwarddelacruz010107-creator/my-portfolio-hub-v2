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
from datetime import datetime, timezone, timedelta
from typing import Optional

from app import db
from app.models import User
from app.models.core import Tenant
from app.security import log_security_event
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
    user.email_verification_expires = datetime.now(timezone.utc) + _VERIFICATION_TTL
    return raw


def register_local_user(
    *,
    full_name: str,
    email: str,
    password: str,
    ip: str,
    user_agent: Optional[str] = None,
) -> tuple[User, str]:
    """
    Create a fresh local user + owned tenant. Returns (user, raw_verification_token).

    Never assigns superadmin. Never joins the DEFAULT tenant.
    """
    email = (email or '').strip().lower()
    full_name = (full_name or '').strip()
    if not email or '@' not in email:
        raise RegistrationError('A valid email is required.')
    if not full_name:
        raise RegistrationError('Please provide your full name.')
    if User.query.filter_by(email=email).first():
        raise RegistrationError('An account with that email already exists.')

    tenant = Tenant(
        slug=_unique_tenant_slug(full_name),
        company_name=full_name,
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
    raw_token = _issue_verification_token(user)

    db.session.add(user)
    db.session.commit()

    log_activity('register', 'user', user.username,
                 f'Local signup from {ip}', tenant_slug=tenant.slug)
    log_security_event('signup_local', user, f'Signup from {ip}', 'info')
    logger.info('REGISTER: created user id=%s tenant=%s', user.id, tenant.slug)
    return user, raw_token
