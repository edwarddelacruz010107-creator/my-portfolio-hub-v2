"""One Phase 9 assembler composing trusted domain read models."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import time

from sqlalchemy import func

from app.extensions import cache, db
from app.models.ai_center import AIUsageRequest
from app.models.billing_center import SubscriptionStatusEvent
from app.models.core import Inquiry, Tenant
from app.models.intelligence import PortfolioIntelligenceSnapshot
from app.models.ledger import PaymentTransaction
from app.models.tenant_data import Profile, Project
from app.services.founder.ai_read_model import build_ai_usage_read_model
from app.services.founder.domain import (
    FOUNDER_DASHBOARD_VERSION,
    FounderFilters,
    comparison_change,
)
from app.services.founder.lifecycle_read_model import build_lifecycle_read_model
from app.services.founder.operations_read_model import build_operations_read_model
from app.services.founder.portfolio_read_model import build_portfolio_read_model
from app.services.ledger.analytics_service import build_founder_financial_read_model


CACHE_SECONDS = 60
ASSEMBLY_LATENCY_BUDGET_MS = 750


def _stamp(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _source_watermark() -> str:
    """Cheap max-timestamp fingerprint invalidates cached domain composition."""
    values = (
        db.session.query(func.max(Tenant.updated_at)).scalar(),
        db.session.query(func.max(PaymentTransaction.recorded_at)).scalar(),
        db.session.query(func.max(SubscriptionStatusEvent.created_at)).scalar(),
        db.session.query(func.max(PortfolioIntelligenceSnapshot.calculated_at)).scalar(),
        db.session.query(func.max(AIUsageRequest.created_at)).scalar(),
        db.session.query(func.max(Inquiry.updated_at)).scalar(),
        Profile.query.with_entities(func.max(Profile.updated_at)).scalar(),
        Project.query.with_entities(func.max(Project.updated_at)).scalar(),
    )
    return hashlib.sha256("|".join(_stamp(value) for value in values).encode("utf-8")).hexdigest()[:20]


def _build_business_snapshot(
    *, filters: FounderFilters, periods: dict, generated_at: datetime
) -> dict:
    lifecycle = build_lifecycle_read_model(filters=filters, **periods)
    tenant_ids = lifecycle["tenant_ids"]
    payment_provider = None if filters.payment_provider == "all" else filters.payment_provider
    ai_provider = None if filters.ai_provider == "all" else filters.ai_provider
    financial = build_founder_financial_read_model(
        start_at=periods["start_at"],
        end_at=periods["end_at"],
        provider=payment_provider,
        tenant_ids=tenant_ids,
    )
    financial_comparison = (
        build_founder_financial_read_model(
            start_at=periods["comparison_start_at"],
            end_at=periods["comparison_end_at"],
            provider=payment_provider,
            tenant_ids=tenant_ids,
        )
        if periods["comparison_start_at"] and periods["comparison_end_at"]
        else None
    )
    portfolio = build_portfolio_read_model(
        tenant_ids=tenant_ids,
        start_at=periods["start_at"],
        end_at=periods["end_at"],
    )
    ai = build_ai_usage_read_model(
        start_at=periods["start_at"],
        end_at=periods["end_at"],
        provider=ai_provider,
        tenant_ids=tenant_ids,
    )
    ai_comparison = (
        build_ai_usage_read_model(
            start_at=periods["comparison_start_at"],
            end_at=periods["comparison_end_at"],
            provider=ai_provider,
            tenant_ids=tenant_ids,
        )
        if periods["comparison_start_at"] and periods["comparison_end_at"]
        else None
    )
    comparisons = {
        "net_cash": comparison_change(
            financial["net_cash_revenue"],
            financial_comparison["net_cash_revenue"] if financial_comparison else None,
        ),
        "refunds": comparison_change(
            financial["refunds"],
            financial_comparison["refunds"] if financial_comparison else None,
        ),
        "ai_requests": comparison_change(ai["requests"], ai_comparison["requests"] if ai_comparison else None),
    }
    return {
        "version": FOUNDER_DASHBOARD_VERSION,
        "generated_at": generated_at,
        "periods": periods,
        "filters": {
            "days": filters.days,
            "comparison": filters.comparison,
            "payment_provider": filters.payment_provider,
            "ai_provider": filters.ai_provider,
            "plan": filters.plan,
        },
        "lifecycle": lifecycle,
        "financial": financial,
        "financial_comparison": financial_comparison,
        "portfolio": portfolio,
        "ai": ai,
        "ai_comparison": ai_comparison,
        "comparisons": comparisons,
        "definitions": {
            "lifecycle": lifecycle["definition_version"],
            "finance": financial["definition_version"],
            "portfolio": portfolio["definition_version"],
            "ai": ai["definition_version"],
        },
        "freshness": {
            "lifecycle": lifecycle["freshness"],
            "finance": financial["freshness"],
            "portfolio": portfolio["freshness"],
            "ai": ai["freshness"],
        },
    }


def build_founder_dashboard(
    *, filters: FounderFilters, as_of: datetime | None = None
) -> dict:
    started = time.monotonic()
    generated_at = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    periods = filters.periods(as_of=generated_at)
    watermark = _source_watermark()
    interval_bucket = periods["end_at"].strftime("%Y%m%d%H%M")
    cache_key = (
        f"founder:{FOUNDER_DASHBOARD_VERSION}:{filters.cache_fragment()}:"
        f"{interval_bucket}:{watermark}"
    )
    cache_hit = False
    cache_status = "available"
    try:
        snapshot = cache.get(cache_key)
        cache_hit = snapshot is not None
    except Exception:
        snapshot = None
        cache_status = "unavailable"
    if snapshot is None:
        snapshot = _build_business_snapshot(
            filters=filters, periods=periods, generated_at=generated_at
        )
        try:
            cache.set(cache_key, snapshot, timeout=CACHE_SECONDS)
        except Exception:
            cache_status = "unavailable"
    result = dict(snapshot)
    effective_periods = result["periods"]
    result["operations"] = build_operations_read_model(
        tenant_ids=result["lifecycle"]["tenant_ids"],
        start_at=effective_periods["start_at"],
        end_at=effective_periods["end_at"],
    )
    result["cache"] = {
        "hit": cache_hit,
        "status": cache_status,
        "ttl_seconds": CACHE_SECONDS,
        "source_watermark": watermark,
    }
    result["definitions"] = {
        **result["definitions"],
        "operations": result["operations"]["definition_version"],
    }
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    result["performance"] = {
        "assembly_ms": elapsed_ms,
        "budget_ms": ASSEMBLY_LATENCY_BUDGET_MS,
        "within_budget": elapsed_ms <= ASSEMBLY_LATENCY_BUDGET_MS,
        "bounded_incidents": 12,
        "bounded_audits": 15,
    }
    return result
