from __future__ import annotations

from datetime import timedelta

from app.utils.datetime_utils import ensure_utc_aware, utc_now


class LifecycleService:
    def __init__(self) -> None:
        self.grace_days = 3

    def evaluate_state(self, tenant) -> str:
        if tenant is None:
            return 'none'
        current = (getattr(tenant, 'subscription_state', '') or '').strip().lower()
        if current in {'suspended', 'cancelled'}:
            return current

        now = utc_now()
        trial_ends = getattr(tenant, 'trial_ends_at', None)
        grace_ends = getattr(tenant, 'grace_period_ends_at', None)

        if current == 'trial' and trial_ends is not None:
            trial_ends = ensure_utc_aware(trial_ends)
            if trial_ends and trial_ends > now:
                return 'trial'
            if grace_ends is None:
                grace_ends = trial_ends + timedelta(days=self.grace_days)
                tenant.grace_period_ends_at = grace_ends
            if grace_ends is not None:
                grace_ends = ensure_utc_aware(grace_ends)
                if grace_ends and grace_ends > now:
                    return 'grace'
            return 'readonly'

        if current == 'grace' and grace_ends is not None:
            grace_ends = ensure_utc_aware(grace_ends)
            return 'grace' if grace_ends and grace_ends > now else 'readonly'

        if current == 'active':
            return 'active'
        return current or 'none'

    def apply(self, tenant) -> str:
        new_state = self.evaluate_state(tenant)
        if tenant is not None and getattr(tenant, 'subscription_state', None) != new_state:
            tenant.subscription_state = new_state
            tenant.subscription_status = new_state
        return new_state
