from __future__ import annotations

from app.models.core import get_plan_features, normalize_plan_name
from app.system_plan import has_administrator_access


class PlanService:
    def normalize_plan(self, plan: str | None) -> str:
        return normalize_plan_name(plan)

    def features_for(self, plan: str | None) -> dict:
        return get_plan_features(plan or 'starter')

    def has_feature(self, tenant, feature: str) -> bool:
        if tenant is None:
            return False
        if has_administrator_access(tenant):
            return True
        plan = tenant.effective_plan() if callable(getattr(tenant, 'effective_plan', None)) else getattr(tenant, 'plan', 'starter')
        return bool(self.features_for(plan).get(feature, False))
