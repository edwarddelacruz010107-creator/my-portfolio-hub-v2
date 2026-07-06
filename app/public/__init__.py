"""
app/public/__init__.py — Public SaaS foundation blueprint.

Phase 1a (shipped): GET /explore, /projects, /pricing, /administrator — stub
templates, /pricing wired to BILLING_PLANS.

Phase 1b (this delivery — see AUDIT_REPORT.md):
    GET /explore, /projects    → now backed by real cross-tenant queries via
                              app/public/services/*.
    GET /u/<tenant_slug>   → NEW. Canonical public creator link (additive
                              alias — see routes.py::creator_link for why
                              this doesn't touch tenant_bp's existing
                              /<tenant_slug>/* route tree).
    GET /                  → still deliberately NOT a route on this
                              blueprint. It's registered directly on `app`
                              in app/__init__.py as the `root` endpoint,
                              which now calls
                              app.public.routes.render_landing_page().
                              Reason: 18 existing call sites do
                              url_for('root') expecting that exact endpoint
                              name; mounting '/' here would mean either a
                              duplicate route (Flask error) or renaming
                              'root' and touching all 18 sites. See
                              AUDIT_REPORT.md §1 for the full decision
                              record. Public templates should still prefer
                              url_for('root') over a hardcoded '/' for the
                              homepage link, exactly as the source spec
                              asked — it just isn't url_for('public.landing')
                              because that endpoint doesn't exist.

Registration order requirement: MUST be registered before tenant_bp
(which owns the '/<tenant_slug>' catch-all) and after the other
system blueprints. See app/__init__.py.
"""

from flask import Blueprint

public_bp = Blueprint(
    'public',
    __name__,
    url_prefix='',
    template_folder='templates',
)

from . import routes  # noqa: E402  (import after blueprint object exists)
