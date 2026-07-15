"""Read-only portfolio publication, inventory, engagement, and completion model."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from app import db
from app.models.core import Inquiry
from app.models.intelligence import PortfolioIntelligenceSnapshot
from app.models.tenant_data import Profile, Project, ProjectReaction, Service
from app.services.founder.domain import PORTFOLIO_READ_MODEL_VERSION, PRIVACY_THRESHOLD


def _scope(model, tenant_ids: tuple[int, ...] | None):
    return [] if tenant_ids is None else [model.tenant_id.in_(tenant_ids)]


def _aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def build_portfolio_read_model(
    *,
    tenant_ids: tuple[int, ...] | None,
    start_at: datetime,
    end_at: datetime,
) -> dict:
    published_portfolios = int(
        Profile.query.filter(Profile.is_available.is_(True), *_scope(Profile, tenant_ids)).count()
    )
    published_projects = int(
        Project.query.filter(Project.status == "published", *_scope(Project, tenant_ids)).count()
    )
    visible_services = int(
        Service.query.filter(Service.is_visible.is_(True), *_scope(Service, tenant_ids)).count()
    )
    project_engagement = (
        db.session.query(
            func.coalesce(func.sum(Project.view_count), 0),
            func.coalesce(func.sum(Project.like_count), 0),
        )
        .filter(Project.status == "published", *_scope(Project, tenant_ids))
        .first()
    )
    reactions = int(
        db.session.query(func.count(ProjectReaction.id))
        .filter(*_scope(ProjectReaction, tenant_ids))
        .scalar()
        or 0
    )
    contact_filters = [
        Inquiry.sender == "visitor",
        Inquiry.is_spam.is_(False),
        Inquiry.created_at >= start_at,
        Inquiry.created_at < end_at,
        *_scope(Inquiry, tenant_ids),
    ]
    contact_inquiries = int(Inquiry.query.filter(*contact_filters).count())
    contact_delivered = int(
        Inquiry.query.filter(*contact_filters, Inquiry.delivery_status == "delivered").count()
    )

    latest_by_tenant = (
        db.session.query(
            PortfolioIntelligenceSnapshot.tenant_id.label("tenant_id"),
            func.max(PortfolioIntelligenceSnapshot.calculated_at).label("calculated_at"),
        )
        .filter(*_scope(PortfolioIntelligenceSnapshot, tenant_ids))
        .group_by(PortfolioIntelligenceSnapshot.tenant_id)
        .subquery()
    )
    completion = (
        db.session.query(
            func.count(PortfolioIntelligenceSnapshot.id),
            func.avg(PortfolioIntelligenceSnapshot.total_score),
            func.max(PortfolioIntelligenceSnapshot.calculated_at),
        )
        .join(
            latest_by_tenant,
            (PortfolioIntelligenceSnapshot.tenant_id == latest_by_tenant.c.tenant_id)
            & (PortfolioIntelligenceSnapshot.calculated_at == latest_by_tenant.c.calculated_at),
        )
        .first()
    )
    evaluated_count = int(completion[0] or 0)
    average_score = (
        Decimal(str(completion[1])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if completion[1] is not None and evaluated_count >= PRIVACY_THRESHOLD
        else None
    )

    freshness_values = [
        Profile.query.with_entities(func.max(Profile.updated_at)).filter(*_scope(Profile, tenant_ids)).scalar(),
        Project.query.with_entities(func.max(Project.updated_at)).filter(*_scope(Project, tenant_ids)).scalar(),
        Service.query.with_entities(func.max(Service.updated_at)).filter(*_scope(Service, tenant_ids)).scalar(),
        Inquiry.query.with_entities(func.max(Inquiry.created_at)).filter(*_scope(Inquiry, tenant_ids)).scalar(),
        completion[2],
    ]
    latest_evidence = max((_aware(value) for value in freshness_values if value is not None), default=None)
    return {
        "definition_version": PORTFOLIO_READ_MODEL_VERSION,
        "published_portfolios": published_portfolios,
        "published_projects": published_projects,
        "visible_services": visible_services,
        "project_engagement": {
            "views": int(project_engagement[0] or 0),
            "likes": int(project_engagement[1] or 0),
            "reactions": reactions,
            "interval_available": False,
            "reason": "Project engagement is stored only as cumulative counters",
        },
        "service_engagement": {
            "available": False,
            "value": None,
            "reason": "No versioned service-engagement event source exists",
        },
        "contacts": {
            "inquiries": contact_inquiries,
            "delivered": contact_delivered,
            "definition": "non-spam visitor inquiries received in the selected UTC interval",
        },
        "completion": {
            "available": average_score is not None,
            "average_score": average_score,
            "evaluated_tenants": evaluated_count,
            "reason": "" if average_score is not None else f"Requires {PRIVACY_THRESHOLD} latest evaluated portfolios",
        },
        "freshness": {"latest_evidence_at": latest_evidence},
    }
