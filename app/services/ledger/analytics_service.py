"""Bounded SQL aggregation facade for all financial displays."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import and_, case, extract, func

from app.extensions import cache, db
from app.models import PaymentTransaction, Subscription
from app.services.ledger.definitions import DEFINITION_VERSION, METRIC_DEFINITIONS
from app.services.ledger.posting_service import LEDGER_CACHE_GENERATION_KEY


ZERO = Decimal("0")
YEARLY = ("yearly", "annual", "annually", "year")


def _month_floor(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=timezone.utc)


def _shift_month(value: datetime, delta: int) -> datetime:
    absolute = value.year * 12 + value.month - 1 + delta
    return datetime(absolute // 12, absolute % 12 + 1, 1, tzinfo=timezone.utc)


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _query_filters(tenant_id: int | None = None):
    filters = [
        PaymentTransaction.status == "posted",
        PaymentTransaction.provider_environment == "live",
    ]
    if tenant_id is not None:
        filters.append(PaymentTransaction.tenant_id == tenant_id)
    return filters


def build_ledger_analytics(*, months: int = 6, tenant_id: int | None = None) -> dict[str, Any]:
    months = min(max(int(months), 1), 24)
    try:
        generation = cache.get(LEDGER_CACHE_GENERATION_KEY) or "0"
    except Exception:
        generation = "0"
    cache_key = f"ledger-analytics:{DEFINITION_VERSION}:{generation}:{months}:{tenant_id or 'global'}"
    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return {**cached, "cache_hit": True}
    except Exception:
        pass

    session = db.session
    now = datetime.now(timezone.utc)
    filters = _query_filters(tenant_id)

    provider_rows = (
        session.query(
            PaymentTransaction.provider,
            func.coalesce(func.sum(PaymentTransaction.usd_reporting_amount), 0),
        )
        .filter(*filters)
        .group_by(PaymentTransaction.provider)
        .all()
    )
    provider_revenue = {"dodo": ZERO, "paymongo": ZERO, "manual": ZERO}
    for provider, amount in provider_rows:
        if provider in provider_revenue:
            provider_revenue[provider] = _decimal(amount)

    gross = _decimal(
        session.query(
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                PaymentTransaction.accounting_type.in_(["settlement", "adjustment"]),
                                PaymentTransaction.usd_reporting_amount > 0,
                            ),
                            PaymentTransaction.usd_reporting_amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        ).filter(*filters).scalar()
    )
    net = sum(provider_revenue.values(), ZERO)

    coverage_filters = [PaymentTransaction.provider_environment == "live"]
    if tenant_id is not None:
        coverage_filters.append(PaymentTransaction.tenant_id == tenant_id)
    coverage_rows = (
        session.query(PaymentTransaction.status, func.count(PaymentTransaction.id))
        .filter(*coverage_filters)
        .group_by(PaymentTransaction.status)
        .all()
    )
    source_coverage = {"posted": 0, "review_required": 0}
    for status, count in coverage_rows:
        source_coverage[status] = int(count)

    latest_query = session.query(func.max(PaymentTransaction.recorded_at)).filter(*coverage_filters)
    latest_recorded = latest_query.scalar()
    if latest_recorded and latest_recorded.tzinfo is None:
        latest_recorded = latest_recorded.replace(tzinfo=timezone.utc)
    age_seconds = int((now - latest_recorded).total_seconds()) if latest_recorded else None
    stale = latest_recorded is None or age_seconds > 900

    ranked_settlements = (
        session.query(
            PaymentTransaction.id.label("transaction_id"),
            PaymentTransaction.subscription_id.label("subscription_id"),
            func.row_number().over(
                partition_by=PaymentTransaction.subscription_id,
                order_by=(PaymentTransaction.recorded_at.desc(), PaymentTransaction.id.desc()),
            ).label("row_number"),
        )
        .filter(
            *filters,
            PaymentTransaction.accounting_type == "settlement",
            PaymentTransaction.subscription_id.isnot(None),
        )
        .subquery()
    )
    latest_settlement = session.query(
        ranked_settlements.c.transaction_id,
        ranked_settlements.c.subscription_id,
    ).filter(ranked_settlements.c.row_number == 1).subquery()
    active_subscription_filters = [
        Subscription.status == "active",
        func.lower(func.coalesce(Subscription.plan, "")) != "administrator",
        func.lower(func.coalesce(Subscription.plan, "")) != "trial",
        (Subscription.started_at.is_(None) | (Subscription.started_at <= now)),
        (Subscription.expires_at.is_(None) | (Subscription.expires_at > now)),
    ]
    recurring_value = case(
        (func.lower(Subscription.billing_cycle).in_(YEARLY), PaymentTransaction.usd_reporting_amount / Decimal("12")),
        else_=PaymentTransaction.usd_reporting_amount,
    )
    mrr_query = (
        session.query(func.coalesce(func.sum(recurring_value), 0))
        .join(
            latest_settlement,
            PaymentTransaction.id == latest_settlement.c.transaction_id,
        )
        .join(Subscription, Subscription.id == PaymentTransaction.subscription_id)
        .filter(*active_subscription_filters)
    )
    if tenant_id is not None:
        mrr_query = mrr_query.filter(PaymentTransaction.tenant_id == tenant_id)
    mrr = _decimal(mrr_query.scalar()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    arr = (mrr * Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    provider_active_rows = (
        session.query(
            PaymentTransaction.provider,
            func.count(func.distinct(PaymentTransaction.tenant_id)),
        )
        .join(
            latest_settlement,
            PaymentTransaction.id == latest_settlement.c.transaction_id,
        )
        .join(Subscription, Subscription.id == PaymentTransaction.subscription_id)
        .filter(*active_subscription_filters)
        .group_by(PaymentTransaction.provider)
    )
    if tenant_id is not None:
        provider_active_rows = provider_active_rows.filter(PaymentTransaction.tenant_id == tenant_id)
    provider_active_rows = provider_active_rows.all()
    provider_active = {"dodo": 0, "paymongo": 0, "manual": 0}
    for provider, count in provider_active_rows:
        if provider in provider_active:
            provider_active[provider] = int(count)

    original_rows = (
        session.query(
            PaymentTransaction.provider,
            PaymentTransaction.original_currency,
            PaymentTransaction.currency_exponent,
            func.sum(PaymentTransaction.original_amount_minor),
        )
        .filter(*filters)
        .group_by(
            PaymentTransaction.provider,
            PaymentTransaction.original_currency,
            PaymentTransaction.currency_exponent,
        )
        .all()
    )
    provider_original = {"dodo": [], "paymongo": [], "manual": []}
    for provider, currency, exponent, minor in original_rows:
        if provider in provider_original:
            provider_original[provider].append({
                "amount": Decimal(int(minor or 0)).scaleb(-int(exponent)),
                "currency": currency,
            })

    current_month = _month_floor(now)
    month_starts = [_shift_month(current_month, offset) for offset in range(-(months - 1), 1)]
    first_month = month_starts[0]
    monthly_rows = (
        session.query(
            extract("year", PaymentTransaction.occurred_at),
            extract("month", PaymentTransaction.occurred_at),
            func.coalesce(func.sum(PaymentTransaction.usd_reporting_amount), 0),
        )
        .filter(*filters, PaymentTransaction.occurred_at >= first_month)
        .group_by(extract("year", PaymentTransaction.occurred_at), extract("month", PaymentTransaction.occurred_at))
        .all()
    )
    monthly_map = {(int(year), int(month)): _decimal(amount) for year, month, amount in monthly_rows}
    values = [monthly_map.get((month.year, month.month), ZERO) for month in month_starts]
    chart_max = max(values, default=ZERO)
    revenue_chart = []
    for index, (month, value) in enumerate(zip(month_starts, values)):
        x = Decimal("8") + Decimal(index) * (Decimal("84") / Decimal(max(months - 1, 1)))
        y = Decimal("84") - ((value / chart_max) * Decimal("66") if chart_max else ZERO)
        revenue_chart.append({
            "label": month.strftime("%b"), "value": value.quantize(Decimal("0.01")),
            "x": x.quantize(Decimal("0.01")), "y": y.quantize(Decimal("0.01")),
        })
    revenue_polyline = " ".join(f"{item['x']},{item['y']}" for item in revenue_chart)
    revenue_area = f"8,90 {revenue_polyline} 92,90" if revenue_polyline else ""

    revenue_share = {
        provider: ((amount / net * Decimal("100")).quantize(Decimal("0.1")) if net else ZERO)
        for provider, amount in provider_revenue.items()
    }
    provider_mix = {
        provider: {
            "amount": provider_revenue[provider].quantize(Decimal("0.01")),
            "active": provider_active[provider],
            "share": revenue_share[provider],
            "original": provider_original[provider],
        }
        for provider in provider_revenue
    }

    result = {
        "generated_at": now,
        "definition_version": DEFINITION_VERSION,
        "definitions": METRIC_DEFINITIONS,
        "currency_code": "USD",
        "currency_symbol": "$",
        "gross_cash_revenue": gross.quantize(Decimal("0.01")),
        "net_cash_revenue": net.quantize(Decimal("0.01")),
        "mrr": mrr,
        "arr": arr,
        "provider_revenue": {key: value.quantize(Decimal("0.01")) for key, value in provider_revenue.items()},
        "provider_active": provider_active,
        "provider_original": provider_original,
        "revenue_share": revenue_share,
        "provider_mix": provider_mix,
        "revenue_chart": revenue_chart,
        "revenue_polyline": revenue_polyline,
        "revenue_area": revenue_area,
        "source_coverage": source_coverage,
        "freshness": {
            "latest_recorded_at": latest_recorded,
            "age_seconds": age_seconds,
            "stale": stale,
            "label": "Unavailable" if latest_recorded is None else ("Stale" if stale else "Current"),
        },
        "cache_hit": False,
    }
    try:
        cache.set(cache_key, result, timeout=120)
    except Exception:
        pass
    return result


def build_founder_financial_read_model(
    *,
    start_at: datetime,
    end_at: datetime,
    provider: str | None = None,
    tenant_ids: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Return exact interval cash plus current recurring value for Phase 9.

    This stays in the ledger domain so the founder dashboard does not recreate
    financial definitions. ``tenant_ids=()`` intentionally produces an empty
    segment, while ``None`` means every tenant.
    """
    if start_at.tzinfo is None or end_at.tzinfo is None or start_at >= end_at:
        raise ValueError("founder financial interval must be an aware increasing range")
    if provider is not None and provider not in {"dodo", "paymongo", "manual"}:
        raise ValueError("unsupported payment provider filter")

    base_filters = [
        PaymentTransaction.status == "posted",
        PaymentTransaction.provider_environment == "live",
    ]
    segment_filters = list(base_filters)
    if provider is not None:
        segment_filters.append(PaymentTransaction.provider == provider)
    if tenant_ids is not None:
        segment_filters.append(PaymentTransaction.tenant_id.in_(tenant_ids))
    interval_filters = [
        *segment_filters,
        PaymentTransaction.occurred_at >= start_at,
        PaymentTransaction.occurred_at < end_at,
    ]
    session = db.session

    provider_rows = (
        session.query(
            PaymentTransaction.provider,
            func.coalesce(func.sum(PaymentTransaction.usd_reporting_amount), 0),
        )
        .filter(*interval_filters)
        .group_by(PaymentTransaction.provider)
        .all()
    )
    provider_net = {"dodo": ZERO, "paymongo": ZERO, "manual": ZERO}
    for row_provider, amount in provider_rows:
        if row_provider in provider_net:
            provider_net[row_provider] = _decimal(amount)

    gross = _decimal(
        session.query(func.coalesce(func.sum(PaymentTransaction.usd_reporting_amount), 0))
        .filter(
            *interval_filters,
            PaymentTransaction.usd_reporting_amount > 0,
            PaymentTransaction.accounting_type.in_(["settlement", "adjustment"]),
        )
        .scalar()
    )
    refund_total = -_decimal(
        session.query(func.coalesce(func.sum(PaymentTransaction.usd_reporting_amount), 0))
        .filter(
            *interval_filters,
            PaymentTransaction.accounting_type.in_(["refund", "reversal", "chargeback"]),
        )
        .scalar()
    )
    net = sum(provider_net.values(), ZERO)

    coverage_filters = [
        PaymentTransaction.provider_environment == "live",
        PaymentTransaction.occurred_at >= start_at,
        PaymentTransaction.occurred_at < end_at,
    ]
    if provider is not None:
        coverage_filters.append(PaymentTransaction.provider == provider)
    if tenant_ids is not None:
        coverage_filters.append(PaymentTransaction.tenant_id.in_(tenant_ids))
    coverage_rows = (
        session.query(PaymentTransaction.status, func.count(PaymentTransaction.id))
        .filter(*coverage_filters)
        .group_by(PaymentTransaction.status)
        .all()
    )
    coverage = {"posted": 0, "review_required": 0}
    for status, count in coverage_rows:
        coverage[str(status)] = int(count)

    ranked_settlements = (
        session.query(
            PaymentTransaction.id.label("transaction_id"),
            PaymentTransaction.subscription_id.label("subscription_id"),
            func.row_number().over(
                partition_by=PaymentTransaction.subscription_id,
                order_by=(PaymentTransaction.recorded_at.desc(), PaymentTransaction.id.desc()),
            ).label("row_number"),
        )
        .filter(
            *segment_filters,
            PaymentTransaction.accounting_type == "settlement",
            PaymentTransaction.subscription_id.isnot(None),
        )
        .subquery()
    )
    latest_settlement = session.query(
        ranked_settlements.c.transaction_id,
        ranked_settlements.c.subscription_id,
    ).filter(ranked_settlements.c.row_number == 1).subquery()
    now = datetime.now(timezone.utc)
    recurring_value = case(
        (
            func.lower(Subscription.billing_cycle).in_(YEARLY),
            PaymentTransaction.usd_reporting_amount / Decimal("12"),
        ),
        else_=PaymentTransaction.usd_reporting_amount,
    )
    mrr = _decimal(
        session.query(func.coalesce(func.sum(recurring_value), 0))
        .join(latest_settlement, PaymentTransaction.id == latest_settlement.c.transaction_id)
        .join(Subscription, Subscription.id == PaymentTransaction.subscription_id)
        .filter(
            Subscription.status == "active",
            func.lower(func.coalesce(Subscription.plan, "")) != "administrator",
            func.lower(func.coalesce(Subscription.plan, "")) != "trial",
            (Subscription.started_at.is_(None) | (Subscription.started_at <= now)),
            (Subscription.expires_at.is_(None) | (Subscription.expires_at > now)),
        )
        .scalar()
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    latest_recorded = (
        session.query(func.max(PaymentTransaction.recorded_at))
        .filter(*coverage_filters)
        .scalar()
    )
    if latest_recorded and latest_recorded.tzinfo is None:
        latest_recorded = latest_recorded.replace(tzinfo=timezone.utc)
    age_seconds = int((now - latest_recorded).total_seconds()) if latest_recorded else None
    return {
        "definition_version": DEFINITION_VERSION,
        "generated_at": now,
        "currency_code": "USD",
        "gross_cash_revenue": gross.quantize(Decimal("0.01")),
        "net_cash_revenue": net.quantize(Decimal("0.01")),
        "refunds": refund_total.quantize(Decimal("0.01")),
        "mrr": mrr,
        "arr": (mrr * Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "provider_net": {
            key: value.quantize(Decimal("0.01")) for key, value in provider_net.items()
        },
        "source_coverage": coverage,
        "freshness": {
            "latest_recorded_at": latest_recorded,
            "age_seconds": age_seconds,
            "stale": latest_recorded is None or age_seconds > 900,
            "label": "Unavailable" if latest_recorded is None else ("Stale" if age_seconds > 900 else "Current"),
        },
    }
