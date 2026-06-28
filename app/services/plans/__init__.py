"""
app/services/plans/__init__.py
──────────────────────────────────────────────────────────────────────────────
Plans service package — centralized plan hierarchy and entitlement system.

Canonical import surface:

    from app.services.plans import (
        # Core hierarchy functions
        has_plan_access,
        is_administrator,
        get_effective_plan,
        normalize_plan,
        # High-level services
        PlanResolver,
        EntitlementService,
        ThemeAccessService,
        QuotaService,
        # Decorator gates
        require_plan,
        require_capability,
        check_feature,
        check_plan,
        # Constants
        PLAN_TRIAL, PLAN_BASIC, PLAN_PRO,
        PLAN_BUSINESS, PLAN_ENTERPRISE, PLAN_ADMINISTRATOR,
    )
"""

from app.services.plans.plan_hierarchy import (
    has_plan_access,
    is_administrator,
    get_effective_plan,
    normalize_plan,
    get_plan_rank,
    is_at_least,
    is_paid_plan,
    PLAN_TRIAL,
    PLAN_BASIC,
    PLAN_PRO,
    PLAN_BUSINESS,
    PLAN_ENTERPRISE,
    PLAN_ADMINISTRATOR,
    PUBLIC_PLAN_KEYS,
    ASSIGNABLE_PLAN_KEYS,
)

from app.services.plans.plan_resolver import PlanResolver
from app.services.plans.entitlement_service import EntitlementService, EntitlementResult
from app.services.plans.theme_access_service import ThemeAccessService
from app.services.plans.quota_service import QuotaService
from app.services.plans.feature_gate_service import (
    require_plan,
    require_capability,
    gate_administrator_only,
    check_feature,
    check_plan,
)

__all__ = [
    # Hierarchy core
    "has_plan_access",
    "is_administrator",
    "get_effective_plan",
    "normalize_plan",
    "get_plan_rank",
    "is_at_least",
    "is_paid_plan",
    # Constants
    "PLAN_TRIAL",
    "PLAN_BASIC",
    "PLAN_PRO",
    "PLAN_BUSINESS",
    "PLAN_ENTERPRISE",
    "PLAN_ADMINISTRATOR",
    "PUBLIC_PLAN_KEYS",
    "ASSIGNABLE_PLAN_KEYS",
    # Services
    "PlanResolver",
    "EntitlementService",
    "EntitlementResult",
    "ThemeAccessService",
    "QuotaService",
    # Decorators / inline gates
    "require_plan",
    "require_capability",
    "gate_administrator_only",
    "check_feature",
    "check_plan",
]
