from __future__ import annotations

from app.utils.datetime_utils import ensure_utc_aware, utc_now

from app.system_plan import has_administrator_access


class SubscriptionStateService:
    def current_state(self, tenant) -> str:
        if tenant is None:
            return 'none'
        if has_administrator_access(tenant):
            return 'active'
        state = (getattr(tenant, 'subscription_state', '') or '').strip().lower()
        if state in {'trial', 'active', 'grace', 'readonly', 'expired', 'suspended', 'cancelled'}:
            return state
        return 'none'

    def can_publish(self, tenant) -> bool:
        return self.current_state(tenant) in {'trial', 'active'}

    def can_upload(self, tenant) -> bool:
        return self.current_state(tenant) in {'trial', 'active'}

    def trial_days_left(self, tenant) -> int:
        if tenant is None or has_administrator_access(tenant):
            return 0
        trial_ends = getattr(tenant, 'trial_ends_at', None)
        if not trial_ends:
            return 0
        trial_ends = ensure_utc_aware(trial_ends)
        if trial_ends is None:
            return 0
        return max(0, (trial_ends - utc_now()).days)
