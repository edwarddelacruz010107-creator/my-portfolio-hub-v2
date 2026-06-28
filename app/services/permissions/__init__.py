"""
app/services/permissions/__init__.py — v6.7
─────────────────────────────────────────────────────────────────────────────
Public API for the centralized permission / plan system.

v6.7: All core plan logic now lives in app/services/plans/.
      This module re-exports everything for backwards compatibility so no
      existing import sites need to change.

      New code should import from app.services.plans directly.
"""

# ── New canonical source (app/services/plans/) ─────────────────────────────
# Import core hierarchy from new package — these are the authoritative versions.
from app.services.plans.plan_hierarchy import (          # noqa: F401
    has_plan_access,
    is_administrator,
    get_effective_plan,
    normalize_plan as _normalize_plan_new,
    get_plan_rank as plan_rank,
    is_at_least,
    PLAN_ADMINISTRATOR,
    PLAN_TRIAL,
    PLAN_BASIC,
    PLAN_PRO,
    PLAN_ENTERPRISE,
    PUBLIC_PLAN_KEYS,
)

from app.services.plans.quota_service import QuotaService   # noqa: F401
from app.services.plans.theme_access_service import ThemeAccessService  # noqa: F401
from app.services.plans.entitlement_service import EntitlementService   # noqa: F401
from app.services.plans.feature_gate_service import (       # noqa: F401
    require_plan,
    require_capability,
    gate_administrator_only,
    check_feature,
    check_plan,
)

# ── Legacy compat shims ────────────────────────────────────────────────────
# Old callers used is_administrator_plan(plan) — now is_administrator(plan).
# Both names are exported so no call site needs to change.
is_administrator_plan = is_administrator

# Old callers used normalize_plan_name — maps to normalize_plan.
normalize_plan_name = _normalize_plan_new

# Old permission_registry exports
from app.services.permissions.permission_registry import (  # noqa: F401
    is_hidden_plan,
    is_purchasable_plan,
    is_system_plan,
    public_plan_keys,
    resolve_plan,
    PUBLIC_PLANS,
    TENANT_VISIBLE_PLANS,
)

# Old QuotaResolver (still works — wraps QuotaService)
from app.services.permissions.quota_resolver import QuotaResolver  # noqa: F401

# Old gate_administrator name → gate_administrator_only
gate_administrator = gate_administrator_only

# Old check_plan_feature name → check_feature
check_plan_feature = check_feature

# Tenant lifecycle guards (unchanged)
from app.services.permissions.tenant_access import (        # noqa: F401
    ensure_administrator_plan,
    is_default_tenant,
    is_protected_tenant,
    validate_plan_change,
    validate_tenant_deletion,
    validate_tenant_suspension,
    DEFAULT_TENANT_SLUG,
)

__all__ = [
    # ── New canonical names ──────────────────────────────────────────────
    "has_plan_access",
    "is_administrator",
    "get_effective_plan",
    "is_at_least",
    "plan_rank",
    "PLAN_ADMINISTRATOR",
    "PLAN_TRIAL",
    "PLAN_BASIC",
    "PLAN_PRO",
    "PLAN_ENTERPRISE",
    "PUBLIC_PLAN_KEYS",
    "QuotaService",
    "ThemeAccessService",
    "EntitlementService",
    "require_plan",
    "require_capability",
    "gate_administrator_only",
    "check_feature",
    "check_plan",
    # ── Legacy compat names ──────────────────────────────────────────────
    "is_administrator_plan",       # → is_administrator
    "normalize_plan_name",         # → normalize_plan
    "gate_administrator",          # → gate_administrator_only
    "check_plan_feature",          # → check_feature
    "QuotaResolver",               # → QuotaService wrapper
    # ── permission_registry (still needed by some callers) ───────────────
    "is_hidden_plan",
    "is_purchasable_plan",
    "is_system_plan",
    "public_plan_keys",
    "resolve_plan",
    "PUBLIC_PLANS",
    "TENANT_VISIBLE_PLANS",
    "DEFAULT_TENANT_SLUG",
    # ── tenant lifecycle ─────────────────────────────────────────────────
    "ensure_administrator_plan",
    "is_default_tenant",
    "is_protected_tenant",
    "validate_plan_change",
    "validate_tenant_deletion",
    "validate_tenant_suspension",
]
