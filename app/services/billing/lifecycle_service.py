"""Canonical subscription lifecycle with provider adapters and audit events."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.billing_center import SubscriptionStatusEvent
from app.utils.datetime_utils import ensure_utc_aware, utc_now


class InvalidSubscriptionTransition(ValueError):
    pass


ALLOWED_TRANSITIONS = {
    "trial": {"pending", "active", "past_due", "expired", "cancelled"},
    "pending": {"scheduled", "active", "failed", "past_due", "cancelled", "expired"},
    "scheduled": {"active", "pending", "cancelled", "expired"},
    "active": {"past_due", "suspended", "cancelled", "expired"},
    "failed": {"pending", "active", "cancelled", "expired"},
    "past_due": {"pending", "active", "suspended", "cancelled", "expired"},
    "suspended": {"pending", "active", "cancelled", "expired"},
    "expired": {"pending", "active"},
    "cancelled": {"pending", "active"},
}

PROVIDER_STATE_MAP = {
    "paymongo": {
        "active": "active", "paid": "active", "payment.paid": "active",
        "payment.failed": "past_due", "failed": "past_due",
        "subscription.cancelled": "cancelled", "cancelled": "cancelled",
        "subscription.expired": "expired", "expired": "expired",
    },
    "dodo": {
        "active": "active", "subscription.active": "active", "subscription.renewed": "active",
        "payment.succeeded": "active", "checkout.session.completed": "active",
        "on_hold": "past_due", "subscription.on_hold": "past_due",
        "failed": "past_due", "payment.failed": "past_due", "subscription.failed": "past_due",
        "cancelled": "cancelled", "subscription.cancelled": "cancelled",
        "expired": "expired", "subscription.expired": "expired",
    },
    "manual": {"submitted": "pending", "approved": "active", "rejected": "failed"},
}


def adapt_provider_state(provider: str, state: str) -> str:
    provider_key = str(provider or "").strip().lower()
    state_key = str(state or "").strip().lower()
    try:
        return PROVIDER_STATE_MAP[provider_key][state_key]
    except KeyError as exc:
        raise InvalidSubscriptionTransition("provider state is not mapped") from exc


def can_transition(current: str, target: str) -> bool:
    current = str(current or "pending").lower()
    target = str(target or "").lower()
    return current == target or target in ALLOWED_TRANSITIONS.get(current, set())


def transition_subscription(
    subscription,
    target: str,
    *,
    actor: str,
    reason: str,
    idempotency_key: str,
    provider: str | None = None,
    provider_event_id: str | None = None,
    occurred_at=None,
    commit: bool = False,
):
    if not actor or not reason or not idempotency_key:
        raise ValueError("actor, reason, and idempotency_key are required")
    target = str(target or "").strip().lower()
    current = str(getattr(subscription, "status", "pending") or "pending").strip().lower()
    existing = SubscriptionStatusEvent.query.filter_by(idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing
    if not can_transition(current, target):
        raise InvalidSubscriptionTransition(f"cannot transition subscription from {current} to {target}")

    event = SubscriptionStatusEvent(
        subscription_id=subscription.id,
        tenant_id=subscription.tenant_id,
        from_status=current,
        to_status=target,
        actor=str(actor)[:120],
        reason=str(reason),
        provider=str(provider).lower()[:30] if provider else None,
        provider_event_id=str(provider_event_id)[:255] if provider_event_id else None,
        idempotency_key=str(idempotency_key)[:160],
        occurred_at=occurred_at or utc_now(),
    )
    try:
        with db.session.begin_nested():
            db.session.add(event)
            db.session.flush()
            if current != target:
                subscription.status = target
                if target == "active" and getattr(subscription, "started_at", None) is None:
                    subscription.started_at = occurred_at or utc_now()
                if target == "cancelled":
                    subscription.cancelled_at = occurred_at or utc_now()
                db.session.add(subscription)
        if commit:
            db.session.commit()
        return event
    except IntegrityError:
        existing = SubscriptionStatusEvent.query.filter_by(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing
        raise


class LifecycleService:
    """Compatibility API for tenant access plus canonical billing transitions."""

    def __init__(self) -> None:
        self.grace_days = 3

    def evaluate_state(self, tenant) -> str:
        if tenant is None:
            return "none"
        current = (getattr(tenant, "subscription_state", "") or "").strip().lower()
        if current in {"suspended", "cancelled"}:
            return current
        now = utc_now()
        trial_ends = getattr(tenant, "trial_ends_at", None)
        grace_ends = getattr(tenant, "grace_period_ends_at", None)
        if current == "trial" and trial_ends is not None:
            trial_ends = ensure_utc_aware(trial_ends)
            if trial_ends and trial_ends > now:
                return "trial"
            if grace_ends is None:
                grace_ends = trial_ends + timedelta(days=self.grace_days)
                tenant.grace_period_ends_at = grace_ends
            if grace_ends is not None:
                grace_ends = ensure_utc_aware(grace_ends)
                if grace_ends and grace_ends > now:
                    return "grace"
            return "readonly"
        if current == "grace" and grace_ends is not None:
            grace_ends = ensure_utc_aware(grace_ends)
            return "grace" if grace_ends and grace_ends > now else "readonly"
        return "active" if current == "active" else (current or "none")

    def apply(self, tenant) -> str:
        new_state = self.evaluate_state(tenant)
        if tenant is not None and getattr(tenant, "subscription_state", None) != new_state:
            tenant.subscription_state = new_state
            tenant.subscription_status = new_state
        return new_state

    def transition(self, subscription, target: str, **kwargs):
        return transition_subscription(subscription, target, **kwargs)
