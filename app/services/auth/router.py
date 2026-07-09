"""
app/services/auth/router.py — Central post-login redirect resolver

Provides a single `route_user_after_login(user)` function that all login
paths (password, OAuth, signup, impersonation restore) should call to
determine the canonical post-login destination. Uses `url_for()` exclusively
and falls back to safe defaults if named endpoints are unavailable.
"""
from typing import Optional
import logging

from flask import url_for, current_app
from werkzeug.routing import BuildError

logger = logging.getLogger(__name__)


def _try_url(endpoint: str, **values) -> Optional[str]:
    try:
        return url_for(endpoint, **values)
    except BuildError:
        return None


def route_user_after_login(user) -> str:
    """Return the absolute path to redirect the user to after login.

    Prioritises roles strictly:
      - superadmin -> `superadmin.dashboard`
      - tenant owner -> `studio.index` (preferred)
      - admin -> `admin.dashboard` or public administrator gateway
      - new user without tenant -> `auth.onboarding`
      - fallback -> `root`
    """
    # Superadmin -> superadmin dashboard
    if getattr(user, 'is_superadmin', False):
        dest = _try_url('superadmin.dashboard')
        if dest:
            return dest

    # Tenant owner (creator) -> /studio (preferred)
    if getattr(user, 'is_admin', False) and getattr(user, 'tenant_slug', None):
        dest = _try_url('studio.index')
        if dest:
            return dest

    # Admin (tenant-scoped admin) -> admin dashboard
    if getattr(user, 'is_admin', False):
        dest = _try_url('admin.dashboard') or _try_url('public.administrator_gateway')
        if dest:
            return dest

    # New user without tenant -> onboarding
    if not getattr(user, 'tenant_slug', None):
        dest = _try_url('auth.onboarding') or _try_url('auth.portal')
        if dest:
            return dest

    # Fallback to root
    dest = _try_url('root') or '/'
    logger.info('route_user_after_login: falling back to %s', dest)
    return dest
