"""
Portfolio CMS — Theme Engine
============================
Handles theme discovery, registry, validation, and rendering.

v6.3 fixes (productionization pass)
------------------------------------
* The old loader registered every theme's templates/ folder as a flat
  FileSystemLoader and relied on first-match order. Since every theme
  ships an `index.html`, this meant the SAME theme always rendered for
  every tenant regardless of `selected_theme` -- themes never actually
  swapped. Replaced with a PrefixLoader keyed by theme id, so
  `themes/<id>/templates/index.html` is addressed unambiguously as
  `"<id>/index.html"`.
* `render()` previously called `render_template(template_name, ...)`
  with an un-namespaced name, and its "fallback" branch re-ran the
  exact same call instead of switching to the default theme. Fixed to
  always render via the resolved theme's prefix, and to genuinely fall
  back to `default/<template_name>` on a render error.
* `theme_id` is validated against a strict whitelist pattern before
  ever touching the filesystem or the Jinja loader, to block path
  traversal via a crafted `selected_theme` value or `?theme=` query.
"""

import json
import os
import re
from typing import Optional

from flask import render_template, current_app

from app.system_plan import has_administrator_access, is_administrator_plan


THEMES_DIR = os.path.join(os.path.dirname(__file__), '..', 'themes')
DEFAULT_THEME = 'default'
FALLBACK_THEME = 'default'

# Curated production theme set.  Only these themes are discoverable,
# previewable, or selectable.  Keeping the allowlist in the engine prevents
# retired theme folders/catalog rows from reappearing after a deploy or sync.
SUPPORTED_THEME_IDS = (
    'default',
    'developer_pro',
    'blockform_brutal',
    'schematic_spec',
    'developer_journal',
    'console_blueprint',
    'terminal_green',
)
SUPPORTED_THEME_ID_SET = frozenset(SUPPORTED_THEME_IDS)

# Theme ids are directory names. Whitelist strictly -- this is the
# single choke point that prevents path traversal regardless of where
# the theme_id originated (DB column, query string, form field).
_VALID_THEME_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')


def is_valid_theme_id(theme_id: Optional[str]) -> bool:
    return bool(theme_id) and bool(_VALID_THEME_ID.match(theme_id))


def is_supported_theme_id(theme_id: Optional[str]) -> bool:
    """Return True only for an installed, product-supported theme id."""
    return bool(theme_id) and str(theme_id).strip().lower() in SUPPORTED_THEME_ID_SET


import time as _time  # used by ThemeRegistry TTL cache

_CACHE_TTL = 60  # seconds — cache entries expire after this; guards against
                 # cross-worker staleness (clear_cache() only evicts in one worker)


class ThemeRegistry:
    """Discovers and caches all installed themes.

    Cache design:
        _cache stores {theme_id: (meta_dict, expires_at)} tuples.
        Entries expire after _CACHE_TTL seconds so that a SuperAdmin
        change propagated via clear_cache() in one Gunicorn worker will
        automatically reflect in all other workers within one TTL window.
    """

    def __init__(self, themes_dir: str):
        self.themes_dir = os.path.abspath(themes_dir)
        self._cache: dict = {}  # {theme_id: (meta | None, expires_at)}

    def _apply_catalog_override(self, meta: dict, theme_id: str) -> dict:
        """
        Overlay a SuperAdmin-managed ThemeCatalogEntry row (if one exists)
        on top of the theme.json metadata. theme.json stays the source of
        truth for anything the catalog row doesn't explicitly override --
        this keeps the file-based engine fully functional even before any
        migration/sync has run.
        """
        try:
            from app.models.core import ThemeCatalogEntry
            entry = ThemeCatalogEntry.get_by_slug(theme_id)
        except Exception:
            entry = None  # table may not exist yet / outside app context -- degrade gracefully

        meta['catalog_active'] = True
        meta['catalog_entry_id'] = None
        meta['thumbnail_url']   = None
        meta['banner_url']      = None
        meta['preview_images']  = []
        meta['theme_author']    = None
        meta['theme_version']   = None
        meta['theme_tags']      = []
        meta['feature_matrix']  = {}
        meta['is_featured']     = False
        meta['install_count']   = 0
        if entry:
            meta['catalog_entry_id'] = entry.id
            if entry.name:
                meta['name'] = entry.name
            if entry.description:
                meta['description'] = entry.description
            if entry.category:
                meta['category'] = entry.category
            if entry.is_premium is not None:
                meta['premium'] = entry.is_premium
            if entry.required_plan:
                meta['required_plan'] = entry.required_plan
            meta['sort_order']    = entry.sort_order or 0
            meta['catalog_active'] = bool(entry.is_active)
            try:
                meta['thumbnail_url']  = entry.thumbnail_url
                meta['banner_url']     = entry.banner_url
                meta['preview_images'] = entry.get_preview_images()
                meta['theme_author']   = entry.theme_author
                meta['theme_version']  = entry.theme_version
                meta['theme_tags']     = entry.get_tags()
                meta['feature_matrix'] = entry.get_feature_matrix()
                meta['is_featured']    = bool(entry.is_featured)
                meta['install_count']  = entry.install_count or 0
            except Exception:
                pass
        else:
            meta.setdefault('sort_order', 0)
        return meta

    def _load_theme_meta(self, theme_id: str) -> Optional[dict]:
        if not is_valid_theme_id(theme_id) or not is_supported_theme_id(theme_id):
            return None
        meta_path = os.path.join(self.themes_dir, theme_id, 'theme.json')
        if not os.path.isfile(meta_path):
            return None
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            meta['id'] = theme_id  # always authoritative, never trust the file
            meta = self._apply_catalog_override(meta, theme_id)
            return meta
        except (json.JSONDecodeError, OSError):
            current_app.logger.warning('Theme %s has invalid/corrupted theme.json', theme_id)
            return None

    def all(self, include_inactive: bool = False) -> list:
        """Return all installed, renderable themes sorted: free first, then premium.

        include_inactive=True is used by the SuperAdmin theme management
        panel, which needs to show themes a SuperAdmin has deactivated.
        Tenant-facing listings should keep the default (False).

        Results are written through to the per-theme TTL cache so subsequent
        get() calls for individual themes (e.g. resolve_theme) avoid redundant
        DB hits within the same request cycle.
        """
        themes = []
        if not os.path.isdir(self.themes_dir):
            return themes
        for dir_entry in os.scandir(self.themes_dir):
            if (dir_entry.is_dir() and not dir_entry.name.startswith('_')
                    and is_valid_theme_id(dir_entry.name) and is_supported_theme_id(dir_entry.name)):
                if not self.exists(dir_entry.name):
                    continue  # metadata without templates isn't installed -- skip from listings
                meta = self._load_theme_meta(dir_entry.name)
                # Write through to TTL cache — avoids repeat DB hit in get()
                self._cache[dir_entry.name] = (meta, _time.monotonic() + _CACHE_TTL)
                if meta and (include_inactive or meta.get('catalog_active', True)):
                    themes.append(meta)
        themes.sort(key=lambda t: (t.get('sort_order', 0), t.get('premium', False), t.get('name', '')))
        return themes

    def get(self, theme_id: str) -> Optional[dict]:
        if not is_valid_theme_id(theme_id) or not is_supported_theme_id(theme_id):
            return None
        cached = self._cache.get(theme_id)
        if cached is not None:
            meta, expires_at = cached
            if _time.monotonic() < expires_at:
                return meta
            # Entry expired — evict and reload
            del self._cache[theme_id]
        meta = self._load_theme_meta(theme_id)
        self._cache[theme_id] = (meta, _time.monotonic() + _CACHE_TTL)
        return meta

    def exists(self, theme_id: str) -> bool:
        """A theme 'exists' only if it has BOTH metadata and a templates/index.html."""
        if not is_valid_theme_id(theme_id) or not is_supported_theme_id(theme_id):
            return False
        template_dir = os.path.join(self.themes_dir, theme_id, 'templates')
        index_file = os.path.join(template_dir, 'index.html')
        return os.path.isdir(template_dir) and os.path.isfile(index_file)

    def clear_cache(self):
        """Evict all cached entries. Call after any ThemeCatalogEntry mutation."""
        self._cache.clear()


