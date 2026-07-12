"""Shared superadmin analytics source of truth.

Both Platform Overview and Subscription Monitor consume this service so revenue,
MRR, provider totals, subscription counts, and charts cannot drift apart.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func

from app.models.portfolio import (
    Subscription,
    PaymentSubmission,
    WebhookEvent,
    Profile,
    Tenant,
    normalize_plan_name,
)
from app.utils import get_public_billing_plans
from app.services.billing.currency import currency_context
from app.system_plan import is_administrator_plan

_YEARLY = {"yearly", "annual", "annually", "year"}
_SUCCESS = {"approved", "paid", "completed", "succeeded", "success"}


def _money(value: Any) -> float:
    try:
        return float(Decimal(str(value or 0)))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _provider(sub: Subscription) -> str:
    raw = str(getattr(sub, "payment_provider", "") or "").strip().lower()
    method = str(getattr(sub, "payment_method", "") or "").strip().lower()
    if raw == "dodo" or getattr(sub, "dodo_subscription_id", None):
        return "dodo"
    if raw == "paymongo" or getattr(sub, "paymongo_subscription_id", None) or method == "paymongo":
        return "paymongo"
    return "manual"


def _plan_amount(sub: Subscription, plans: dict[str, dict]) -> float:
    key = normalize_plan_name(getattr(sub, "plan", None) or "")
    data = plans.get(key) or plans.get(getattr(sub, "plan", None)) or {}
    cycle = str(getattr(sub, "billing_cycle", "monthly") or "monthly").lower()
    if cycle in _YEARLY:
        value = data.get("price_yearly", data.get("base_price_yearly_usd"))
    else:
        value = data.get("price_monthly", data.get("base_price_usd", data.get("price")))
    return _money(value)


def _month_floor(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=timezone.utc)


def _shift_month(value: datetime, delta: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + delta
    return datetime(absolute // 12, absolute % 12 + 1, 1, tzinfo=timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_superadmin_analytics(*, months: int = 6) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    plans = get_public_billing_plans()

    # Analytics must use the reporting/display currency configured in Plan
    # Settings, not the first plan dictionary. Some legacy plan rows can carry
    # a converted numeric price while still retaining a stale "$" symbol.
    # Reading the shared currency service keeps Platform Overview, Subscription
    # Monitor, charts, and provider totals on the same currency and symbol.
    try:
        reporting_currency = currency_context()
        currency_symbol = reporting_currency.get("symbol") or "$"
        currency_code = str(reporting_currency.get("display_currency") or "USD").upper()
    except Exception:
        first_plan = next(iter(plans.values()), {})
        currency_symbol = first_plan.get("currency_symbol", "$") or "$"
        currency_code = str(first_plan.get("currency_code", "USD") or "USD").upper()

    # One current active subscription per tenant/provider/external subscription.
    active_rows = Subscription.query.filter_by(status="active").order_by(
        Subscription.updated_at.desc() if hasattr(Subscription, "updated_at") else Subscription.id.desc(),
        Subscription.id.desc(),
    ).all()
    unique_active: dict[tuple[str, Any], Subscription] = {}
    for sub in active_rows:
        if is_administrator_plan(getattr(sub, "plan", None)):
            continue
        provider = _provider(sub)
        external = getattr(sub, "dodo_subscription_id", None) or getattr(sub, "paymongo_subscription_id", None)
        tenant_key = getattr(sub, "tenant_id", None) or getattr(sub, "profile_id", None)
        # Revenue/MRR is tenant-based. Webhook retries or checkout retries can create
        # more than one provider subscription row for the same tenant. Count only
        # the newest active row per tenant and provider. Use the external ID only
        # when no tenant/profile ownership key is available.
        dedupe_identity = tenant_key if tenant_key is not None else (external or getattr(sub, "id", None))
        unique_active.setdefault((provider, dedupe_identity), sub)

    provider_revenue = {"dodo": 0.0, "paymongo": 0.0, "manual": 0.0}
    provider_active = {"dodo": 0, "paymongo": 0, "manual": 0}
    provider_original = {"dodo": [], "paymongo": [], "manual": []}
    mrr = 0.0

    for sub in unique_active.values():
        provider = _provider(sub)
        configured = _plan_amount(sub, plans)
        provider_revenue[provider] += configured
        provider_active[provider] += 1
        cycle = str(getattr(sub, "billing_cycle", "monthly") or "monthly").lower()
        mrr += configured / 12.0 if cycle in _YEARLY else configured

        original_amount = _money(getattr(sub, "amount_paid", 0))
        original_currency = str(getattr(sub, "provider_currency", "") or "").upper()
        if original_amount and original_currency:
            provider_original[provider].append({
                "amount": round(original_amount, 2),
                "currency": original_currency,
                "subscription_id": getattr(sub, "id", None),
            })

    # Completed manual payments are transactions, not recurring subscriptions.
    approved_manual = PaymentSubmission.query.filter(
        func.lower(PaymentSubmission.status).in_(list(_SUCCESS))
    ).all()
    manual_seen: set[Any] = set()
    for payment in approved_manual:
        key = (
            getattr(payment, "provider_payment_id", None)
            or getattr(payment, "transaction_reference", None)
            or payment.id
        )
        if key in manual_seen:
            continue
        manual_seen.add(key)
        payment_currency = str(getattr(payment, "currency_code", "") or "").upper()
        if currency_code == "USD" and getattr(payment, "amount_usd", None) is not None:
            amount = _money(payment.amount_usd)
        elif not payment_currency or payment_currency == currency_code:
            amount = _money(getattr(payment, "amount_paid", 0))
        else:
            amount = 0.0
        provider_revenue["manual"] += amount
        if getattr(payment, "amount_paid", None):
            provider_original["manual"].append({
                "amount": round(_money(payment.amount_paid), 2),
                "currency": payment_currency or currency_code,
                "payment_id": payment.id,
            })

    total_revenue = round(sum(provider_revenue.values()), 2)
    revenue_share = {
        key: round(value / total_revenue * 100, 1) if total_revenue else 0.0
        for key, value in provider_revenue.items()
    }
    provider_mix = {
        key: {
            "amount": round(provider_revenue[key], 2),
            "active": provider_active[key],
            "share": revenue_share[key],
            "original": provider_original[key],
        }
        for key in provider_revenue
    }

    total_pending_review = PaymentSubmission.query.filter_by(status="pending").count()
    total_pending_checkout = Subscription.query.filter_by(status="pending").count()
    total_expired = Subscription.query.filter_by(status="expired").count()
    total_cancelled = Subscription.query.filter_by(status="cancelled").count()
    total_trial = Profile.query.filter(func.lower(Profile.plan) == "trial").count()
    total_profiles = Profile.query.count()
    active_profiles = Profile.query.filter(Profile.is_available.is_(True)).count()
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
    for sub in expiring_7 + expiring_30:
        exp = _aware(getattr(sub, "expires_at", None))
        sub.days_left = (exp - now).days if exp else None

    churn_base = max(len(unique_active) + total_expired + total_cancelled, 1)
    churn_rate = round((total_expired + total_cancelled) / churn_base * 100, 1)
    active_rate = round(active_profiles / total_profiles * 100, 1) if total_profiles else 0.0

    # Monthly normalized revenue uses the same authoritative plan values.
    current_month = _month_floor(now)
    month_starts = [_shift_month(current_month, offset) for offset in range(-(months - 1), 1)]
    revenue_by_month = [0.0] * months
    tenant_growth = [0] * months

    all_subscriptions = Subscription.query.order_by(Subscription.id.asc()).all()
    seen_historical: set[tuple[str, Any, str]] = set()
    for sub in all_subscriptions:
        if is_administrator_plan(getattr(sub, "plan", None)):
            continue
        status = str(getattr(sub, "status", "") or "").lower()
        if status not in {"active", "expired", "cancelled"}:
            continue
        provider = _provider(sub)
        external = getattr(sub, "dodo_subscription_id", None) or getattr(sub, "paymongo_subscription_id", None)
        tenant_key = getattr(sub, "tenant_id", None) or getattr(sub, "profile_id", None)
        created = _aware(getattr(sub, "created_at", None) or getattr(sub, "started_at", None))
        if not created:
            continue
        month_key = created.strftime("%Y-%m")
        dedupe_identity = tenant_key if tenant_key is not None else (external or sub.id)
        dedupe_key = (provider, dedupe_identity, month_key)
        if dedupe_key in seen_historical:
            continue
        seen_historical.add(dedupe_key)
        amount = _plan_amount(sub, plans)
        for idx, start in enumerate(month_starts):
            if start <= created < _shift_month(start, 1):
                revenue_by_month[idx] += amount
                break

    # Add approved manual payments by actual approval/submission month.
    for payment in approved_manual:
        created = _aware(getattr(payment, "reviewed_at", None) or getattr(payment, "submitted_at", None))
        if not created:
            continue
        if currency_code == "USD" and getattr(payment, "amount_usd", None) is not None:
            amount = _money(payment.amount_usd)
        else:
            payment_currency = str(getattr(payment, "currency_code", "") or "").upper()
            amount = _money(payment.amount_paid) if not payment_currency or payment_currency == currency_code else 0.0
        for idx, start in enumerate(month_starts):
            if start <= created < _shift_month(start, 1):
                revenue_by_month[idx] += amount
                break

    for tenant in Tenant.query.all():
        created = _aware(getattr(tenant, "created_at", None))
        if not created:
            continue
        for idx, start in enumerate(month_starts):
            if start <= created < _shift_month(start, 1):
                tenant_growth[idx] += 1
                break

    chart_max = max(revenue_by_month) if revenue_by_month else 0.0
    revenue_chart = []
    for idx, (start, value) in enumerate(zip(month_starts, revenue_by_month)):
        x = 8 + idx * (84 / max(months - 1, 1))
        y = 84 - ((value / chart_max) * 66 if chart_max else 0)
        revenue_chart.append({
            "label": start.strftime("%b"),
            "value": round(value, 2),
            "x": round(x, 2),
            "y": round(y, 2),
        })
    revenue_polyline = " ".join(f"{p['x']},{p['y']}" for p in revenue_chart)
    revenue_area = f"8,90 {revenue_polyline} 92,90" if revenue_polyline else ""
    tenant_max = max(tenant_growth) if tenant_growth else 0
    tenant_growth_chart = [
        {
            "label": start.strftime("%b"),
            "value": value,
            "height": round(value / tenant_max * 100, 1) if tenant_max else 0,
        }
        for start, value in zip(month_starts, tenant_growth)
    ]

    since = now - timedelta(days=30)
    webhook_count = WebhookEvent.query.filter(WebhookEvent.received_at >= since).count()
    webhook_processed = WebhookEvent.query.filter(
        WebhookEvent.received_at >= since,
        WebhookEvent.processed.is_(True),
    ).count()
    webhook_health = round(webhook_processed / webhook_count * 100, 1) if webhook_count else 100.0

    return {
        "generated_at": now,
        "currency_symbol": currency_symbol,
        "currency_code": currency_code,
        "metrics": {
            "total_active": len(unique_active),
            "total_expiring": len(expiring_7),
            "total_expired": total_expired,
            "total_cancelled": total_cancelled,
            "total_pending": total_pending_review + total_pending_checkout,
            "total_pending_review": total_pending_review,
            "total_pending_checkout": total_pending_checkout,
            "total_trial": total_trial,
            "total_revenue": total_revenue,
            "mrr": round(mrr, 2),
            "arr": round(mrr * 12, 2),
            "total_tenants": total_profiles,
            "active_tenants": active_profiles,
            "active_rate": active_rate,
            "churn_rate": churn_rate,
            "expiring_30": len(expiring_30),
        },
        "provider_revenue": {k: round(v, 2) for k, v in provider_revenue.items()},
        "provider_active": provider_active,
        "provider_original": provider_original,
        "revenue_share": revenue_share,
        "provider_mix": provider_mix,
        "revenue_chart": revenue_chart,
        "revenue_polyline": revenue_polyline,
        "revenue_area": revenue_area,
        "tenant_growth_chart": tenant_growth_chart,
        "expiring_7": expiring_7,
        "expiring_30": expiring_30,
        "recent_webhooks": WebhookEvent.query.order_by(WebhookEvent.received_at.desc()).limit(20).all(),
        "webhook_count": webhook_count,
        "webhook_health": webhook_health,
        "unique_active_subscriptions": list(unique_active.values()),
    }
