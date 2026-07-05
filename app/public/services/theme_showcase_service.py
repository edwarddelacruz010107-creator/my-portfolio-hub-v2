"""
app/public/services/theme_showcase_service.py

Data provider for the public landing page "Themes" section.

ARCHITECTURE DECISION -- do NOT create a second theme registry:
    The platform already has a real, production theme registry:
    `app/theme_engine.py::ThemeRegistry`, backed by `themes/<id>/theme.json`
    on disk and overlaid with `ThemeCatalogEntry` rows (superadmin-managed
    name/description/category/premium flag/preview images/sort order --
    see migrations 0034/0035). It already powers the tenant-facing theme
    picker (app/admin/routes/profile_appearance.py::themes_index) and the
    superadmin catalog manager (app/superadmin/themes.py).

    Introducing a parallel `THEME_REGISTRY` list in app/themes/registry.py,
    as a naive implementation might do, would create a second source of
    truth that drifts from the one tenants actually use -- the exact
    "static/fake card" problem this task exists to eliminate, just moved
    one layer down. Instead this module is a thin, public-safe adapter
    over the existing engine: same data tenants pick from, filtered and
    shaped for anonymous marketing-page consumption.

SECURITY:
    - Only plain dict/str/bool/int values are returned -- never a raw
      ThemeCatalogEntry/model instance (avoids accidental attribute/ORM
      leakage into a Jinja template rendered for anonymous traffic).
    - Premium-gated themes ARE included here (prospects should be able to
      see -- and want -- the premium themes); gating is enforced at
      *application* time (ThemeEngine.can_use_theme), not at *preview*
      time. The public preview route renders read-only sample content,
      so showing a premium theme's look to a logged-out visitor carries
      no privilege-escalation risk.
    - Deactivated (SuperAdmin `is_active=False`) themes are excluded --
      `ThemeRegistry.all()` already filters these out by default.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Public-safe projection -- never leak internal engine-only fields
# (feature_matrix, catalog_entry_id, install_count, etc.) into the
# anonymous marketing page.
_PUBLIC_FIELDS = (
    "id",
    "name",
    "description",
    "tagline",
    "tags",
    "premium",
    "category",
    "preview_image",
    "thumbnail_url",
    "supports_dark_mode",
    "supports_light_mode",
)


def _project(meta: dict) -> dict:
    """Shape one ThemeRegistry meta dict into a public-safe card model."""
    color_scheme = meta.get("color_scheme") or {}
    tags = meta.get("theme_tags") or meta.get("tags") or []
    # theme.json ships 'description' as a full sentence; the landing card
    # wants a short tagline -- derive one from the first 1-2 tags if the
    # SuperAdmin catalog hasn't set an explicit tagline-style description.
    tagline = " · ".join(t.strip().title() for t in tags[:2]) if tags else ""

    return {
        "id": meta.get("id"),
        "name": meta.get("name") or meta.get("id", "").title(),
        "description": meta.get("description") or "",
        "tagline": tagline,
        "tags": tags,
        "premium": bool(meta.get("premium", False)),
        "category": meta.get("category") or "",
        "preview_image": meta.get("thumbnail_url") or meta.get("preview_image") or "",
        "screenshot_images": meta.get("preview_images") or [],
        "supports_dark_mode": bool(meta.get("supports_dark_mode", True)),
        "supports_light_mode": bool(meta.get("supports_light_mode", False)),
        "primary_color": color_scheme.get("primary") or "#7C5CFF",
        "secondary_color": color_scheme.get("secondary") or "#22D3EE",
        "background_color": color_scheme.get("background") or "#0D0F16",
        "text_color": color_scheme.get("text") or "#F6F7FA",
        "is_featured": bool(meta.get("is_featured", False)),
        "sort_order": meta.get("sort_order", 0),
    }


def get_showcase_themes(limit: Optional[int] = 6) -> dict:
    """
    Return themes for the public landing page showcase, sourced live from
    the real ThemeEngine registry (theme.json + ThemeCatalogEntry overlay).

    Returns:
        {
            "themes": [ <public-safe theme dict>, ... ],   # capped to `limit`
            "total": <int total active/installed theme count>,
            "has_more": <bool>,
        }

    Never raises -- degrades to an empty list (template renders its
    empty-state) rather than 500ing the public homepage on a theme
    filesystem/DB hiccup.
    """
    try:
        from app.theme_engine import get_theme_engine

        engine = get_theme_engine()
        all_meta = engine.get_all_themes(include_inactive=False)
    except Exception:
        logger.exception("get_showcase_themes: failed to load theme registry")
        return {"themes": [], "total": 0, "has_more": False}

    # Featured themes first (SuperAdmin-curated), then registry's own
    # sort_order/premium/name ordering (already applied by ThemeRegistry.all()).
    ordered = sorted(all_meta, key=lambda m: (not m.get("is_featured", False),))
    projected = [_project(m) for m in ordered]

    total = len(projected)
    shown = projected[:limit] if limit else projected
    return {
        "themes": shown,
        "total": total,
        "has_more": total > len(shown),
    }


def get_theme_detail(theme_id: str) -> Optional[dict]:
    """Public-safe single-theme lookup, used by the preview route."""
    try:
        from app.theme_engine import get_theme_engine, is_valid_theme_id

        if not is_valid_theme_id(theme_id):
            return None
        engine = get_theme_engine()
        meta = engine.get_theme_meta(theme_id)
        if not meta or not meta.get("catalog_active", True):
            return None
        return _project(meta)
    except Exception:
        logger.exception("get_theme_detail failed for theme_id=%s", theme_id)
        return None
