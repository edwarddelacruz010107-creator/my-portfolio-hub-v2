"""Production-safe financial data reset utilities.

This module is intentionally separate from normal billing flows. It is meant
for a one-time pre-launch cleanup of test payments and subscriptions while
preserving tenants, plans, payment methods, provider configuration, and site
content.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import timedelta

from app.extensions import db
from app.models.core import (
    PaymentSubmission,
    Subscription,
    SubscriptionNotification,
    Tenant,
    WebhookEvent,
)
from app.models.tenant_data import Profile
from app.models.ledger import PaymentTransaction
from app.models.notification import Notification
from app.services.billing.trial_limits import get_trial_limits
from app.system_plan import has_administrator_access
from app.utils.datetime_utils import utc_now


@dataclass
class FinancialResetResult:
    immutable_ledger_transactions_preserved: int = 0
    payment_submissions_deleted: int = 0
    webhook_events_deleted: int = 0
    billing_notifications_deleted: int = 0
    subscriptions_deleted: int = 0
    tenants_reset_to_trial: int = 0
    profiles_reset_to_trial: int = 0
    protected_administrator_tenants: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


BILLING_NOTIFICATION_TYPES = {
    "payment_succeeded",
    "payment_failed",
    "payment_pending",
    "subscription_active",
    "subscription_activated",
    "subscription_updated",
    "subscription_renewed",
    "subscription_expired",
    "subscription_cancelled",
    "subscription_canceled",
    "subscription_on_hold",
    "renewal_reminder",
    "trial_expiring",
    "trial_expired",
    "manual_payment_approved",
    "manual_payment_rejected",
    "refund_succeeded",
    "refund_failed",
}


def preview_financial_reset() -> FinancialResetResult:
    """Return the number of rows that would be affected without changing data."""
    protected_ids = {
        tenant.id for tenant in Tenant.query.all() if has_administrator_access(tenant)
    }
    non_admin_tenants = Tenant.query.filter(~Tenant.id.in_(protected_ids)).all() if protected_ids else Tenant.query.all()

    legacy_billing_notification_count = SubscriptionNotification.query.filter(
        db.or_(
            SubscriptionNotification.subscription_id.isnot(None),
            SubscriptionNotification.notification_type.in_(BILLING_NOTIFICATION_TYPES),
        )
    ).count()
    unified_billing_notification_count = Notification.query.filter(
        Notification.event_type.like("billing.%")
    ).count()

    sub_query = Subscription.query
    if protected_ids:
        sub_query = sub_query.filter(~Subscription.tenant_id.in_(protected_ids))

    return FinancialResetResult(
        immutable_ledger_transactions_preserved=PaymentTransaction.query.count(),
        payment_submissions_deleted=PaymentSubmission.query.count(),
        webhook_events_deleted=WebhookEvent.query.count(),
        billing_notifications_deleted=(
            legacy_billing_notification_count + unified_billing_notification_count
        ),
        subscriptions_deleted=sub_query.count(),
        tenants_reset_to_trial=len(non_admin_tenants),
        profiles_reset_to_trial=Profile.query.filter(Profile.tenant_id.in_([t.id for t in non_admin_tenants])).count() if non_admin_tenants else 0,
        protected_administrator_tenants=len(protected_ids),
    )


def reset_financial_data() -> FinancialResetResult:
    """Delete test financial history and reset non-admin tenants to a fresh trial.

    Preserved:
    - tenants and users
    - Administrator/default tenant access
    - plan settings and prices
    - payment methods and QR codes
    - Dodo/PayMongo configuration in environment/platform settings
    - portfolio content, uploads, themes, and messages unrelated to billing

    Removed/reset:
    - payment submissions
    - webhook event logs
    - billing/subscription notifications
    - non-administrator subscription rows
    - non-administrator tenant/profile billing state (fresh trial)
    """
    ledger_count = PaymentTransaction.query.count()
    if ledger_count:
        raise RuntimeError(
            "Financial reset refused because immutable ledger transactions exist; "
            "use linked reversals or a separately authorized environment teardown"
        )
    result = FinancialResetResult(immutable_ledger_transactions_preserved=ledger_count)
    now = utc_now()
    trial_days = int(get_trial_limits().get("trial_duration_days", 7) or 7)
    trial_ends = now + timedelta(days=max(1, trial_days))

    protected_ids = {
        tenant.id for tenant in Tenant.query.all() if has_administrator_access(tenant)
    }
    result.protected_administrator_tenants = len(protected_ids)

    try:
        # Delete children first to keep the reset compatible with strict FK setups.
        result.payment_submissions_deleted = PaymentSubmission.query.delete(synchronize_session=False)

        billing_notifications = SubscriptionNotification.query.filter(
            db.or_(
                SubscriptionNotification.subscription_id.isnot(None),
                SubscriptionNotification.notification_type.in_(BILLING_NOTIFICATION_TYPES),
            )
        )
        result.billing_notifications_deleted = billing_notifications.delete(synchronize_session=False)
        result.billing_notifications_deleted += Notification.query.filter(
            Notification.event_type.like("billing.%")
        ).delete(synchronize_session=False)

        result.webhook_events_deleted = WebhookEvent.query.delete(synchronize_session=False)

        subscriptions = Subscription.query
        if protected_ids:
            subscriptions = subscriptions.filter(~Subscription.tenant_id.in_(protected_ids))
        result.subscriptions_deleted = subscriptions.delete(synchronize_session=False)

        tenants = Tenant.query.all()
        for tenant in tenants:
            if tenant.id in protected_ids:
                continue
            tenant.plan = "starter"
            tenant.plan_name = "starter"
            tenant.subscription_state = "trial"
            tenant.status = "active"
            tenant.trial_status = "active"
            tenant.trial_started_at = now
            tenant.trial_ends_at = trial_ends
            tenant.grace_period_ends_at = None
            tenant.subscription_started_at = None
            tenant.subscription_expires_at = None
            result.tenants_reset_to_trial += 1

        reset_tenant_ids = [tenant.id for tenant in tenants if tenant.id not in protected_ids]
        if reset_tenant_ids:
            profiles = Profile.query.filter(Profile.tenant_id.in_(reset_tenant_ids)).all()
            for profile in profiles:
                profile.plan = "Trial"
                profile.monthly_rate = 0.0
                profile.free_trial_days = trial_days
                profile.free_trial_ends = trial_ends
                if hasattr(profile, "_current_subscription_cache"):
                    delattr(profile, "_current_subscription_cache")
                result.profiles_reset_to_trial += 1

        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise
