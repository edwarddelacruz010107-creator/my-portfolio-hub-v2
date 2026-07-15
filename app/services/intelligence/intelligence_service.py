"""Tenant-scoped persistence adapter for the pure intelligence rubric."""
from __future__ import annotations

from datetime import date, datetime, timezone
import logging
from typing import Any, Iterable

from sqlalchemy.exc import IntegrityError

from app import db
from app.models.intelligence import PortfolioIntelligenceSnapshot
from app.models.tenant_data import (
    Certificate,
    Profile,
    Project,
    Service,
    Testimonial,
    WorkExperience,
)
from app.models.tenant_form_settings import TenantFormSettings
from app.services.intelligence.domain import PortfolioFacts, RUBRIC_VERSION, evaluate_portfolio


logger = logging.getLogger(__name__)


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value else None


def _latest(values: Iterable[Any]) -> str | None:
    parsed: list[datetime] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            parsed.append(value.astimezone(timezone.utc))
    return max(parsed).isoformat() if parsed else None


def _profile_fact(profile: Profile | None) -> dict[str, Any]:
    if profile is None:
        return {}
    fields = (
        "name", "title", "bio", "location", "email", "profile_image",
        "profile_image_alt", "meta_title", "meta_description", "og_image",
        "seo_indexable", "selected_theme",
    )
    return {field: getattr(profile, field, None) for field in fields}


def _item_fact(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _iso(getattr(row, field, None)) if field.endswith("_date") else getattr(row, field, None) for field in fields}


def collect_portfolio_facts(tenant_id: int, *, as_of: date | datetime | None = None) -> PortfolioFacts:
    """Read only the requested tenant from both database binds."""
    tenant_id = int(tenant_id)
    profile = Profile.query.filter_by(tenant_id=tenant_id).first()
    projects = Project.query.filter_by(tenant_id=tenant_id).all()
    services = Service.query.filter_by(tenant_id=tenant_id).all()
    testimonials = Testimonial.query.filter_by(tenant_id=tenant_id).all()
    certificates = Certificate.query.filter_by(tenant_id=tenant_id).all()
    experiences = WorkExperience.query.filter_by(tenant_id=tenant_id).all()
    form_settings = TenantFormSettings.query.filter_by(tenant_id=tenant_id).first()

    project_facts = [
        _item_fact(row, (
            "id", "status", "title", "description", "description_short",
            "image", "image_alt", "before_image", "before_image_alt",
            "after_image", "after_image_alt", "live_url", "github_url",
            "prototype_url", "outcome_summary", "client_quote",
        ))
        for row in projects
    ]
    service_facts = [_item_fact(row, ("id", "is_visible", "title", "description", "features")) for row in services]
    testimonial_facts = [_item_fact(row, ("id", "is_visible", "author_name", "content")) for row in testimonials]
    certificate_facts = [
        _item_fact(row, (
            "id", "is_visible", "title", "description", "credential_id",
            "verification_url", "skills",
        ))
        for row in certificates
    ]
    experience_facts = [
        _item_fact(row, (
            "id", "is_visible", "role", "company", "start_date",
            "end_date", "is_current", "description", "achievements",
        ))
        for row in experiences
    ]
    timestamps = [getattr(profile, "updated_at", None)]
    for rows in (projects, services, testimonials, certificates, experiences):
        for row in rows:
            timestamps.append(getattr(row, "updated_at", None) or getattr(row, "created_at", None))

    provider = getattr(form_settings, "provider", "disabled") if form_settings else "disabled"
    provider_ready = bool(form_settings and form_settings.is_enabled and form_settings.is_configured)
    return PortfolioFacts(
        theme_id=(getattr(profile, "selected_theme", None) or "default"),
        profile=_profile_fact(profile),
        projects=tuple(project_facts),
        services=tuple(service_facts),
        testimonials=tuple(testimonial_facts),
        certificates=tuple(certificate_facts),
        experiences=tuple(experience_facts),
        contact={
            "internal_inbox_available": True,
            "public_email": getattr(profile, "email", "") if profile else "",
            "provider": provider,
            "external_provider_ready": provider_ready,
        },
        latest_content_at=_latest(timestamps),
        as_of=as_of or datetime.now(timezone.utc).date(),
    )


