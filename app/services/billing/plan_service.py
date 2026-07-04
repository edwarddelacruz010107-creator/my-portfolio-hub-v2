from __future__ import annotations

from app.models.core import get_plan_features, normalize_plan_name


class PlanService:
    def normalize_plan(self, plan: str | None) -> str:
        return normalize_plan_name(plan)

    def features_for(self, plan: str | None) -> dict:
        return get_plan_features(plan or 'starter')

    def has_feature(self, tenant, feature: str) -> bool:
        if tenant is None:
            return False
        return bool(self.features_for(getattr(tenant, 'plan', 'starter')).get(feature, False))
