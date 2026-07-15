"""Phase 8 usage evidence shaped for the founder dashboard."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func

from app import db
from app.models.ai_center import AIUsageRequest
from app.services.ai.domain import CONTROL_PLANE_VERSION


def build_ai_usage_read_model(
    *,
    start_at: datetime,
    end_at: datetime,
    provider: str | None,
    tenant_ids: tuple[int, ...] | None,
) -> dict:
    filters = [AIUsageRequest.created_at >= start_at, AIUsageRequest.created_at < end_at]
    if provider is not None:
        filters.append(AIUsageRequest.provider_code == provider)
    if tenant_ids is not None:
        filters.append(AIUsageRequest.tenant_id.in_(tenant_ids))
    aggregate = (
        db.session.query(
            func.count(AIUsageRequest.id),
            func.sum(case((AIUsageRequest.outcome == "failed", 1), else_=0)),
            func.avg(AIUsageRequest.latency_ms),
            func.sum(AIUsageRequest.cost_microunits),
            func.sum(case((AIUsageRequest.cost_microunits.isnot(None), 1), else_=0)),
            func.sum(case((AIUsageRequest.cost_microunits.is_(None), 1), else_=0)),
            func.max(AIUsageRequest.created_at),
        )
        .filter(*filters)
        .first()
    )
    requests = int(aggregate[0] or 0)
    known_cost_count = int(aggregate[4] or 0)
    unknown_cost_count = int(aggregate[5] or 0)
    provider_rows = (
        db.session.query(AIUsageRequest.provider_code, func.count(AIUsageRequest.id))
        .filter(*filters)
        .group_by(AIUsageRequest.provider_code)
        .order_by(AIUsageRequest.provider_code.asc())
        .all()
    )
    return {
        "definition_version": CONTROL_PLANE_VERSION,
        "requests": requests,
        "failures": int(aggregate[1] or 0),
        "average_latency_ms": int(aggregate[2]) if aggregate[2] is not None else None,
        "known_cost_microunits": int(aggregate[3] or 0) if known_cost_count else None,
        "known_cost_count": known_cost_count,
        "unavailable_cost_count": unknown_cost_count,
        "provider_requests": {str(name): int(count) for name, count in provider_rows},
        "freshness": {"latest_usage_at": aggregate[6]},
    }
