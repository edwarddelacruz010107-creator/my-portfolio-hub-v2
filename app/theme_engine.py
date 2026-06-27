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


THEMES_DIR = os.path.join(os.path.dirname(__file__), '..', 'themes')
DEFAULT_THEME = 'default'
FALLBACK_THEME = 'default'

# Theme ids are directory names. Whitelist strictly -- this is the
# single choke point that prevents path traversal regardless of where
# the theme_id originated (DB column, query string, form field).
_VALID_THEME_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')


def is_valid_theme_id(theme_id: Optional[str]) -> bool:
    return bool(theme_id) and bool(_VALID_THEME_ID.match(theme_id))


class ThemeRegistry:
    """Discovers and caches all installed themes."""

    def __init__(self, themes_dir: str):
        self.themes_dir = os.path.abspath(themes_dir)
        self._cache: dict = {}

    def _load_theme_meta(self, theme_id: str) -> Optional[dict]:
        if not is_valid_theme_id(theme_id):
            return None
        meta_path = os.path.join(self.themes_dir, theme_id, 'theme.json')
        if not os.path.isfile(meta_path):
            return None
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            meta['id'] = theme_id  # always authoritative, never trust the file
            return meta
        except (json.JSONDecodeError, OSError):
            current_app.logger.warning('Theme %s has invalid/corrupted theme.json', theme_id)
            return None

    def all(self) -> list:
        """Return all installed, renderable themes sorted: free first, then premium."""
        themes = []
        if not os.path.isdir(self.themes_dir):
            return themes
        for entry in os.scandir(self.themes_dir):
            if entry.is_dir() and not entry.name.startswith('_') and is_valid_theme_id(entry.name):
                if not self.exists(entry.name):
                    continue  # metadata without templates isn't installed -- skip from listings
                meta = self._load_theme_meta(entry.name)
                if meta:
                    themes.append(meta)
        themes.sort(key=lambda t: (t.get('premium', False), t.get('name', '')))
        return themes

    def get(self, theme_id: str) -> Optional[dict]:
        if not is_valid_theme_id(theme_id):
            return None
        if theme_id not in self._cache:
            self._cache[theme_id] = self._load_theme_meta(theme_id)
        return self._cache[theme_id]

    def exists(self, theme_id: str) -> bool:
        """A theme 'exists' only if it has BOTH metadata and a templates/index.html."""
        if not is_valid_theme_id(theme_id):
            return False
        template_dir = os.path.join(self.themes_dir, theme_id, 'templates')
        index_file = os.path.join(template_dir, 'index.html')
        return os.path.isdir(template_dir) and os.path.isfile(index_file)

    def clear_cache(self):
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
                if theme_dir.is_dir() and is_valid_theme_id(theme_dir.name):
                    tpl = os.path.join(theme_dir.path, 'templates')
                    if os.path.isdir(tpl):
                        theme_loaders[theme_dir.name] = FileSystemLoader(tpl)

        return ChoiceLoader([
            PrefixLoader(theme_loaders, delimiter='/'),
            app.jinja_loader,
        ])

    # ── Public API ────────────────────────────

    def resolve_theme(self, tenant_profile) -> str:
        """
        Determine the active theme for a tenant's profile.

        Rules:
          - Administrators        -> always honoured (no restriction)
          - PRO/premium tenants   -> any theme allowed
          - FREE tenants          -> only non-premium themes
          - Missing/invalid/corrupted theme -> DEFAULT_THEME
        """
        requested = getattr(tenant_profile, 'selected_theme', None) or DEFAULT_THEME

        if not is_valid_theme_id(requested) or not self.registry.exists(requested):
            return FALLBACK_THEME

        meta = self.registry.get(requested)
        if not meta:
            return FALLBACK_THEME

        if getattr(tenant_profile, 'is_administrator', False):
            return requested

        # Use effective_plan() so subscription-based upgrades (e.g. tenant upgraded
        # to PRO via billing while profile.plan column is still 'Basic') are honoured.
        if callable(getattr(tenant_profile, 'effective_plan', None)):
            plan = tenant_profile.effective_plan()
        else:
            plan = (getattr(tenant_profile, 'plan', None) or 'free')

        if str(plan).lower() in ('pro', 'premium', 'enterprise', 'agency'):
            return requested

        if meta.get('premium', False):
            return FALLBACK_THEME

        return requested

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

    def get_all_themes(self) -> list:
        return self.registry.all()

    def get_theme_meta(self, theme_id: str) -> Optional[dict]:
        return self.registry.get(theme_id)

    def can_use_theme(self, tenant_profile, theme_id: str) -> bool:
        meta = self.registry.get(theme_id)
        if not meta:
            return False
        if getattr(tenant_profile, 'is_administrator', False):
            return True
        # Use effective_plan() so subscription upgrades are reflected immediately.
        if callable(getattr(tenant_profile, 'effective_plan', None)):
            plan = tenant_profile.effective_plan()
        else:
            plan = (getattr(tenant_profile, 'plan', None) or 'free')
        if str(plan).lower() in ('pro', 'premium', 'enterprise', 'agency'):
            return True
        return not meta.get('premium', False)


def get_theme_engine() -> ThemeEngine:
    return current_app.extensions['theme_engine']
