"""
app/public/services/landing_service.py

Data providers for the new Landing2 homepage. All queries here are
cross-tenant and public-safe — they never return raw model instances,
only plain dicts + primitives.

Add to the public services package: it plugs into render_landing_page()
in app/public/routes.py to power the stats bar, founder card, and
community feed on the new landing template (templates/public/index.html).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import func

from app.models.core import PlatformSetting, Tenant  # active-tenant scoping
from app.models.tenant_data import Profile, Project  # public-safe fields only

from .serializers import serialize_creator_card

logger = logging.getLogger(__name__)


def _visible_project_filter(Project):
    """Public project visibility for landing counters/cards.

    Normal tenants expose only Published projects. The protected default
    administrator portfolio also exposes Featured Draft projects, matching
    /administrator-portfolio and avoiding mismatched landing counts.
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


def _active_slugs() -> list[str]:
    return [
        t.slug
        for t in Tenant.query.filter(Tenant.status == "active")
        .with_entities(Tenant.slug)
        .all()
    ]


def get_landing_stats() -> dict:
    """
    Real counters for the stats bar. Falls back to sensible defaults on
    any DB error so the homepage never breaks because of a stats query.
    """
    try:
        slugs = _active_slugs()
        if not slugs:
            return {}
        portfolios = (
            Profile.query.filter(Profile.tenant_slug.in_(slugs)).count()
        )
        projects = (
            Project.query.filter(
                Project.tenant_slug.in_(slugs),
                _visible_project_filter(Project),
            ).count()
        )
        # Skills / certificates: totals across public profiles.
        # Wrapped in try because these columns may not exist on older
        # deployments; the template already has data-count fallbacks.
        skills = 0
        certificates = 0
        try:
            from app.models.tenant_data import Skill  # type: ignore

            skills = Skill.query.filter(Skill.tenant_slug.in_(slugs)).count()
        except Exception:
            pass
        try:
            from app.models.tenant_data import Certificate  # type: ignore

            certificates = Certificate.query.filter(
                Certificate.tenant_slug.in_(slugs)
            ).count()
        except Exception:
            pass
        return {
            "portfolios": portfolios,
            "projects": projects,
            "skills": skills,
            "certificates": certificates,
        }
    except Exception:  # pragma: no cover — homepage must never 500
        logger.exception("landing stats query failed; using template defaults")
        return {}


def get_community_stats(avatar_limit: int = 4) -> dict:
    """
    Real cross-tenant numbers for the hero's social-proof row ("Joined by
    N+ professionals across M+ countries") and its avatar stack — replaces
    the old hardcoded "1,500+ ... 40+ countries" copy and fake pravatar.cc
    images with actual signed-up creators.

    country_count is a best-effort heuristic: Profile.location is a free
    text field (e.g. "Nairobi, Kenya"), so we take the last comma-separated
    segment as a proxy for country and count distinct values. It will
    undercount if creators only enter a city, but it never fabricates a
    number that isn't backed by real profile data.
    """
    try:
        slugs = _active_slugs()
        if not slugs:
            return {"member_count": 0, "country_count": 0, "avatars": []}

        base = Profile.query.filter(
            Profile.tenant_slug.in_(slugs),
            Profile.name != "",
        )
        member_count = base.count()

        locations = [
            loc
            for (loc,) in base.filter(
                Profile.location.isnot(None), Profile.location != ""
            )
            .with_entities(Profile.location)
            .all()
        ]
        countries = set()
        for loc in locations:
            segment = loc.split(",")[-1].strip().lower()
            if segment:
                countries.add(segment)
        country_count = len(countries)

        recent = base.order_by(Profile.updated_at.desc()).limit(avatar_limit).all()
        avatars = [
            {
                "name": p.name or p.tenant_slug,
                "image": p.profile_image or "",
                "initial": (p.name or p.tenant_slug or "?").strip()[:1].upper(),
            }
            for p in recent
        ]
        return {
            "member_count": member_count,
            "country_count": country_count,
            "avatars": avatars,
        }
    except Exception:  # pragma: no cover — homepage must never 500
        logger.exception("community stats query failed; using template defaults")
        return {"member_count": 0, "country_count": 0, "avatars": []}


def get_administrator_card() -> Optional[dict]:
    """
    Public-safe card for the platform administrator (tenant_slug='default').
    Returns None if the seed profile is missing so the template renders
    the neutral fallback copy.
    """
    try:
        profile = Profile.query.filter(Profile.tenant_slug == "default").first()
        if not profile:
            return None
        # Reuse the same serializer as creator cards for consistency.
        project_count = (
            Project.query.filter(
                Project.tenant_slug == "default",
                _visible_project_filter(Project),
            ).count()
        )
        return serialize_creator_card(profile, project_count=project_count)
    except Exception:
        logger.exception("administrator card query failed")
        return None


