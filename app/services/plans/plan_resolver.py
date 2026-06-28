"""
app/services/plans/plan_resolver.py
──────────────────────────────────────────────────────────────────────────────
Plan Resolver — translates any tenant-like object into a canonical plan
decision without callers needing to know whether they have a Tenant ORM,
a Profile ORM, a dict, or a plain string.

All upstream code should call:
    PlanResolver.resolve(obj)          → canonical plan string
    PlanResolver.is_administrator(obj) → bool
    PlanResolver.meets(obj, required)  → bool

Do not import plan_hierarchy directly in route handlers or services —
always go through PlanResolver so the indirection layer is preserved.
"""

from __future__ import annotations

import logging

from app.services.plans.plan_hierarchy import (
    get_effective_plan,
    has_plan_access,
    is_administrator,
    normalize_plan,
    get_plan_rank,
    PLAN_ADMINISTRATOR,
    PLAN_TRIAL,
)

logger = logging.getLogger(__name__)


class PlanResolver:
    """
    Stateless namespace for plan resolution operations.
    All methods are @staticmethod — instantiation is never needed.
    """

    @staticmethod
    def resolve(obj) -> str:
        """
        Return the canonical plan string for any tenant-like object.

        Accepts: Tenant ORM, Profile ORM, dict, plain str, or None.
        Always returns a normalized plan name (e.g. 'Pro', 'Administrator').
        """
        return get_effective_plan(obj)

    @staticmethod
    def is_administrator(obj) -> bool:
        """
        True iff the object's resolved plan is Administrator.

        Accepts: Tenant, Profile, str, dict, or None.
        """
        plan = get_effective_plan(obj)
        return is_administrator(plan)

    @staticmethod
    def meets(obj, required_plan: str) -> bool:
        """
        True iff the object's plan meets or exceeds required_plan.
        Administrator always returns True regardless of required_plan.

        Args:
            obj:           Tenant / Profile / str / dict / None
            required_plan: The minimum plan string required (e.g. 'Pro')
        """
        plan = get_effective_plan(obj)
        result = has_plan_access(plan, required_plan)
        if not result:
            logger.debug(
                "[PlanResolver] Access denied: have=%r required=%r",
                plan, required_plan,
            )
        return result

    @staticmethod
    def rank(obj) -> int:
        """Return the numeric plan rank for the object."""
        return get_plan_rank(get_effective_plan(obj))

    @staticmethod
    def entitlement_payload(obj) -> dict:
        """
        Build the standard API entitlement response payload for any tenant object.
        Used by all entitlement API endpoints to ensure consistent response shape.

        Returns:
        {
            "plan": "Administrator",
            "access_level": 999,
            "can_access": true,
            "premium_unlocked": true,
            "upgrade_required": false,
            "quota_bypass": true,
            "hide_upgrade_prompts": true,
            "is_administrator": true
        }
        """
        plan = get_effective_plan(obj)
        rank = get_plan_rank(plan)
        _is_admin = is_administrator(plan)
        _is_paid  = rank > 0

        return {
            "plan":                plan,
            "access_level":        rank,
            "can_access":          True,           # caller should gate on .meets()
            "premium_unlocked":    _is_admin or _is_paid,
            "upgrade_required":    False if _is_admin else rank == 0,
            "quota_bypass":        _is_admin,
            "hide_upgrade_prompts": _is_admin,
            "is_administrator":    _is_admin,
        }
