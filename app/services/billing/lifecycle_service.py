from __future__ import annotations

from datetime import datetime, timezone, timedelta


class LifecycleService:
    def __init__(self) -> None:
        self.grace_days = 3

    def evaluate_state(self, tenant) -> str:
        if tenant is None:
            return 'none'
        current = (getattr(tenant, 'subscription_state', '') or '').strip().lower()
        if current in {'suspended', 'cancelled'}:
            return current

        now = datetime.now(timezone.utc)
        trial_ends = getattr(tenant, 'trial_ends_at', None)
        grace_ends = getattr(tenant, 'grace_period_ends_at', None)

        if current == 'trial' and trial_ends is not None:
            if trial_ends.tzinfo is None:
                trial_ends = trial_ends.replace(tzinfo=timezone.utc)
            if trial_ends > now:
                return 'trial'
            if grace_ends is None:
                grace_ends = trial_ends + timedelta(days=self.grace_days)
                tenant.grace_period_ends_at = grace_ends
            if grace_ends is not None:
                if grace_ends.tzinfo is None:
                    grace_ends = grace_ends.replace(tzinfo=timezone.utc)
                if grace_ends > now:
                    return 'grace'
            return 'readonly'

        if current == 'grace' and grace_ends is not None:
            if grace_ends.tzinfo is None:
                grace_ends = grace_ends.replace(tzinfo=timezone.utc)
            return 'grace' if grace_ends > now else 'readonly'

        if current == 'active':
            return 'active'
        return current or 'none'

    def apply(self, tenant) -> str:
        new_state = self.evaluate_state(tenant)
        if tenant is not None and getattr(tenant, 'subscription_state', None) != new_state:
            tenant.subscription_state = new_state
            tenant.subscription_status = new_state
        return new_state
