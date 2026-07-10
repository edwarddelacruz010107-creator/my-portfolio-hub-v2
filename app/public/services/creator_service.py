"""
app/public/services/creator_service.py — Cross-tenant creator discovery.

Read-only. Used by the landing page ("Featured Creators") and /explore.
See app/public/services/__init__.py for the cross-DB-bind constraint this
module works around, and serializers.py for the field allowlist every
result is passed through before it reaches a template.
"""

from __future__ import annotations

import logging

from .serializers import serialize_creator_card

logger = logging.getLogger(__name__)


def _visible_project_filter(Project):
    """Match public project visibility used by the landing showcase."""
    from sqlalchemy import and_, or_

    return or_(
        Project.status == "published",
        and_(
            Project.tenant_slug == "default",
            Project.status == "draft",
            Project.is_featured.is_(True),
        ),
    )


def _active_tenant_slugs() -> set[str]:
    """
    core_db tenants with status='active'. Suspended/cancelled tenants must
    never surface in public discovery, regardless of what's in their
    (still-live) tenant_db Profile row.
    """
    from app.models.core import Tenant

    return {t.slug for t in Tenant.query.filter(Tenant.status == "active").with_entities(Tenant.slug).all()}


def _project_counts(tenant_slugs: list[str]) -> dict[str, int]:
    """One grouped COUNT query instead of N per-creator queries."""
    from app.models.tenant_data import Project
    from sqlalchemy import func

    if not tenant_slugs:
        return {}

    rows = (
        Project.query.with_entities(Project.tenant_slug, func.count(Project.id))
        .filter(Project.tenant_slug.in_(tenant_slugs), _visible_project_filter(Project))
        .group_by(Project.tenant_slug)
        .all()
    )
    return {slug: count for slug, count in rows}


def get_featured_creators(limit: int = 6) -> list[dict]:
    """
    Featured = active tenant + is_available profile, most recently updated
    first. No dedicated "is_featured" flag exists on Profile yet (Phase 12
    scope — see AUDIT_REPORT.md); recency is the honest proxy available
    today rather than inventing a flag with no admin UI to set it.
    """
    from app.models.tenant_data import Profile

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return []

    profiles = (
        Profile.query.filter(
            Profile.tenant_slug.in_(active_slugs),
            Profile.is_available.is_(True),
            Profile.name != "",
        )
        .order_by(Profile.updated_at.desc())
        .limit(limit)
        .all()
    )
    counts = _project_counts([p.tenant_slug for p in profiles])
    return [serialize_creator_card(p, counts.get(p.tenant_slug, 0)) for p in profiles]


def get_latest_creators(limit: int = 12, exclude_slugs: list[str] | None = None) -> list[dict]:
    """Newest active profiles — used by /explore's default (unfiltered) view."""
    from app.models.tenant_data import Profile

    active_slugs = _active_tenant_slugs()
    if exclude_slugs:
        active_slugs -= set(exclude_slugs)
    if not active_slugs:
        return []

    profiles = (
        Profile.query.filter(Profile.tenant_slug.in_(active_slugs), Profile.name != "")
        .order_by(Profile.updated_at.desc())
        .limit(limit)
        .all()
    )
    counts = _project_counts([p.tenant_slug for p in profiles])
    return [serialize_creator_card(p, counts.get(p.tenant_slug, 0)) for p in profiles]


def search_creators(query: str = "", limit: int = 24, offset: int = 0) -> tuple[list[dict], int]:
    """
    Basic ILIKE search over name/title for /explore. Returns (results, total)
    for pagination. Not full-text search — fine for current creator volume;
    revisit with a proper search index (e.g. Postgres tsvector) if/when
    tenant count makes ILIKE scans slow.
    """
    from app.models.tenant_data import Profile
    from sqlalchemy import or_

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return [], 0

    base = Profile.query.filter(Profile.tenant_slug.in_(active_slugs), Profile.name != "")
    if query:
        like = f"%{query.strip()}%"
        base = base.filter(or_(Profile.name.ilike(like), Profile.title.ilike(like)))

    total = base.count()
    profiles = base.order_by(Profile.updated_at.desc()).offset(offset).limit(limit).all()
    counts = _project_counts([p.tenant_slug for p in profiles])
    results = [serialize_creator_card(p, counts.get(p.tenant_slug, 0)) for p in profiles]
    return results, total