def _snapshot_result(snapshot: PortfolioIntelligenceSnapshot, *, theme_id: str) -> dict[str, Any]:
    evidence = snapshot.evidence or {}
    dimensions = []
    for item in snapshot.dimension_scores or []:
        dimension = dict(item)
        dimension["evidence"] = list(evidence.get(dimension.get("key"), []))
        dimensions.append(dimension)
    calculated_at = snapshot.calculated_at
    if calculated_at and calculated_at.tzinfo is None:
        calculated_at = calculated_at.replace(tzinfo=timezone.utc)
    return {
        "rubric_version": snapshot.rubric_version,
        "portfolio_hash": snapshot.portfolio_hash,
        "theme_id": theme_id,
        "total_score": round(float(snapshot.total_score)) if snapshot.total_score is not None else None,
        "evaluated_weight": int(snapshot.evaluated_weight),
        "dimensions": dimensions,
        "recommendations": list(snapshot.recommendations or []),
        "calculated_at": calculated_at.isoformat() if calculated_at else None,
        "definition": "Weighted readiness from stored portfolio facts; unavailable checks do not affect the total.",
    }


def get_portfolio_intelligence(
    tenant_id: int,
    *,
    persist: bool = True,
    as_of: date | datetime | None = None,
) -> dict[str, Any]:
    """Calculate or return the cached snapshot for exactly one tenant."""
    tenant_id = int(tenant_id)
    facts = collect_portfolio_facts(tenant_id, as_of=as_of)
    calculated = evaluate_portfolio(facts)
    existing = PortfolioIntelligenceSnapshot.query.filter_by(
        tenant_id=tenant_id,
        portfolio_hash=calculated["portfolio_hash"],
        rubric_version=RUBRIC_VERSION,
    ).first()
    if existing is not None:
        return _snapshot_result(existing, theme_id=calculated["theme_id"])
    if not persist:
        return calculated

    dimensions = []
    evidence = {}
    for item in calculated["dimensions"]:
        stored = dict(item)
        evidence[item["key"]] = stored.pop("evidence", [])
        dimensions.append(stored)
    snapshot = PortfolioIntelligenceSnapshot(
        tenant_id=tenant_id,
        portfolio_hash=calculated["portfolio_hash"],
        rubric_version=calculated["rubric_version"],
        total_score=calculated["total_score"],
        evaluated_weight=calculated["evaluated_weight"],
        dimension_scores=dimensions,
        evidence=evidence,
        recommendations=calculated["recommendations"],
        calculated_at=datetime.fromisoformat(calculated["calculated_at"]),
    )
    db.session.add(snapshot)
    try:
        db.session.commit()
    except IntegrityError:
        # Two workers can calculate the same new hash concurrently.  The
        # unique constraint selects the winner without creating duplicate
        # history or failing the content write that triggered recalculation.
        db.session.rollback()
        snapshot = PortfolioIntelligenceSnapshot.query.filter_by(
            tenant_id=tenant_id,
            portfolio_hash=calculated["portfolio_hash"],
            rubric_version=RUBRIC_VERSION,
        ).one()
    return _snapshot_result(snapshot, theme_id=calculated["theme_id"])


def recalculate_after_write(tenant_id: int) -> dict[str, Any] | None:
    """Best-effort post-commit hook used by relevant admin writes."""
    try:
        return get_portfolio_intelligence(int(tenant_id), persist=True)
    except Exception:
        db.session.rollback()
        logger.exception("Portfolio intelligence recalculation failed: tenant_id=%s", tenant_id)
        return None
