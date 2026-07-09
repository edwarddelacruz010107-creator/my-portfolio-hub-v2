from __future__ import annotations

from app.services.billing.plan_service import PlanService
from app.services.billing.subscription_state_service import SubscriptionStateService


class FeatureGateService:
    def __init__(self) -> None:
        self.plan_service = PlanService()
        self.state_service = SubscriptionStateService()

    def can_publish(self, tenant) -> bool:
        return self.state_service.can_publish(tenant)

    def can_upload(self, tenant) -> bool:
        return self.state_service.can_upload(tenant)

    def can_access_analytics(self, tenant) -> bool:
        if self.state_service.current_state(tenant) in {'readonly', 'suspended', 'expired', 'cancelled'}:
            return False
        return self.plan_service.has_feature(tenant, 'analytics')

    def can_use_custom_domain(self, tenant) -> bool:
        if self.state_service.current_state(tenant) in {'readonly', 'suspended', 'expired', 'cancelled'}:
            return False
        return self.plan_service.has_feature(tenant, 'custom_domain')
