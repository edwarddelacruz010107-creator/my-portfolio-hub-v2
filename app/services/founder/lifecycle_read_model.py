"""Versioned tenant growth, activation, conversion, subscription, and churn reads."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from app import db
from app.models.billing_center import SubscriptionStatusEvent
from app.models.core import Subscription, Tenant
from app.services.founder.domain import (
    LIFECYCLE_DEFINITION_VERSION,
    FounderFilters,
    comparison_change,
    safe_rate,
)


TERMINAL_STATUSES = ("cancelled", "canceled", "expired")


def _tenant_filters(filters: FounderFilters):
    if filters.plan == "all":
        return []
    if filters.plan == "trial":
        return [func.lower(func.coalesce(Tenant.subscription_state, "")) == "trial"]
    return [
        func.lower(func.coalesce(Tenant.subscription_state, "")) != "trial",
        func.lower(func.coalesce(Tenant.plan, "")) == filters.plan,
    ]


def segment_tenant_ids(filters: FounderFilters) -> tuple[int, ...] | None:
    if filters.plan == "all":
        return None
    rows = db.session.query(Tenant.id).filter(*_tenant_filters(filters)).all()
    return tuple(int(row[0]) for row in rows)


def _scope(model, tenant_ids: tuple[int, ...] | None):
    return [] if tenant_ids is None else [model.tenant_id.in_(tenant_ids)]


def _provider_event_filter(filters: FounderFilters):
    return [] if filters.payment_provider == "all" else [
        func.lower(func.coalesce(SubscriptionStatusEvent.provider, "manual")) == filters.payment_provider
    ]


def _subscription_provider_filter(filters: FounderFilters):
    return [] if filters.payment_provider == "all" else [
        func.lower(Subscription.payment_provider) == filters.payment_provider
    ]


def _interval_counts(
    *,
    filters: FounderFilters,
    tenant_ids: tuple[int, ...] | None,
    start_at: datetime,
    end_at: datetime,
) -> dict:
    signups = int(
        db.session.query(func.count(Tenant.id))
        .filter(Tenant.created_at >= start_at, Tenant.created_at < end_at, *_tenant_filters(filters))
        .scalar()
        or 0
    )
    cohort = (
        db.session.query(Tenant.id)
        .filter(Tenant.created_at >= start_at, Tenant.created_at < end_at, *_tenant_filters(filters))
        .subquery()
    )
    activated_cohort = int(
        db.session.query(func.count(func.distinct(SubscriptionStatusEvent.tenant_id)))
        .filter(
            SubscriptionStatusEvent.tenant_id.in_(db.session.query(cohort.c.id)),
            SubscriptionStatusEvent.to_status == "active",
            SubscriptionStatusEvent.occurred_at < end_at,
            *_provider_event_filter(filters),
        )
        .scalar()
        or 0
    )
    activation_events = int(
        db.session.query(func.count(func.distinct(SubscriptionStatusEvent.tenant_id)))
        .filter(
            SubscriptionStatusEvent.to_status == "active",
            SubscriptionStatusEvent.occurred_at >= start_at,
            SubscriptionStatusEvent.occurred_at < end_at,
            *_scope(SubscriptionStatusEvent, tenant_ids),
            *_provider_event_filter(filters),
        )
        .scalar()
        or 0
    )
    return {"signups": signups, "activated_cohort": activated_cohort, "activation_events": activation_events}


def build_lifecycle_read_model(
    *,
    filters: FounderFilters,
    start_at: datetime,
    end_at: datetime,
    comparison_start_at: datetime | None,
    comparison_end_at: datetime | None,
) -> dict:
    tenant_ids = segment_tenant_ids(filters)
    interval = _interval_counts(
        filters=filters, tenant_ids=tenant_ids, start_at=start_at, end_at=end_at
    )
    comparison = (
        _interval_counts(
            filters=filters,
            tenant_ids=tenant_ids,
            start_at=comparison_start_at,
            end_at=comparison_end_at,
        )
        if comparison_start_at and comparison_end_at
        else None
    )

    active_base_filters = [
        Subscription.status == "active",
        (Subscription.started_at.is_(None) | (Subscription.started_at <= end_at)),
        (Subscription.expires_at.is_(None) | (Subscription.expires_at > end_at)),
        *_scope(Subscription, tenant_ids),
    ]
    all_active_subscriptions = int(
        db.session.query(func.count(func.distinct(Subscription.id)))
        .filter(*active_base_filters)
        .scalar()
        or 0
    )
    provider_evidenced_active = int(
        db.session.query(func.count(func.distinct(Subscription.id)))
        .filter(
            *active_base_filters,
            func.length(func.trim(func.coalesce(Subscription.payment_provider, ""))) > 0,
        )
        .scalar()
        or 0
    )
    active_subscriptions = int(
        db.session.query(func.count(func.distinct(Subscription.id)))
        .filter(
            *active_base_filters,
            *_subscription_provider_filter(filters),
        )
        .scalar()
        or 0
    )
    trial_query = db.session.query(func.count(Tenant.id)).filter(
        func.lower(func.coalesce(Tenant.subscription_state, "")) == "trial",
        *_tenant_filters(filters),
    )
    trial_tenants = int(trial_query.scalar() or 0)
    evidenced_active = int(
        db.session.query(func.count(func.distinct(SubscriptionStatusEvent.subscription_id)))
        .join(Subscription, Subscription.id == SubscriptionStatusEvent.subscription_id)
        .filter(
            SubscriptionStatusEvent.to_status == "active",
            *active_base_filters,
            *_provider_event_filter(filters),
        )
        .scalar()
        or 0
    )
    provider_assignment_complete = (
        filters.payment_provider == "all"
        or provider_evidenced_active >= all_active_subscriptions
    )
    lifecycle_complete = (
        (active_subscriptions == 0 or evidenced_active >= active_subscriptions)
        and provider_assignment_complete
    )
    conversion = safe_rate(interval["activated_cohort"], interval["signups"])
    if not lifecycle_complete:
        conversion = {
            **conversion,
            "available": False,
            "value": None,
            "reason": "Legacy active subscriptions lack versioned activation evidence",
        }

    ranked = (
        db.session.query(
            SubscriptionStatusEvent.subscription_id.label("subscription_id"),
            SubscriptionStatusEvent.to_status.label("to_status"),
            func.row_number().over(
                partition_by=SubscriptionStatusEvent.subscription_id,
                order_by=(
                    SubscriptionStatusEvent.occurred_at.desc(),
                    SubscriptionStatusEvent.created_at.desc(),
                    SubscriptionStatusEvent.id.desc(),
                ),
            ).label("row_number"),
        )
        .filter(
            SubscriptionStatusEvent.occurred_at < start_at,
            *_scope(SubscriptionStatusEvent, tenant_ids),
            *_provider_event_filter(filters),
        )
        .subquery()
    )
    active_at_start = db.session.query(ranked.c.subscription_id).filter(
        ranked.c.row_number == 1, ranked.c.to_status == "active"
    ).subquery()
    active_start_count = int(db.session.query(func.count(active_at_start.c.subscription_id)).scalar() or 0)
    churned = int(
        db.session.query(func.count(func.distinct(SubscriptionStatusEvent.subscription_id)))
        .filter(
            SubscriptionStatusEvent.subscription_id.in_(
                db.session.query(active_at_start.c.subscription_id)
            ),
            SubscriptionStatusEvent.to_status.in_(TERMINAL_STATUSES),
            SubscriptionStatusEvent.occurred_at >= start_at,
            SubscriptionStatusEvent.occurred_at < end_at,
            *_provider_event_filter(filters),
        )
        .scalar()
        or 0
    )
    churn = safe_rate(churned, active_start_count)
    if not lifecycle_complete:
        churn = {
            **churn,
            "available": False,
            "value": None,
            "reason": "Interval-start lifecycle coverage is incomplete",
        }

    latest_event = db.session.query(func.max(SubscriptionStatusEvent.created_at)).filter(
        *_scope(SubscriptionStatusEvent, tenant_ids), *_provider_event_filter(filters)
    ).scalar()
    return {
        "definition_version": LIFECYCLE_DEFINITION_VERSION,
        "tenant_ids": tenant_ids,
        "signups": interval["signups"],
        "activation_events": interval["activation_events"],
        "active_subscriptions": active_subscriptions,
        "active_subscriptions_available": provider_assignment_complete,
        "trial_tenants": trial_tenants,
        "trial_tenants_available": filters.payment_provider == "all",
        "conversion": conversion,
        "churn": churn,
        "coverage": {
            "complete": lifecycle_complete,
            "active_subscriptions": active_subscriptions,
            "active_with_versioned_event": evidenced_active,
            "provider_assignment_complete": provider_assignment_complete,
            "provider_evidenced_active": provider_evidenced_active,
            "all_active_subscriptions": all_active_subscriptions,
        },
        "comparison": {
            "signups": comparison_change(interval["signups"], comparison["signups"]),
            "activations": comparison_change(
                interval["activation_events"], comparison["activation_events"]
            ),
        } if comparison else None,
        "freshness": {"latest_lifecycle_recorded_at": latest_event},
    }
