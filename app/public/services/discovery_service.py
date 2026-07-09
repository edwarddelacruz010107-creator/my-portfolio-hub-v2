"""
app/public/services/discovery_service.py — /explore page orchestration.

Thin composition layer over creator_service + feed_service. Exists so
app/public/routes.py stays route-handling only (arg parsing, template
selection) and never touches SQLAlchemy directly, per the source spec's
"no queries in routes" rule.
"""

from __future__ import annotations

from . import creator_service, feed_service

PAGE_SIZE = 24


def explore_page(query: str = "", category: str | None = None, page: int = 1) -> dict:
    """
    Returns everything /explore.html needs to render one page: creator
    results (search-filtered) + category list for the filter UI +
    pagination metadata. Category filtering currently applies to the
    project list, not creators (Profile has no category field) — the
    template should scope the category selector to the projects grid.
    """
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    creators, creator_total = creator_service.search_creators(query=query, limit=PAGE_SIZE, offset=offset)
    projects, project_total = feed_service.get_latest_projects(limit=PAGE_SIZE, offset=offset, category=category)
    categories = feed_service.get_categories()

    return {
        "query": query,
        "category": category,
        "page": page,
        "page_size": PAGE_SIZE,
        "creators": creators,
        "creator_total": creator_total,
        "projects": projects,
        "project_total": project_total,
        "categories": categories,
        "has_next": offset + PAGE_SIZE < max(creator_total, project_total),
        "has_prev": page > 1,
    }
