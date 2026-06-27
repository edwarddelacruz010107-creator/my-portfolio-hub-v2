"""
app/middleware/subscription_guard.py — Subscription Lifecycle Middleware (v6.0)

Implements the SaaS subscription state machine:

    Trial → Active → Grace Period → Read-Only → Suspended

STATE MACHINE:
    ┌──────────────────────────────────────────────────────────────────────┐
    │  trial      Tenant is in free trial period. All features per caps.  │
    │  active     Paid subscription, active. Full plan access.            │
    │  grace      Subscription just expired. 7-day grace, read-only soon. │
    │  readonly   Past grace. Can login, billing, export. No writes.      │
    │  suspended  Manually suspended by superadmin.                       │
    └──────────────────────────────────────────────────────────────────────┘

READ-ONLY ALLOWS:
    GET /admin/billing/*
    GET /admin/account/*
    GET /admin/export/*
    POST /admin/billing/*  (payment submission)

READ-ONLY BLOCKS (all other POST/PUT/PATCH/DELETE):
    Uploads, project edits, page edits, publishing, email campaigns.

INTEGRATION:
    Register in app/__init__.py after blueprint registration:

        from app.middleware.subscription_guard import init_subscription_guard
        init_subscription_guard(app)

    Or use the decorator on individual routes:

        from app.middleware.subscription_guard import require_active_subscription
        @bp.route('/upload', methods=['POST'])
        @require_active_subscription
        def upload():
            ...
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    request,
    url_for,
)
from flask_login import current_user

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

GRACE_PERIOD_DAYS      = 7
READONLY_WARNING_DAYS  = 3   # warn X days before grace ends

# URL prefixes always allowed in read-only mode (GET and POST)
_READONLY_WHITELIST_PREFIXES = (
    '/admin/billing',
    '/admin/account',
    '/admin/export',
    '/admin/notifications',
    '/admin/logout',
    '/static',
    '/auth',
)

# State values that grant full write access
_WRITE_STATES = frozenset({'trial', 'active'})

# ─── State computation ────────────────────────────────────────────────────────


def compute_subscription_state(tenant) -> str:
    """
    Compute and (if changed) persist the tenant's subscription state.

    Transition rules:
      • If status == 'suspended'             → 'suspended'
      • If active subscription exists        → 'active'
      • If in trial (plan == 'Trial')        → 'trial'
      • If grace_period_ends_at in future    → 'grace'
      • If grace_period_ends_at in past      → 'readonly'
      • Otherwise (no sub, not trial)        → 'readonly'

    Returns the new state string.
    Caller is responsible for committing if state changed.
    """
    from app import db

    current_state = getattr(tenant, 'subscription_state', 'active') or 'active'

    # 1. Hard suspension (superadmin override)
    if getattr(tenant, 'status', 'active') == 'suspended':
        new_state = 'suspended'

    # 2. Active paid subscription
    elif any(s.is_active() for s in getattr(tenant, 'subscriptions', [])):
        new_state = 'active'

    # 3. Trial plan
    elif (getattr(tenant, 'plan', '') or '').lower() == 'trial':
        new_state = 'trial'

    else:
        grace_ends = getattr(tenant, 'grace_period_ends_at', None)
        now = datetime.now(timezone.utc)

        if grace_ends is None:
            # Subscription just lapsed — enter grace period
            grace_ends = now + timedelta(days=GRACE_PERIOD_DAYS)
            tenant.grace_period_ends_at = grace_ends
            new_state = 'grace'
            logger.info(
                '[SubscriptionGuard] tenant_id=%s entering grace period until %s',
                tenant.id, grace_ends.isoformat(),
            )
        elif grace_ends.replace(tzinfo=timezone.utc) > now:
            new_state = 'grace'
        else:
            new_state = 'readonly'

    if new_state != current_state:
        tenant.subscription_state = new_state
        try:
            db.session.add(tenant)
        except Exception as exc:
            logger.warning('[SubscriptionGuard] Could not stage state change: %s', exc)

    return new_state


def _is_readonly_allowed(path: str, method: str) -> bool:
    """True if this request is permitted in read-only mode."""
    # Always allow GET
    if method == 'GET':
        return True
    # Whitelisted prefixes (billing payment, export POST)
    return any(path.startswith(prefix) for prefix in _READONLY_WHITELIST_PREFIXES)


# ─── Flask integration ────────────────────────────────────────────────────────

def init_subscription_guard(app: Flask) -> None:
    """
    Register a before_request hook that enforces subscription state
    for authenticated admin routes.
    """

    @app.before_request
    def _subscription_guard():
        # Only apply to authenticated admin (non-superadmin) users
        if not current_user or not current_user.is_authenticated:
            return
        if getattr(current_user, 'is_superadmin', False):
            return
        if not request.path.startswith('/admin'):
            return

        # Lazy-load tenant to avoid N+1 in every request
        tenant = getattr(g, '_subscription_tenant', None)
        if tenant is None:
            try:
                from app.models.core import Tenant
                tenant = Tenant.query.get(current_user.tenant_id)
                g._subscription_tenant = tenant
            except Exception as exc:
                logger.warning('[SubscriptionGuard] Tenant load failed: %s', exc)
                return

        if tenant is None:
            return

        state = compute_subscription_state(tenant)
        g.subscription_state = state

        # Emit flash banners (once per request, not on XHR/API)
        is_html = 'text/html' in request.accept_mimetypes.values()
        if is_html and state == 'grace':
            grace_ends = getattr(tenant, 'grace_period_ends_at', None)
            days_left = ''
            if grace_ends:
                remaining = (grace_ends.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
                days_left = f' ({remaining} day{"s" if remaining != 1 else ""} remaining)'
            flash(
                f'⚠️ Your subscription has expired. You are in a grace period{days_left}. '
                'Please renew to avoid losing write access.',
                'warning',
            )

        elif is_html and state == 'readonly':
            flash(
                '🔒 Your account is in read-only mode. Uploads and edits are disabled. '
                'Renew your subscription to restore full access.',
                'danger',
            )

        elif is_html and state == 'suspended':
            flash(
                '🚫 Your account has been suspended. Please contact support.',
                'danger',
            )

        # Block writes in restricted states
        if state in ('readonly', 'suspended'):
            if not _is_readonly_allowed(request.path, request.method):
                logger.warning(
                    '[SubscriptionGuard] BLOCKED state=%s method=%s path=%s tenant_id=%s',
                    state, request.method, request.path, tenant.id,
                )
                if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    abort(402)  # Payment Required
                flash(
                    'This action is not available in your current subscription state. '
                    'Please renew or contact support.',
                    'danger',
                )
                return redirect(url_for('admin.billing_plans'))


# ─── Decorator for individual routes ─────────────────────────────────────────

def require_active_subscription(fn: Callable) -> Callable:
    """
    Route decorator that blocks the route when subscription is not active/trial.
    Use on write endpoints for explicit per-route control.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        state = getattr(g, 'subscription_state', 'active')
        if state not in _WRITE_STATES:
            flash(
                'This feature requires an active subscription.',
                'warning',
            )
            return redirect(url_for('admin.billing_plans'))
        return fn(*args, **kwargs)
    return wrapper


def require_capability(capability_attr: str, plan_gate: str | None = None):
    """
    Route decorator factory that checks a PlanCapability boolean attribute.

    Usage:
        @require_capability('can_use_resend')
        def resend_settings(): ...

        @require_capability('can_remove_branding', plan_gate='Pro')
        def branding_settings(): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                from app.models.core import Tenant
                from app.services.plan_capabilities import get_tenant_capabilities
                tenant = Tenant.query.get(current_user.tenant_id)
                caps   = get_tenant_capabilities(tenant)
                if not getattr(caps, capability_attr, False):
                    gate = plan_gate or 'a higher plan'
                    flash(
                        f'This feature requires {gate}. '
                        'Upgrade your subscription to access it.',
                        'warning',
                    )
                    return redirect(url_for('admin.billing_plans'))
            except Exception as exc:
                logger.warning('[require_capability] Check failed: %s', exc)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
