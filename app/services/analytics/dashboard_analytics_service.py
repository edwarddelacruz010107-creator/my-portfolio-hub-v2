"""Compatibility facade backed by the authoritative financial ledger.

Financial metrics come only from ``payment_transactions``. Subscription,
tenant, and operational counts remain SQL aggregates over their own domains.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import extract, func

from app.extensions import db
from app.models import PaymentSubmission, Profile, Subscription, Tenant, WebhookEvent
from app.services.ledger.analytics_service import build_ledger_analytics


def _month_floor(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=timezone.utc)


def _shift_month(value: datetime, delta: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + delta
    return datetime(absolute // 12, absolute % 12 + 1, 1, tzinfo=timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _active_subscription_filters(now: datetime):
    return (
        Subscription.status == "active",
        func.lower(func.coalesce(Subscription.plan, "")) != "administrator",
        func.lower(func.coalesce(Subscription.plan, "")) != "trial",
        (Subscription.started_at.is_(None) | (Subscription.started_at <= now)),
        (Subscription.expires_at.is_(None) | (Subscription.expires_at > now)),
    )


def _tenant_growth(*, now: datetime, months: int) -> list[dict[str, Any]]:
    current = _month_floor(now)
    starts = [_shift_month(current, offset) for offset in range(-(months - 1), 1)]
    rows = (
        db.session.query(
            extract("year", Tenant.created_at),
            extract("month", Tenant.created_at),
            func.count(Tenant.id),
        )
        .filter(Tenant.created_at >= starts[0])
        .group_by(extract("year", Tenant.created_at), extract("month", Tenant.created_at))
        .all()
    )
    values = {(int(year), int(month)): int(count) for year, month, count in rows}
    series = [values.get((start.year, start.month), 0) for start in starts]
    peak = max(series, default=0)
    return [
        {
            "label": start.strftime("%b"),
            "value": value,
            "height": round(value / peak * 100, 1) if peak else 0,
        }
        for start, value in zip(starts, series)
    ]


def build_superadmin_analytics(*, months: int = 6) -> dict[str, Any]:
    """Return the legacy dashboard contract from one versioned source."""
    months = min(max(int(months), 1), 24)
    now = datetime.now(timezone.utc)
    ledger = build_ledger_analytics(months=months)

    active_count = int(
        db.session.query(func.count(func.distinct(Subscription.tenant_id)))
        .filter(*_active_subscription_filters(now))
        .scalar()
        or 0
    )
    total_pending_review = PaymentSubmission.query.filter_by(status="pending").count()
    total_pending_checkout = Subscription.query.filter_by(status="pending").count()
    total_expired = Subscription.query.filter_by(status="expired").count()
    total_cancelled = Subscription.query.filter_by(status="cancelled").count()
    total_trial = Profile.query.filter(func.lower(Profile.plan) == "trial").count()
    total_profiles = Profile.query.count()
    active_profiles = Profile.query.filter(Profile.is_available.is_(True)).count()
    active_rate = round(active_profiles / total_profiles * 100, 1) if total_profiles else None

    horizon_7 = now + timedelta(days=7)
    horizon_30 = now + timedelta(days=30)
    expiring_7 = Subscription.query.filter(
        Subscription.status == "active",
        Subscription.expires_at.between(now, horizon_7),
    ).order_by(Subscription.expires_at.asc()).all()
    expiring_30 = Subscription.query.filter(
        Subscription.status == "active",
        Subscription.expires_at.between(now, horizon_30),
    ).order_by(Subscription.expires_at.asc()).all()
    for subscription in expiring_7 + expiring_30:
        expiry = _aware(subscription.expires_at)
        subscription.days_left = (expiry - now).days if expiry else None

    since = now - timedelta(days=30)
    webhook_count = WebhookEvent.query.filter(WebhookEvent.received_at >= since).count()
    webhook_processed = WebhookEvent.query.filter(
        WebhookEvent.received_at >= since,
        WebhookEvent.processed.is_(True),
    ).count()
    webhook_health = round(webhook_processed / webhook_count * 100, 1) if webhook_count else None
    recent_webhooks = WebhookEvent.query.order_by(WebhookEvent.received_at.desc()).limit(20).all()

    # The legacy schema has no interval-start subscription snapshot. Under the
    # approved definition, churn is therefore unavailable rather than guessed.
    churn_rate = None
    total_pending = total_pending_review + total_pending_checkout
    metrics = {
        "total_active": active_count,
        "total_expiring": len(expiring_7),
        "total_expired": total_expired,
        "total_cancelled": total_cancelled,
        "total_pending": total_pending,
        "total_pending_review": total_pending_review,
        "total_pending_checkout": total_pending_checkout,
        "total_trial": total_trial,
        "total_revenue": ledger["net_cash_revenue"],
        "gross_revenue": ledger["gross_cash_revenue"],
        "net_revenue": ledger["net_cash_revenue"],
        "mrr": ledger["mrr"],
        "arr": ledger["arr"],
        "total_tenants": total_profiles,
        "active_tenants": active_profiles,
        "active_rate": active_rate,
        "churn_rate": churn_rate,
        "churn_available": False,
        "expiring_30": len(expiring_30),
        "definition_version": ledger["definition_version"],
        "source_coverage": ledger["source_coverage"],
        "freshness": ledger["freshness"],
    }

    return {
        "generated_at": ledger["generated_at"],
        "currency_symbol": ledger["currency_symbol"],
        "currency_code": ledger["currency_code"],
        "definition_version": ledger["definition_version"],
        "definitions": ledger["definitions"],
        "freshness": ledger["freshness"],
        "source_coverage": ledger["source_coverage"],
        "cache_hit": ledger["cache_hit"],
        "metrics": metrics,
        "provider_revenue": ledger["provider_revenue"],
        "provider_active": ledger["provider_active"],
        "provider_original": ledger["provider_original"],
        "revenue_share": ledger["revenue_share"],
        "provider_mix": ledger["provider_mix"],
        "revenue_chart": ledger["revenue_chart"],
        "revenue_polyline": ledger["revenue_polyline"],
        "revenue_area": ledger["revenue_area"],
        "tenant_growth_chart": _tenant_growth(now=now, months=months),
        "expiring_7": expiring_7,
        "expiring_30": expiring_30,
        "recent_webhooks": recent_webhooks,
        "webhook_count": webhook_count,
        "webhook_health": webhook_health,
        "webhook_health_available": webhook_health is not None,
    }
