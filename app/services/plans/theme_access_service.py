"""
app/services/plans/theme_access_service.py
──────────────────────────────────────────────────────────────────────────────
Theme Access Service — the single gatekeeper for all theme entitlement checks.

ThemeEngine previously embedded plan comparisons using a local _PLAN_RANK dict
that had no 'administrator' key and a hardcoded list of plan strings.  This
produced two bugs for Administrator tenants:
    1. is_administrator check used getattr(profile, 'is_administrator', False)
       which is False on Profile objects (no such attribute).
    2. _PLAN_RANK.get('administrator', 0) returned 0 → ranked as Free tier.

This service fixes both by routing all theme access decisions through the
canonical plan_hierarchy module.

Usage:
    from app.services.plans.theme_access_service import ThemeAccessService

    # Boolean check (use in apply_theme route, preview, etc.)
    if not ThemeAccessService.can_access_theme(profile, theme_meta):
        abort(403)

    # Annotate theme list with access flags (use in themes_index route)
    themes = ThemeAccessService.annotate_theme_list(profile, all_themes)

    # Theme resolution (use in ThemeEngine.resolve_theme)
    theme_id = ThemeAccessService.resolve_active_theme(profile, requested_id, all_meta)
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.plans.plan_hierarchy import (
    has_plan_access,
    is_administrator,
    get_effective_plan,
    normalize_plan,
    PLAN_PRO,
    PLAN_ENTERPRISE,
    PLAN_ADMINISTRATOR,
)

logger = logging.getLogger(__name__)

# Default fallback theme id when access is denied
_FALLBACK_THEME = "default"

# Theme flag types supported in metadata
_THEME_FLAG_HIDDEN   = "hidden"
_THEME_FLAG_INTERNAL = "internal"
_THEME_FLAG_BETA     = "beta"

# Known special theme categories — administrator always passes all of them
_SPECIAL_FLAGS = {_THEME_FLAG_HIDDEN, _THEME_FLAG_INTERNAL, _THEME_FLAG_BETA}


class ThemeAccessService:
    """
    Centralized theme entitlement authority.
    No other code may perform theme access checks — all must call here.
    """

    @staticmethod
    def can_access_theme(tenant_obj, theme_meta: dict) -> bool:
        """
        Core theme access check.

        Rules (evaluated in order):
          1. If catalog_active is False (SuperAdmin deactivated) → deny EVERYONE
             except Administrator (admins can still preview deactivated themes).
          2. Administrator plan → ALWAYS grant access to everything.
          3. If theme has required_plan → use plan_hierarchy comparison.
          4. If theme is marked premium=True → require Pro or higher.
          5. Hidden / internal / beta themes → require Enterprise or higher
             (Administrators bypass this too via rule 2).
          6. Default: allow.

        Args:
            tenant_obj: Tenant ORM, Profile ORM, dict, str, or None
            theme_meta: dict from theme.json / ThemeRegistry.get(theme_id)

        Returns:
            True if the tenant may use this theme, False otherwise.
        """
        if not theme_meta:
            return False

        plan   = get_effective_plan(tenant_obj)
        _admin = is_administrator(plan)

        # ── Rule 1: Catalog deactivation (superadmin kill-switch) ─────────────
        # Administrator tenants are STILL allowed to access deactivated themes
        # so the platform owner is never locked out of their own themes.
        catalog_active = theme_meta.get("catalog_active", True)
        if not catalog_active and not _admin:
            return False

        # ── Rule 2: Administrator bypass ──────────────────────────────────────
        if _admin:
            return True

        # ── Rule 3: Explicit required_plan from catalog / theme.json ──────────
        required_plan = theme_meta.get("required_plan")
        if required_plan:
            ok = has_plan_access(plan, required_plan)
            logger.debug(
                "[ThemeAccess] plan=%r required_plan=%r access=%s theme=%r",
                plan, required_plan, ok, theme_meta.get("id"),
            )
            return ok

        # ── Rule 4: Legacy premium flag ───────────────────────────────────────
        if theme_meta.get("premium", False):
            return has_plan_access(plan, PLAN_PRO)

        # ── Rule 5: Special flags (hidden, internal, beta) ────────────────────
        category = (theme_meta.get("category") or "").strip().lower()
        if category in _SPECIAL_FLAGS:
            return has_plan_access(plan, PLAN_ENTERPRISE)

        # ── Rule 6: Unrestricted ──────────────────────────────────────────────
        return True

    @staticmethod
    def resolve_active_theme(
        tenant_obj,
        requested_theme_id: Optional[str],
        theme_meta: Optional[dict],
    ) -> str:
        """
        Determine the theme to render for a tenant.

        Returns the requested theme_id if the tenant is entitled to it,
        otherwise _FALLBACK_THEME.

        This replaces ThemeEngine.resolve_theme()'s inline plan logic.

        Args:
            tenant_obj:        Tenant / Profile
            requested_theme_id: The theme id the tenant has selected
            theme_meta:        The metadata dict for requested_theme_id
        """
        if not requested_theme_id or not theme_meta:
            return _FALLBACK_THEME

        if not ThemeAccessService.can_access_theme(tenant_obj, theme_meta):
            logger.info(
                "[ThemeAccess] Tenant plan=%r denied theme=%r — falling back to default",
                get_effective_plan(tenant_obj), requested_theme_id,
            )
            return _FALLBACK_THEME

        return requested_theme_id

    @staticmethod
    def annotate_theme_list(tenant_obj, themes: list[dict]) -> list[dict]:
        """
        Annotate a theme list with per-theme access and display metadata.

        Adds these keys to each theme dict (mutates in-place for efficiency):
            _can_use         : bool  — tenant may apply this theme
            _access_label    : str   — 'FREE', 'PRO', 'ENTERPRISE', 'ADMIN ACCESS'
            _badge_class     : str   — CSS class for the badge
            _show_lock       : bool  — show a lock icon (never True for Administrator)
            _show_upgrade_cta: bool  — show "Upgrade to unlock" (never True for Administrator)

        Args:
            tenant_obj: Tenant / Profile
            themes:     list of theme metadata dicts from ThemeRegistry.all()

        Returns:
            The same list with annotations added.
        """
        plan   = get_effective_plan(tenant_obj)
        _admin = is_administrator(plan)

        for theme in themes:
            can_use = ThemeAccessService.can_access_theme(tenant_obj, theme)
            theme["_can_use"]          = can_use
            theme["_show_lock"]        = not can_use and not _admin
            theme["_show_upgrade_cta"] = not can_use and not _admin
            theme["_access_label"]     = ThemeAccessService._access_label(theme, _admin)
            theme["_badge_class"]      = ThemeAccessService._badge_class(theme, _admin)

        return themes

    @staticmethod
    def _access_label(theme_meta: dict, is_admin: bool) -> str:
        """Human-readable access tier label for a theme card badge."""
        if is_admin:
            return "ADMIN ACCESS"
        required = theme_meta.get("required_plan")
        if required:
            canon = normalize_plan(required)
            return canon.upper()
        if theme_meta.get("premium", False):
            return "PRO"
        category = (theme_meta.get("category") or "").strip().lower()
        if category in _SPECIAL_FLAGS:
            return "ENTERPRISE"
        return "FREE"

    @staticmethod
    def _badge_class(theme_meta: dict, is_admin: bool) -> str:
        """CSS class for a theme card's plan badge."""
        if is_admin:
            return "badge-administrator"
        required = theme_meta.get("required_plan", "")
        canon    = normalize_plan(required).lower()
        _map = {
            "trial":        "badge-free",
            "basic":        "badge-basic",
            "pro":          "badge-pro",
            "business":     "badge-business",
            "enterprise":   "badge-enterprise",
        }
        if theme_meta.get("premium", False) and not canon:
            return "badge-pro"
        return _map.get(canon, "badge-free")

    @staticmethod
    def is_theme_visible_to_tenant(tenant_obj, theme_meta: dict) -> bool:
        """
        True iff the theme should appear in the tenant's theme picker.

        Hidden and internal themes are excluded from non-admin pickers.
        Administrators see ALL themes including hidden/internal/beta.
        Deactivated themes are invisible to everyone (even Administrators)
        in the picker — they may only be accessed via direct preview.
        """
        if not theme_meta.get("catalog_active", True):
            return False  # universally hidden when deactivated

        plan   = get_effective_plan(tenant_obj)
        _admin = is_administrator(plan)
        if _admin:
            return True  # administrators see everything

        category = (theme_meta.get("category") or "").strip().lower()
        if category in {_THEME_FLAG_HIDDEN, _THEME_FLAG_INTERNAL}:
            return False

        return True
