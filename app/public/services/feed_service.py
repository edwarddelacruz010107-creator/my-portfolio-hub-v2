"""
app/public/services/feed_service.py — Cross-tenant project feed.

Read-only. Powers the landing page ("Trending Projects") and /projects (/feed legacy alias).
Same cross-DB-bind constraint as creator_service.py: Tenant lives in
core_db, Project lives in tenant_db — two queries, stitched in Python.
"""

from __future__ import annotations

import logging

from .serializers import serialize_project_card

logger = logging.getLogger(__name__)


def _active_tenant_slugs() -> set[str]:
    from app.models.core import Tenant

    return {t.slug for t in Tenant.query.filter(Tenant.status == "active").with_entities(Tenant.slug).all()}


def _creator_names(tenant_slugs: list[str]) -> dict[str, str]:
    """tenant_slug -> display name, for stamping creator_name on project cards."""
    from app.models.tenant_data import Profile

    if not tenant_slugs:
        return {}
    rows = (
        Profile.query.with_entities(Profile.tenant_slug, Profile.name)
        .filter(Profile.tenant_slug.in_(tenant_slugs))
        .all()
    )
    return {slug: (name or slug) for slug, name in rows}


def get_trending_projects(limit: int = 8, current_user_id: int | None = None) -> list[dict]:
    """
    "Trending" = published + featured, ranked by view_count then recency.
    No time-decay/velocity model yet (that needs a views-over-time table —
    Phase 12 social-foundation scope, not this phase). view_count is a
    monotonic counter today, so this is "most-viewed", not "trending this
    week" — documented as a known simplification in AUDIT_REPORT.md.
    """
    from app.models.tenant_data import Project, ProjectReaction

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return []

    projects = (
        Project.query.filter(
            Project.tenant_slug.in_(active_slugs),
            Project.status == "published",
        )
        .order_by(Project.is_featured.desc(), Project.view_count.desc(), Project.created_at.desc())
        .limit(limit)
        .all()
    )

    liked_ids = set()
    if current_user_id and projects:
        liked_ids = {
            row[0]
            for row in ProjectReaction.query.with_entities(ProjectReaction.project_id)
            .filter(
                ProjectReaction.user_id == current_user_id,
                ProjectReaction.project_id.in_([p.id for p in projects]),
            )
            .all()
        }

    names = _creator_names([p.tenant_slug for p in projects])
    results = []
    for p in projects:
        p.liked = p.id in liked_ids
        results.append(serialize_project_card(p, creator_name=names.get(p.tenant_slug, p.tenant_slug)))
    return results


def get_latest_projects(
    limit: int = 12,
    offset: int = 0,
    category: str | None = None,
    current_user_id: int | None = None,
    query: str | None = None,
    sort: str = "latest",
) -> tuple[list[dict], int]:
    """Browse/search newest published projects across all active tenants."""
    from sqlalchemy import or_
    from app.models.tenant_data import Project, ProjectReaction

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return [], 0

    base = Project.query.filter(Project.tenant_slug.in_(active_slugs), Project.status == "published")
    if category:
        base = base.filter(Project.category == category)

    cleaned_query = (query or "").strip()
    if cleaned_query:
        like = f"%{cleaned_query}%"
        base = base.filter(or_(
            Project.title.ilike(like),
            Project.description_short.ilike(like),
            Project.description.ilike(like),
            Project.category.ilike(like),
            Project.framework.ilike(like),
            Project.language.ilike(like),
        ))

    total = base.count()
    sort_key = (sort or "latest").lower()
    if sort_key == "popular":
        order_by = (Project.view_count.desc(), Project.like_count.desc(), Project.created_at.desc())
    elif sort_key == "liked":
        order_by = (Project.like_count.desc(), Project.view_count.desc(), Project.created_at.desc())
    elif sort_key == "featured":
        order_by = (Project.is_featured.desc(), Project.order.asc(), Project.created_at.desc())
    else:
        order_by = (Project.created_at.desc(),)

    projects = base.order_by(*order_by).offset(offset).limit(limit).all()

    liked_ids = set()
    if current_user_id and projects:
        liked_ids = {
            row[0]
            for row in ProjectReaction.query.with_entities(ProjectReaction.project_id)
            .filter(
                ProjectReaction.user_id == current_user_id,
                ProjectReaction.project_id.in_([p.id for p in projects]),
            )
            .all()
        }

    names = _creator_names([p.tenant_slug for p in projects])
    results = []
    for p in projects:
        p.liked = p.id in liked_ids
        results.append(serialize_project_card(p, creator_name=names.get(p.tenant_slug, p.tenant_slug)))
    return results, total


def get_categories() -> list[str]:
    """Distinct categories across published projects, for /explore + /feed filters."""
    from app.models.tenant_data import Project

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return []
    rows = (
        Project.query.with_entities(Project.category)
        .filter(Project.tenant_slug.in_(active_slugs), Project.status == "published", Project.category.isnot(None))
        .distinct()
        .all()
    )
    return sorted({c for (c,) in rows if c})
