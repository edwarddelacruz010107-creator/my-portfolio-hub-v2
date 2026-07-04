from __future__ import annotations

from datetime import datetime, timezone


class SubscriptionStateService:
    def current_state(self, tenant) -> str:
        if tenant is None:
            return 'none'
        state = (getattr(tenant, 'subscription_state', '') or '').strip().lower()
        if state in {'trial', 'active', 'grace', 'readonly', 'expired', 'suspended', 'cancelled'}:
            return state
        return 'none'

    def can_publish(self, tenant) -> bool:
        return self.current_state(tenant) in {'trial', 'active'}

    def can_upload(self, tenant) -> bool:
        return self.current_state(tenant) in {'trial', 'active'}

    def trial_days_left(self, tenant) -> int:
        if tenant is None:
            return 0
        trial_ends = getattr(tenant, 'trial_ends_at', None)
        if not trial_ends:
            return 0
        if trial_ends.tzinfo is None:
            trial_ends = trial_ends.replace(tzinfo=timezone.utc)
        return max(0, (trial_ends - datetime.now(timezone.utc)).days)