class ThemeEngine:
    """
    Core theme engine. Attach to Flask app at startup.

    Resolves the active theme for a tenant and renders Jinja templates
    from the correct, isolated theme directory.
    """

    def __init__(self, app=None):
        self.registry = ThemeRegistry(THEMES_DIR)
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Register a per-theme PrefixLoader alongside the app's existing loader."""
        app.jinja_loader = self._build_loader(app)
        app.extensions['theme_engine'] = self

    def _build_loader(self, app):
        """
        Composite Jinja loader:
          themes/<id>/templates/*  -> addressed as "<id>/*.html"  (PrefixLoader,
                                       fully isolated -- no collisions between themes)
          everything else          -> original app.jinja_loader (admin/auth/base
                                       templates, completely unaffected)
        """
        from jinja2 import ChoiceLoader, PrefixLoader, FileSystemLoader

        theme_loaders = {}
        if os.path.isdir(THEMES_DIR):
            for theme_dir in os.scandir(THEMES_DIR):
                if (theme_dir.is_dir() and is_valid_theme_id(theme_dir.name)
                        and is_supported_theme_id(theme_dir.name)):
                    tpl = os.path.join(theme_dir.path, 'templates')
                    if os.path.isdir(tpl):
                        theme_loaders[theme_dir.name] = FileSystemLoader(tpl)

        return ChoiceLoader([
            PrefixLoader(theme_loaders, delimiter='/'),
            app.jinja_loader,
        ])

    # ── Public API ────────────────────────────

    # Plan rank used to evaluate ThemeCatalogEntry.required_plan, independent
    # of the broader SUBSCRIPTION_PLAN_ORDER used elsewhere -- theme gating
    # only ever cares about this 3-tier ladder.
    _PLAN_RANK = {'free': 0, 'basic': 0, 'starter': 0, 'trial': 0, 'pro': 1, 'premium': 1, 'enterprise': 2, 'business': 2, 'agency': 2, 'administrator': 99, 'admin': 99}

    def _effective_plan_for_theme_access(self, tenant_profile) -> str:
        """Resolve the current plan from the same source used by admin gates."""
        if callable(getattr(tenant_profile, 'effective_plan', None)):
            return tenant_profile.effective_plan()
        return getattr(tenant_profile, 'plan', None) or 'free'

    def _plan_features_for_theme_access(self, tenant_profile, plan: str) -> dict:
        """Return editable plan settings even for objects without plan_features()."""
        if callable(getattr(tenant_profile, 'plan_features', None)):
            try:
                features = tenant_profile.plan_features() or {}
                if features:
                    return features
            except Exception:
                pass
        try:
            from app.models.core import get_plan_features
            return get_plan_features(plan) or {}
        except Exception:
            return {}

    def _theme_allowed_by_plan(self, tenant_profile, theme_id: str, meta: dict) -> bool:
        """Central plan gate used by picker, apply route, preview, and renderer."""
        if not meta:
            return False
        if not meta.get('catalog_active', True):
            return False
        if getattr(tenant_profile, 'is_administrator', False) or has_administrator_access(tenant_profile):
            return True

        plan = self._effective_plan_for_theme_access(tenant_profile)
        features = self._plan_features_for_theme_access(tenant_profile, plan)

        if theme_id != DEFAULT_THEME and not bool(features.get('theme_customization', False)):
            return False
        if meta.get('premium', False) and not bool(features.get('premium_themes', False)):
            return False

        required_plan = meta.get('required_plan')
        if required_plan:
            return self._plan_meets_requirement(plan, required_plan)

        return True

    def _plan_meets_requirement(self, plan: str, required_plan: Optional[str]) -> bool:
        if is_administrator_plan(plan):
            return True
        if not required_plan:
            return True
        have = self._PLAN_RANK.get(str(plan).lower(), 0)
        need = self._PLAN_RANK.get(str(required_plan).lower(), 0)
        return have >= need

    def _tenant_theme_features(self, tenant_profile) -> dict:
        """SuperAdmin-editable feature flags (theme_customization, premium_themes)
        for the tenant's current effective plan. Empty dict if unavailable --
        callers fail open to legacy plan-rank-only behaviour in that case."""
        if not callable(getattr(tenant_profile, 'plan_features', None)):
            return {}
        try:
            return tenant_profile.plan_features() or {}
        except Exception:
            return {}

    def _theme_access_allowed(self, tenant_profile, meta: dict) -> bool:
        """
        Single source of truth for "can this tenant use this theme". Consumed
        by both resolve_theme() (render path) and can_use_theme() (picker/UI
        path) so they can never disagree.

        Rules (evaluated in order):
          1. Administrators / system tenants        -> always allowed.
          2. `theme_customization` OFF for the plan  -> locked to default
             theme entirely (non-default theme_id denied outright).
          3. `premium_themes` ON for the plan        -> OVERRIDES both the
             legacy `premium` boolean AND any catalog `required_plan`. This
             is the control point for granting Trial (or any plan) access
             to Pro/Enterprise-tier themes via the SuperAdmin Pricing CMS,
             without touching the plan-rank table used elsewhere.
          4. Catalog `required_plan` set              -> gated by plan rank
             (Trial < Basic < Pro < Enterprise < Administrator).
          5. Legacy `premium` boolean, no required_plan -> gated to Pro+.
          6. Free theme                                -> always allowed.
        """
        if getattr(tenant_profile, 'is_administrator', False) or has_administrator_access(tenant_profile):
            return True

        theme_id = meta.get('id')
        features = self._tenant_theme_features(tenant_profile)

        if theme_id != DEFAULT_THEME and features and not features.get('theme_customization', False):
            return False

        if features and bool(features.get('premium_themes', False)):
            return True

        # Use effective_plan() so subscription-based upgrades (e.g. tenant upgraded
        # to PRO via billing while profile.plan column is still 'Basic') are honoured.
        if callable(getattr(tenant_profile, 'effective_plan', None)):
            plan = tenant_profile.effective_plan()
        else:
            plan = (getattr(tenant_profile, 'plan', None) or 'free')

        required_plan = meta.get('required_plan')
        if required_plan:
            return self._plan_meets_requirement(plan, required_plan)

        if meta.get('premium', False):
            return str(plan).lower() in ('pro', 'premium', 'enterprise', 'agency')

        return True

    def resolve_theme(self, tenant_profile) -> str:
        """
        Determine the active theme for a tenant's profile.

        Rules:
          - Administrators        -> always honoured (no restriction)
          - Access gating         -> delegated to _theme_access_allowed(),
                                      shared with can_use_theme() so the
                                      picker UI and the render path never
                                      disagree on what a tenant can see.
          - Deactivated theme (SuperAdmin) -> DEFAULT_THEME for everyone
          - Missing/invalid/corrupted theme -> DEFAULT_THEME
        """
        requested = getattr(tenant_profile, 'selected_theme', None) or DEFAULT_THEME

        if not is_valid_theme_id(requested) or not self.registry.exists(requested):
            return FALLBACK_THEME

        meta = self.registry.get(requested)
        if not meta:
            return FALLBACK_THEME

        if not meta.get('catalog_active', True):
            # SuperAdmin deactivated this theme platform-wide.
            return FALLBACK_THEME

        return requested if self._theme_access_allowed(tenant_profile, meta) else FALLBACK_THEME

    def render(self, tenant_profile, template_name: str, **context) -> str:
        """
        Render a theme template for a tenant's profile.

        Resolves the correct theme, injects theme metadata, then renders
        `themes/<theme_id>/templates/<template_name>` via the isolated
        PrefixLoader namespace. On any rendering error, falls back to
        the default theme instead of 500ing the whole portfolio page.
        """
        theme_id = self.resolve_theme(tenant_profile)
        meta = self.registry.get(theme_id) or {}

        context.setdefault('active_theme', meta)
        context.setdefault('theme_id', theme_id)

        try:
            return render_template(f'{theme_id}/{template_name}', **context)
        except Exception:
            current_app.logger.exception(
                'Theme "%s" failed to render %s -- falling back to default theme',
                theme_id, template_name,
            )
            if theme_id == FALLBACK_THEME:
                raise  # default theme itself is broken -- nothing left to fall back to
            fallback_meta = self.registry.get(FALLBACK_THEME) or {}
            context['active_theme'] = fallback_meta
            context['theme_id'] = FALLBACK_THEME
            return render_template(f'{FALLBACK_THEME}/{template_name}', **context)

    def get_all_themes(self, include_inactive: bool = False) -> list:
        return self.registry.all(include_inactive=include_inactive)

    def get_theme_meta(self, theme_id: str) -> Optional[dict]:
        return self.registry.get(theme_id)

    def clear_cache(self) -> None:
        """Invalidate the theme metadata cache (call after editing a ThemeCatalogEntry)."""
        self.registry.clear_cache()

    def can_use_theme(self, tenant_profile, theme_id: str) -> bool:
        meta = self.registry.get(theme_id)
        if not meta:
            return False
        if not meta.get('catalog_active', True):
            return False
        return self._theme_access_allowed(tenant_profile, meta)


def get_theme_engine() -> ThemeEngine:
    return current_app.extensions['theme_engine']
