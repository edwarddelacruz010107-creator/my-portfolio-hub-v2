"""
app/public/services/serializers.py — Public-safe field allowlists.

Every model → dict conversion exposed to a public template MUST go through
one of these functions. Do not pass Profile/Project/Tenant instances
directly into public/* templates — they carry PII and billing internals
(email, phone, monthly_rate, internal_notes, free_trial_ends, og_image
credentials, etc.) that have no business leaving a tenant-scoped request.

If a template needs a new field, add it here explicitly. Do not widen
these by passing `vars(profile)` or `profile.__dict__` — that defeats
the allowlist entirely.
"""

from __future__ import annotations

from typing import Any


def _portfolio_url(tenant_slug: str) -> str:
    try:
        from app.services.custom_domain_service import tenant_portfolio_public_url
        return tenant_portfolio_public_url(tenant_slug)
    except Exception:
        return f"/u/{tenant_slug}"


def _project_url(tenant_slug: str, project_slug: str) -> str:
    try:
        from app.services.custom_domain_service import tenant_project_public_url
        return tenant_project_public_url(tenant_slug, project_slug)
    except Exception:
        return f"/{tenant_slug}/project/{project_slug}"


def serialize_creator_card(profile, project_count: int = 0) -> dict[str, Any]:
    """Public creator-card fields only. No email/phone/billing internals."""
    return {
        "tenant_slug": profile.tenant_slug,
        "name": profile.name or profile.tenant_slug,
        "title": profile.title or "",
        "profile_image": profile.profile_image or "",
        "bio_short": (profile.bio_short or "")[:180],
        "years_experience": profile.get_years_experience() if hasattr(profile, "get_years_experience") else (profile.years_experience or 0),
        "is_available": bool(profile.is_available),
        "availability_status": profile.availability_status or "",
        "project_count": project_count,
        # 'default' is the platform's reserved, built-in tenant slug — see
        # RESERVED_SLUGS / creator_link() in app/public/routes.py, which
        # already special-cases this exact slug the same way. It's the
        # seeded admin/showcase profile, not a real creator signup, so the
        # public card should read "Administrator" instead of an
        # availability badge that implies it's open for hire.
        "is_administrator": profile.tenant_slug == "default",
        "url": _portfolio_url(profile.tenant_slug),
    }


def serialize_project_card(
    project,
    creator_name: str = "",
    creator_slug: str = "",
    creator_profile_image: str = "",
) -> dict[str, Any]:
    """Public project-card fields only."""
    return {
        "id": project.id,
        "title": project.title,
        "slug": project.slug,
        "description_short": (project.description_short or "")[:200],
        "image": project.image or "",
        "category": project.category or "",
        "tags": list(project.tags or [])[:5],
        "tenant_slug": project.tenant_slug,
        "creator_name": creator_name or project.tenant_slug,
        "creator_slug": creator_slug or project.tenant_slug,
        "creator_profile_image": creator_profile_image or "",
        "is_featured": bool(project.is_featured),
        "like_count": int(getattr(project, 'like_count', 0) or 0),
        "liked": bool(getattr(project, 'liked', False)),
        "view_count": int(getattr(project, 'view_count', 0) or 0),
        "url": _project_url(project.tenant_slug, project.slug),
        "portfolio_url": _portfolio_url(project.tenant_slug),
    }
