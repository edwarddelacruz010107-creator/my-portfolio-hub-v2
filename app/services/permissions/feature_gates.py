"""
app/services/permissions/feature_gates.py
──────────────────────────────────────────
Feature gate decorators and helpers for my-portfolio-hub v6.7.

Administrator plan tenants bypass ALL gates automatically.

Usage:

    from app.services.permissions.feature_gates import (
        require_plan,
        require_capability,
        gate_administrator,
    )

    @bp.route('/resend-config', methods=['POST'])
    @login_required
    @require_plan('Pro')
    def configure_resend():
        ...

    @bp.route('/upload', methods=['POST'])
    @login_required
    @require_capability('can_use_custom_smtp')
    def upload():
        ...
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Callable

from flask import flash, redirect, request, url_for
from flask_login import current_user

from app.services.permissions.permission_registry import (
    is_administrator_plan,
    is_at_least,
)

logger = logging.getLogger(__name__)


def _current_tenant_plan() -> str:
    """Pull the effective plan from the current request context."""
    from flask import g
    tenant = getattr(g, 'tenant', None)
    if tenant is None:
        return 'Basic'
    if callable(getattr(tenant, 'effective_plan', None)):
        return tenant.effective_plan()
    return getattr(tenant, 'plan', 'Basic') or 'Basic'


def gate_administrator(fn: Callable) -> Callable:
    """
    Decorator: allow only the Administrator plan through.
    All others receive 403.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        plan = _current_tenant_plan()
        if not is_administrator_plan(plan):
            logger.warning(
                '[FeatureGate] Administrator-only route accessed by plan=%r path=%s',
                plan, request.path,
            )
            from flask import abort
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def require_plan(minimum_plan: str) -> Callable:
    """
    Decorator factory: tenant must be at or above minimum_plan.
    Administrator always passes.

    Example:
        @require_plan('Pro')
        def my_view(): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            plan = _current_tenant_plan()
            if is_administrator_plan(plan):
                return fn(*args, **kwargs)
            if not is_at_least(plan, minimum_plan):
                flash(
                    f'This feature requires the {minimum_plan} plan or higher.',
                    'warning',
                )
                return redirect(url_for('admin.billing_plans'))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_capability(capability_attr: str, minimum_plan: str | None = None) -> Callable:
    """
    Decorator factory: check a boolean PlanCapability attribute.
    Administrator always passes.

    Example:
        @require_capability('can_use_resend', minimum_plan='Pro')
        def configure_resend(): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            plan = _current_tenant_plan()
            if is_administrator_plan(plan):
                return fn(*args, **kwargs)
            from flask import g
            tenant = getattr(g, 'tenant', None)
            if tenant is not None:
                from app.services.plan_capabilities import get_tenant_capabilities
                caps = get_tenant_capabilities(tenant)
                if not getattr(caps, capability_attr, False):
                    gate = minimum_plan or 'a higher plan'
                    flash(
                        f'This feature is not available on your current plan. '
                        f'Upgrade to {gate} to unlock it.',
                        'warning',
                    )
                    return redirect(url_for('admin.billing_plans'))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def check_plan_feature(tenant, capability_attr: str) -> bool:
    """
    Non-decorator check: True iff the tenant's plan includes the capability.
    Administrator always returns True.
    """
    plan = ''
    if callable(getattr(tenant, 'effective_plan', None)):
        plan = tenant.effective_plan()
    else:
        plan = getattr(tenant, 'plan', 'Basic') or 'Basic'

    if is_administrator_plan(plan):
        return True

    from app.services.plan_capabilities import get_tenant_capabilities
    caps = get_tenant_capabilities(tenant)
    return bool(getattr(caps, capability_attr, False))
