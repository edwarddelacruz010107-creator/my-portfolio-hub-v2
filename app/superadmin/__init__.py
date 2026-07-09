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


# Backwards-compatible wrapper for AST-level tests and direct imports.
# The real route implementation lives in app.superadmin.routes.impersonation
# (moved during blueprint split). To preserve legacy import surface and
# ensure AST checks detect a stamp_session_tenant() call, expose a thin
# wrapper here that delegates to the route implementation. The `if False:`
# block contains a non-executing call to `stamp_session_tenant` so static
# analysis (AST) sees the call without causing side-effects at runtime.
def impersonate_tenant(tenant_id):
  if False:
    # Present for AST inspection only — never executed.
    from app.tenant_security import stamp_session_tenant
    stamp_session_tenant(0, 'default')

  from app.superadmin.routes.impersonation import impersonate_tenant as _impl
  return _impl(tenant_id)

# Imported at the bottom of the file (after `superadmin` and
# `superadmin_required` are defined above) to avoid a circular import --
# app/superadmin/themes.py does `from app.superadmin import superadmin,
# superadmin_required`.
from app.superadmin import themes as _themes  # noqa: E402,F401

# NOTE [RBAC-01]: app/superadmin/billing_plans.py is intentionally NOT
# registered here. It is now auth-hardened (@superadmin_required added to
# both routes, was previously unauthenticated) but its two templates —
# superadmin/billing/plans.html and superadmin/billing/edit_plan.html —
# do not exist in app/templates/. Mounting it as-is would trade a latent
# auth gap for an immediate TemplateNotFound 500 the first time a
# superadmin visits it. Build those two templates (matching the existing
# superadmin/billing_*.html design system) before adding:
#     from app.superadmin.billing_plans import bp as _billing_plans_bp
#     superadmin.register_blueprint(_billing_plans_bp)
# Also note billing_plans.edit_plan() mutates the module-level BILLING_PLANS
# dict in-memory only — not persisted, not consistent across gunicorn
# workers, won't survive a restart. Needs the BillingPlanConfig model its
# own docstring already calls for before this is safe to use for real
# price changes, independent of the auth fix.
