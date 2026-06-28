"""
app/services/plans/feature_gate_service.py
──────────────────────────────────────────────────────────────────────────────
Feature Gate Service — decorator-based and inline plan gates for Flask routes.

Replaces app/services/permissions/feature_gates.py.
Retains the same decorator API surface (require_plan, require_capability)
so existing decorated routes work without modification.

KEY FIXES vs old feature_gates.py:
    • Uses PlanResolver which calls get_effective_plan() — handles both
      Tenant and Profile ORM objects uniformly.
    • Administrator bypass is evaluated via is_administrator() from
      plan_hierarchy, not an ad-hoc plan string check.
    • require_capability delegates to EntitlementService.can_access_feature()
      which uses the FEATURE_PLAN_REQUIREMENTS registry — no inline string
      comparisons.

Usage:
    from app.services.plans.feature_gate_service import (
        require_plan, require_capability, check_feature,
    )

    @bp.route('/resend-config', methods=['POST'])
    @login_required
    @require_plan('Pro')
    def configure_resend():
        ...

    # Inline check (not decorator)
    if not check_feature(profile, 'custom_smtp'):
        flash('Custom SMTP requires Basic plan or higher.', 'warning')
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Callable, Optional

from flask import abort, flash, redirect, request, url_for

from app.services.plans.plan_hierarchy import is_administrator, get_effective_plan
from app.services.plans.plan_resolver import PlanResolver
from app.services.plans.entitlement_service import EntitlementService

logger = logging.getLogger(__name__)


def _get_tenant_from_context():
    """
    Pull the active tenant / profile from the Flask request context.
    Priority: g.tenant → g.profile → g.current_profile
    """
    from flask import g
    return (
        getattr(g, "tenant", None)
        or getattr(g, "profile", None)
        or getattr(g, "current_profile", None)
    )


def require_plan(minimum_plan: str) -> Callable:
    """
    Route decorator: tenant must be at or above minimum_plan in the hierarchy.
    Administrator always passes regardless of minimum_plan.

    On failure: flashes a warning and redirects to admin.billing_plans.

    Usage:
        @require_plan('Pro')
        def my_route(): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            obj = _get_tenant_from_context()
            result = EntitlementService.check(obj, minimum_plan)
            if not result.allowed:
                logger.warning(
                    "[FeatureGate] require_plan DENIED: have=%r need=%r path=%s",
                    result.plan, minimum_plan, request.path,
                )
                flash(
                    f"This feature requires the {minimum_plan} plan or higher.",
                    "warning",
                )
                return redirect(url_for("admin.billing_plans"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_capability(capability_key: str, minimum_plan: Optional[str] = None) -> Callable:
    """
    Route decorator: tenant must have the named capability enabled.
    capability_key must exist in EntitlementService.FEATURE_PLAN_REQUIREMENTS
    (or be a PlanCapability boolean attribute name for backwards compatibility).

    Administrator always passes.

    Usage:
        @require_capability('can_use_resend', minimum_plan='Pro')
        def configure_resend(): ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            obj = _get_tenant_from_context()

            plan = get_effective_plan(obj)
            if is_administrator(plan):
                return fn(*args, **kwargs)

            # Try EntitlementService registry first (new path)
            allowed = EntitlementService.can_access_feature(obj, capability_key)

            # Backwards compat: also check PlanCapability boolean attribute
            if not allowed:
                try:
                    from app.services.plan_capabilities import get_tenant_capabilities
                    caps = get_tenant_capabilities(obj)
                    allowed = bool(getattr(caps, capability_key, False))
                except Exception:
                    pass

            if not allowed:
                gate = minimum_plan or "a higher plan"
                flash(
                    f"This feature is not available on your current plan. "
                    f"Upgrade to {gate} to unlock it.",
                    "warning",
                )
                logger.warning(
                    "[FeatureGate] require_capability DENIED: capability=%r plan=%r path=%s",
                    capability_key, plan, request.path,
                )
                return redirect(url_for("admin.billing_plans"))

            return fn(*args, **kwargs)
        return wrapper
    return decorator


def gate_administrator_only(fn: Callable) -> Callable:
    """
    Route decorator: ONLY the Administrator plan may access this route.
    All other plans receive HTTP 403.

    Usage:
        @gate_administrator_only
        def admin_internal_tool(): ...
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        obj  = _get_tenant_from_context()
        plan = get_effective_plan(obj)
        if not is_administrator(plan):
            logger.warning(
                "[FeatureGate] administrator-only route accessed by plan=%r path=%s",
                plan, request.path,
            )
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def check_feature(obj, feature_key: str) -> bool:
    """
    Inline (non-decorator) feature gate check.
    Returns True if the tenant has access, False otherwise.
    Administrator always returns True.

    Usage:
        if not check_feature(profile, 'premium_themes'):
            flash('Upgrade to Pro to access premium themes.', 'warning')
    """
    return EntitlementService.can_access_feature(obj, feature_key)


def check_plan(obj, minimum_plan: str) -> bool:
    """
    Inline plan tier check.
    Returns True if the tenant meets or exceeds minimum_plan.
    Administrator always returns True.
    """
    return PlanResolver.meets(obj, minimum_plan)
