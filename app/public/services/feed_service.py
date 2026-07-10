"""
app/public/services/feed_service.py — Cross-tenant project feed.

Read-only. Powers the landing page ("Trending Projects") and /feed.
Same cross-DB-bind constraint as creator_service.py: Tenant lives in
core_db, Project lives in tenant_db — two queries, stitched in Python.
"""

from __future__ import annotations

import logging

from .serializers import serialize_project_card

logger = logging.getLogger(__name__)


def _visible_project_filter(Project):
    """Public project visibility used by landing/project discovery.

    Normal tenants only expose Published projects. The protected owner/default
    portfolio is special: its portfolio route intentionally allows Featured
    Draft projects so the platform owner can showcase selected in-progress
    work. Landing showcase, community updates, project browser, and creator
    counts must use the same rule or the homepage will say `0 projects` while
    /administrator-portfolio shows a project.
    """
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
    from app.models.core import Tenant

    return {t.slug for t in Tenant.query.filter(Tenant.status == "active").with_entities(Tenant.slug).all()}


def _creator_profiles(tenant_slugs: list[str]) -> dict[str, dict[str, str]]:
    """tenant_slug -> public creator display fields for project cards."""
    from app.models.tenant_data import Profile

    if not tenant_slugs:
        return {}
    rows = (
        Profile.query.with_entities(Profile.tenant_slug, Profile.name, Profile.profile_image)
        .filter(Profile.tenant_slug.in_(tenant_slugs))
        .all()
    )
    return {
        slug: {
            "name": name or slug,
            "profile_image": profile_image or "",
        }
        for slug, name, profile_image in rows
    }


def _creator_names(tenant_slugs: list[str]) -> dict[str, str]:
    """Backward-compatible helper for older call sites."""
    return {slug: data.get("name", slug) for slug, data in _creator_profiles(tenant_slugs).items()}


def get_trending_projects(limit: int = 8, current_user_id: int | None = None) -> list[dict]:
    """
    "Trending" = public-visible projects ranked by featured, views, then recency.
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
            _visible_project_filter(Project),
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

    creators = _creator_profiles([p.tenant_slug for p in projects])
    results = []
    for p in projects:
        p.liked = p.id in liked_ids
        creator = creators.get(p.tenant_slug, {})
        results.append(
            serialize_project_card(
                p,
                creator_name=creator.get("name", p.tenant_slug),
                creator_profile_image=creator.get("profile_image", ""),
            )
        )
    return results


def get_latest_projects(limit: int = 12, offset: int = 0, category: str | None = None, current_user_id: int | None = None) -> tuple[list[dict], int]:
    """Newest public-visible projects across all active tenants, for /feed."""
    from app.models.tenant_data import Project, ProjectReaction

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return [], 0

    base = Project.query.filter(Project.tenant_slug.in_(active_slugs), _visible_project_filter(Project))
    if category:
        base = base.filter(Project.category == category)

    total = base.count()
    projects = base.order_by(Project.created_at.desc()).offset(offset).limit(limit).all()

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

    creators = _creator_profiles([p.tenant_slug for p in projects])
    results = []
    for p in projects:
        p.liked = p.id in liked_ids
        creator = creators.get(p.tenant_slug, {})
        results.append(
            serialize_project_card(
                p,
                creator_name=creator.get("name", p.tenant_slug),
                creator_profile_image=creator.get("profile_image", ""),
            )
        )
    return results, total



def browse_projects(
    *,
    limit: int = 12,
    offset: int = 0,
    query: str | None = None,
    category: str | None = None,
    sort: str = 'latest',
    current_user_id: int | None = None,
) -> tuple[list[dict], int]:
    """Searchable/sortable public project browser for /projects.

    Keeps queries public-safe: only active tenants and public-visible projects are
    returned, then model instances are converted through serialize_project_card.
    """
    from sqlalchemy import or_
    from app.models.tenant_data import Project, ProjectReaction

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return [], 0

    base = Project.query.filter(
        Project.tenant_slug.in_(active_slugs),
        _visible_project_filter(Project),
    )

    q = (query or '').strip()
    if q:
        needle = f"%{q}%"
        base = base.filter(or_(
            Project.title.ilike(needle),
            Project.description_short.ilike(needle),
            Project.description.ilike(needle),
            Project.category.ilike(needle),
            Project.framework.ilike(needle),
            Project.language.ilike(needle),
        ))

    cat = (category or '').strip()
    if cat:
        base = base.filter(Project.category == cat)

    sort_key = (sort or 'latest').strip().lower()
    if sort_key == 'featured':
        ordered = base.order_by(Project.is_featured.desc(), Project.created_at.desc(), Project.id.desc())
    elif sort_key == 'popular':
        ordered = base.order_by(Project.view_count.desc(), Project.created_at.desc(), Project.id.desc())
    elif sort_key == 'liked':
        ordered = base.order_by(Project.like_count.desc(), Project.created_at.desc(), Project.id.desc())
    else:
        sort_key = 'latest'
        ordered = base.order_by(Project.created_at.desc(), Project.id.desc())

    total = ordered.count()
    projects = ordered.offset(offset).limit(limit).all()

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

    creators = _creator_profiles([p.tenant_slug for p in projects])
    results = []
    for p in projects:
        p.liked = p.id in liked_ids
        creator = creators.get(p.tenant_slug, {})
        results.append(
            serialize_project_card(
                p,
                creator_name=creator.get("name", p.tenant_slug),
                creator_profile_image=creator.get("profile_image", ""),
            )
        )
    return results, total


def get_categories() -> list[str]:
    """Distinct categories across public-visible projects, for /explore + /feed filters."""
    from app.models.tenant_data import Project

    active_slugs = _active_tenant_slugs()
    if not active_slugs:
        return []
    rows = (
        Project.query.with_entities(Project.category)
        .filter(Project.tenant_slug.in_(active_slugs), _visible_project_filter(Project), Project.category.isnot(None))
        .distinct()
        .all()
    )
    return sorted({c for (c,) in rows if c})
