"""
app/superadmin/__init__.py — Superadmin blueprint package (Phase 4b split)

The 3,305-line monolithic blueprint module has been split into:

  app/superadmin/blueprint.py        — Blueprint object, superadmin_required
                                        decorator, context processor, and
                                        helpers shared by >1 route module.
  app/superadmin/routes/core_auth.py — login/logout/forgot-password/dashboard
  app/superadmin/routes/tenants.py   — tenant CRUD
  app/superadmin/routes/messaging.py — superadmin <-> tenant messaging
  app/superadmin/routes/billing.py   — billing overview/payment methods/instructions/submissions
  app/superadmin/routes/media.py     — media library management
  app/superadmin/routes/email_settings.py — global email config (MailerSend/SMTP/Resend)
  app/superadmin/routes/subscriptions.py  — subscription settings + licenses
  app/superadmin/routes/twofa.py     — superadmin TOTP 2FA
  app/superadmin/routes/logs_monitor.py   — activity logs + subscription monitor
  app/superadmin/routes/impersonation.py  — tenant impersonation + tenant comms

No route URLs, endpoint names, or behavior changed — every function moved
verbatim. The Blueprint object's name ('superadmin') is unchanged, so every
existing `url_for('superadmin.xxx')` call and template reference continues
to resolve identically. See PHASE4_AUDIT.md and the blueprint-split plan
for the full audit trail.

This module exists only to preserve the import surface other modules and
tests rely on:
    from app.superadmin import superadmin
    from app.superadmin import superadmin_required
    from app.superadmin import forgot_password_request, forgot_password_verify, forgot_password
"""

from app.superadmin.blueprint import superadmin, superadmin_required

# Importing app.superadmin.routes registers every route module's
# @superadmin.route(...) handlers against the shared blueprint object above.
from app.superadmin import routes as _routes  # noqa: E402,F401

# Re-export functions that other modules/tests import directly by name
# (not just as routes) — preserves `from app.superadmin import X`.
from app.superadmin.routes.core_auth import (  # noqa: E402,F401
    login, logout, forgot_password, dashboard, dashboard_alias,
    forgot_password_request, forgot_password_verify, forgot_password_reset,
)

# Imported at the bottom of the file (after `superadmin` and
# `superadmin_required` are defined above) to avoid a circular import --
# app/superadmin/themes.py does `from app.superadmin import superadmin,
# superadmin_required`.
from app.superadmin import themes as _themes  # noqa: E402,F401
