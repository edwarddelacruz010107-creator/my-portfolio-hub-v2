"""
PATCH FILE: app/admin/__init__.py — Themes routes Administrator fix (v6.7)
═══════════════════════════════════════════════════════════════════════════════
PURPOSE
    Fix Administrator tenants seeing locked themes, lock icons, and upgrade CTAs
    in the Appearance → Themes UI.

ROOT CAUSE
    themes_index() only set theme['_can_use'] but the template had no access
    to '_access_label', '_badge_class', '_show_lock', '_show_upgrade_cta'.
    These were computed inline in the template using plan string comparisons
    that never accounted for 'Administrator'.

FIX
    Route ThemeAccessService.annotate_theme_list() for all theme annotations.
    Pass `entitlements` context from EntitlementService.get_feature_context().

APPLY INSTRUCTIONS
    Find the themes_index() and apply_theme() functions in app/admin/__init__.py
    and replace their bodies with the code below.  The surrounding decorators
    (@admin.route, @admin_required) stay unchanged.

    Also add this import near the top of app/admin/__init__.py (after existing imports):

        from app.services.plans import ThemeAccessService, EntitlementService
"""


# ═══════════════════════════════════════════════════════════════════════════════
# IMPORT ADDITION — add to top of app/admin/__init__.py
# ═══════════════════════════════════════════════════════════════════════════════

IMPORT_ADDITION = """
# Plan entitlement services (v6.7) — replaces inline plan string comparisons
from app.services.plans import ThemeAccessService, EntitlementService
"""


# ═══════════════════════════════════════════════════════════════════════════════
# REPLACEMENT: themes_index()
# ═══════════════════════════════════════════════════════════════════════════════

# REPLACE everything between `def themes_index():` and the next `@admin.route`
THEMES_INDEX_REPLACEMENT = '''
@admin.route('/appearance/themes')
@admin_required
def themes_index():
    """Theme picker — Appearance -> Themes."""
    from app.theme_engine import get_theme_engine
    from app.services.plans import ThemeAccessService, EntitlementService

    engine  = get_theme_engine()
    profile = _load_tenant_profile()

    all_themes      = engine.get_all_themes()
    active_theme_id = (getattr(profile, 'selected_theme', None) or 'default') if profile else 'default'

    # ── Annotate themes with access metadata ──────────────────────────────────
    # ThemeAccessService handles Administrator bypass:
    #   • _can_use          = True for all themes if Administrator
    #   • _show_lock        = False for Administrator
    #   • _show_upgrade_cta = False for Administrator
    #   • _access_label     = 'ADMIN ACCESS' for Administrator
    ThemeAccessService.annotate_theme_list(profile, all_themes)

    # Mark active theme
    categories = set()
    for theme in all_themes:
        theme['_is_active'] = theme['id'] == active_theme_id
        cat = (theme.get('category') or '').strip()
        if cat:
            categories.add(cat)

    # ── Entitlement context for template (suppresses upgrade banners) ─────────
    entitlements = EntitlementService.get_feature_context(profile)

    return render_template(
        'admin/themes/index.html',
        themes=all_themes,
        active_theme_id=active_theme_id,
        plan_name=_active_tenant_plan_name(),
        theme_categories=sorted(categories),
        entitlements=entitlements,
        # Convenience shortcuts used in legacy template code
        is_administrator_tenant=entitlements['is_administrator'],
        hide_upgrade_prompts=entitlements['hide_upgrade_prompts'],
    )
'''


# ═══════════════════════════════════════════════════════════════════════════════
# REPLACEMENT: apply_theme()
# ═══════════════════════════════════════════════════════════════════════════════

APPLY_THEME_REPLACEMENT = '''
@admin.route('/appearance/themes/apply', methods=['POST'])
@admin_required
def apply_theme():
    """Apply a theme to the active tenant. Never touches portfolio content."""
    from app.theme_engine import get_theme_engine, is_valid_theme_id
    from app.services.plans import ThemeAccessService

    engine   = get_theme_engine()
    profile  = _load_tenant_profile()
    theme_id = (request.form.get('theme_id') or '').strip()

    def _fail(message, status):
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=False, error=message), status
        flash(message, 'warning')
        return redirect(url_for('admin.themes_index'))

    if not profile:
        return _fail('No profile found for this tenant yet — set up your profile first.', 400)

    if not is_valid_theme_id(theme_id) or not engine.get_theme_meta(theme_id):
        return _fail('Theme not found.', 404)

    # Access check via ThemeAccessService (Administrator always passes)
    meta = engine.get_theme_meta(theme_id)
    if not ThemeAccessService.can_access_theme(profile, meta):
        return _fail('Upgrade your plan to unlock this theme.', 403)

    profile.selected_theme = theme_id

    try:
        from app.models.core import ThemeCatalogEntry
        catalog_entry = ThemeCatalogEntry.get_by_slug(theme_id)
        if catalog_entry:
            catalog_entry.increment_installs()
    except Exception:
        pass  # analytics are non-critical

    db.session.commit()
    log_activity('update', 'theme', theme_id, f'Theme switched to {theme_id}')

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(success=True, theme=meta, message=f\'Theme "{meta["name"]}" applied!\')

    flash(f\'Theme "{meta["name"]}" applied! Your live portfolio is now using it.\', 'success')
    return redirect(url_for('admin.themes_index'))
'''


# ═══════════════════════════════════════════════════════════════════════════════
# REPLACEMENT: preview_theme() — remove the is_administrator=True shim object
# ═══════════════════════════════════════════════════════════════════════════════

# In preview_theme, this shim object was needed because Profile had no is_administrator:
#
#   class _PreviewShim:
#       selected_theme=theme_id,
#       is_administrator=True,
#
# Now that Profile.is_administrator exists, the shim is unnecessary.
# CHANGE the preview_theme can_use_theme check from:
#
#   if not engine.can_use_theme(profile, theme_id):
#
# That call is already correct — no change needed there.
# The shim for resolve_theme bypass also still works.

PREVIEW_THEME_NOTE = """
preview_theme() shim note:
The shim object with is_administrator=True (line ~1135) was a workaround because
Profile had no is_administrator attribute.  Profile now has this property.
The shim can remain for safety; it won't break anything.  Or you can replace the
shim with `profile` directly and it will work correctly because Profile.is_administrator
now correctly reads from effective_plan().
"""

if __name__ == "__main__":
    print("This is a patch instruction file, not executable code.")
    print("Read the docstring at the top for apply instructions.")
