from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app import db
from app.models.core import Tenant
from app.models.tenant_data import Profile
from app.services.billing.lifecycle_service import LifecycleService
from app.services.billing.subscription_state_service import SubscriptionStateService
from app.services.shared.tenant_context import TenantContext


class DashboardService:
    def __init__(self) -> None:
        self.lifecycle = LifecycleService()
        self.state_service = SubscriptionStateService()

    def build_context(self, user) -> dict:
        tenant_id = getattr(user, 'tenant_id', None) if user else None
        tenant = Tenant.query.get(tenant_id) if tenant_id else None
        profile = Profile.query.filter_by(tenant_id=tenant_id).first() if tenant_id else None

        if tenant is not None:
            state = self.lifecycle.apply(tenant)
            db.session.add(tenant)
            if state:
                db.session.flush()

        state = self.state_service.current_state(tenant)
        context = TenantContext(
            tenant=tenant,
            profile=profile,
            subscription_state=state,
            plan=getattr(tenant, 'plan', 'starter') if tenant else 'starter',
            subscription_badge='Trial' if state == 'trial' else ('Active' if state == 'active' else state.title()),
            trial_days_left=self.state_service.trial_days_left(tenant),
        )
        return {
            'tenant_context': context,
            'subscription_state': context.subscription_state,
            'subscription_badge': context.subscription_badge,
            'trial_days_left': context.trial_days_left,
        }
