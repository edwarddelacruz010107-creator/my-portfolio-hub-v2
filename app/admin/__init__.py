"""
app/admin/__init__.py — Admin blueprint package (Phase 4b split)

The 2,611-line monolithic blueprint module has been split into:

  app/admin/blueprint.py   — Blueprint object, admin_required decorator,
                              before_request tenant-resolution gate
                              (block_public_admin), and the tenant-resolution
                              helpers shared by >1 route module.
  app/admin/routes/core_auth.py            — license gate, dashboard, login/
                                              reset-password aliases, forgot-password
  app/admin/routes/billing.py              — billing index/plans/payment/history
  app/admin/routes/messaging.py            — admin <-> superadmin messaging
  app/admin/routes/profile_appearance.py   — profile editing + themes
  app/admin/routes/skills.py               — skills CRUD
  app/admin/routes/projects_uploads.py     — projects CRUD + media uploads
  app/admin/routes/testimonials.py         — testimonials CRUD
  app/admin/routes/services.py             — services CRUD
  app/admin/routes/settings_2fa.py         — settings, activity, export, TOTP 2FA
  app/admin/routes/notifications_email.py  — notifications + email services config

No route URLs, endpoint names, or behavior changed — every function moved
verbatim. The Blueprint object's name ('admin') is unchanged, so every
existing `url_for('admin.xxx')` call and template reference continues to
resolve identically. See PHASE4B_ADMIN_SPLIT_AUDIT.md for the full audit
trail.

This module exists only to preserve the import surface other modules and
tests rely on:
    from app.admin import admin
    from app.admin import admin_required
    from app.admin import _active_tenant_slug, _load_tenant_profile,
        _require_tenant_object, _tenant_slug_filter, block_public_admin
"""

from flask_login import current_user
from flask_login import current_user
from app.tenant_security import stamp_session_tenant
from app.admin.blueprint import (
    admin,
    admin_required,
    _safe_root,
    _active_tenant_slug,
    _load_tenant_profile,
    _tenant_slug_filter,
    _require_tenant_object,
    _active_tenant_plan_features,
    _active_tenant_plan_name,
    _tenant_media_upload_count,
    block_public_admin,
    LICENSE_PLANS,
)

# Importing app.admin.routes registers every route module's
# @admin.route(...) handlers against the shared blueprint object above.
from app.admin import routes as _routes  # noqa: E402,F401