def get_landing_content() -> dict[str, str]:
    """
    Fetch superadmin-managed landing page content from platform settings.
    Returns an empty string for every field when no setting exists.
    """
    try:
        return {
            'hero_badge': PlatformSetting.get_string('landing_hero_badge', default='') or '',
            'hero_title': PlatformSetting.get_string('landing_hero_title', default='') or '',
            'hero_subtitle': PlatformSetting.get_string('landing_hero_subtitle', default='') or '',
            'hero_cta_primary_text': PlatformSetting.get_string('landing_hero_cta_primary_text', default='') or '',
            'hero_cta_primary_url': PlatformSetting.get_string('landing_hero_cta_primary_url', default='') or '',
            'hero_cta_secondary_text': PlatformSetting.get_string('landing_hero_cta_secondary_text', default='') or '',
            'hero_cta_secondary_url': PlatformSetting.get_string('landing_hero_cta_secondary_url', default='') or '',
            'hero_image_url': PlatformSetting.get_string('landing_hero_image_url', default='') or '',
            'hero_image_fit': PlatformSetting.get_string('landing_hero_image_fit', default='cover') or 'cover',
            'hero_image_position_x': PlatformSetting.get_string('landing_hero_image_position_x', default='50') or '50',
            'hero_image_position_y': PlatformSetting.get_string('landing_hero_image_position_y', default='50') or '50',
            'hero_image_zoom': PlatformSetting.get_string('landing_hero_image_zoom', default='100') or '100',
            'hero_preview_name': PlatformSetting.get_string('landing_hero_preview_name', default='') or '',
            'hero_preview_role': PlatformSetting.get_string('landing_hero_preview_role', default='') or '',
            'hero_preview_url_text': PlatformSetting.get_string('landing_hero_preview_url_text', default='') or '',
            'hero_stat_badge_text': PlatformSetting.get_string('landing_hero_stat_badge_text', default='') or '',
            'hero_stat_likes': PlatformSetting.get_string('landing_hero_stat_likes', default='') or '',
            'hero_stat_views': PlatformSetting.get_string('landing_hero_stat_views', default='') or '',
            'hero_stat_comments': PlatformSetting.get_string('landing_hero_stat_comments', default='') or '',
            'hero_enable_widgets': PlatformSetting.get_bool('landing_hero_enable_widgets', default=True),
            'hero_enable_animation': PlatformSetting.get_bool('landing_hero_enable_animation', default=True),
            'features_heading': PlatformSetting.get_string('landing_features_heading', default='') or '',
            'features_subtitle': PlatformSetting.get_string('landing_features_subtitle', default='') or '',
            'contact_heading': PlatformSetting.get_string('landing_contact_heading', default='') or '',
            'contact_subtitle': PlatformSetting.get_string('landing_contact_subtitle', default='') or '',
            'contact_receiver_email': PlatformSetting.get_string('landing_contact_receiver_email', default='') or '',
            'contact_email': PlatformSetting.get_string('landing_contact_email', default='') or '',
            'contact_phone': PlatformSetting.get_string('landing_contact_phone', default='') or '',
            'contact_location': PlatformSetting.get_string('landing_contact_location', default='') or '',
            'contact_map_title': PlatformSetting.get_string('landing_contact_map_title', default='') or '',
            'contact_map_note': PlatformSetting.get_string('landing_contact_map_note', default='') or '',
            'contact_x_url': PlatformSetting.get_string('landing_contact_x_url', default='') or '',
            'contact_linkedin_url': PlatformSetting.get_string('landing_contact_linkedin_url', default='') or '',
            'contact_instagram_url': PlatformSetting.get_string('landing_contact_instagram_url', default='') or '',
            'contact_github_url': PlatformSetting.get_string('landing_contact_github_url', default='') or '',
            'founder_photo_url': PlatformSetting.get_string('landing_founder_photo_url', default='') or '',
            'founder_photo_fit': PlatformSetting.get_string('landing_founder_photo_fit', default='cover') or 'cover',
            'founder_photo_position_x': PlatformSetting.get_string('landing_founder_photo_position_x', default='50') or '50',
            'founder_photo_position_y': PlatformSetting.get_string('landing_founder_photo_position_y', default='50') or '50',
            'founder_photo_zoom': PlatformSetting.get_string('landing_founder_photo_zoom', default='100') or '100',
            'founder_role': PlatformSetting.get_string('landing_founder_role', default='') or '',
            'founder_description': PlatformSetting.get_string('landing_founder_description', default='') or PlatformSetting.get_string('landing_founder_bio', default='') or '',
            'founder_portfolio_url': PlatformSetting.get_string('landing_founder_portfolio_url', default='') or '',
            'founder_contact_url': PlatformSetting.get_string('landing_founder_contact_url', default='') or '',
            'founder_preview_image': PlatformSetting.get_string('landing_founder_preview_image', default='') or '',
            'founder_title': PlatformSetting.get_string('landing_founder_title', default='') or '',
            'founder_name': PlatformSetting.get_string('landing_founder_name', default='') or '',
        }
    except Exception:
        logger.exception('Failed to load landing page content settings')
        return {
            'hero_title': '',
            'hero_subtitle': '',
            'hero_image_url': '',
            'hero_image_fit': 'cover',
            'hero_image_position_x': '50',
            'hero_image_position_y': '50',
            'hero_image_zoom': '100',
            'hero_preview_name': '',
            'hero_preview_role': '',
            'hero_preview_url_text': '',
            'hero_stat_badge_text': '',
            'hero_stat_likes': '',
            'hero_stat_views': '',
            'hero_stat_comments': '',
            'hero_enable_widgets': True,
            'hero_enable_animation': True,
            'contact_heading': '',
            'contact_subtitle': '',
            'contact_receiver_email': '',
            'contact_email': '',
            'contact_phone': '',
            'contact_location': '',
            'contact_map_title': '',
            'contact_map_note': '',
            'contact_x_url': '',
            'contact_linkedin_url': '',
            'contact_instagram_url': '',
            'contact_github_url': '',
            'founder_title': '',
            'founder_name': '',
            'founder_bio': '',
            'founder_photo_fit': 'cover',
            'founder_photo_position_x': '50',
            'founder_photo_position_y': '50',
            'founder_photo_zoom': '100',
        }
