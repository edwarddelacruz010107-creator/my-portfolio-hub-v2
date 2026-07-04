from __future__ import annotations

from app.services.billing.feature_gate_service import FeatureGateService
from app.services.billing.subscription_state_service import SubscriptionStateService


class AccessControlService:
    def __init__(self) -> None:
        self.state = SubscriptionStateService()
        self.gates = FeatureGateService()

    def can_publish(self, tenant) -> bool:
        return self.gates.can_publish(tenant)

    def can_upload(self, tenant) -> bool:
        return self.gates.can_upload(tenant)

    def can_view_dashboard(self, tenant) -> bool:
        return self.state.current_state(tenant) not in {'suspended', 'cancelled'}
