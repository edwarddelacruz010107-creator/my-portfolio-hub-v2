"""
routes/portfolio_route_patch.py

Patch for your existing public portfolio view function.
The view that renders the public portfolio (usually in routes/portfolio.py
or routes/tenant.py) needs to pass certificates and badges to the template.

FIND your existing public portfolio view — something like:

    @portfolio_bp.route('/')
    def portfolio():
        tenant = resolve_active_tenant()
        ...
        return render_template(
            theme_template,
            profile=profile,
            projects=projects,
            skills=skills,
            experiences=experiences,
            services=services,
            testimonials=testimonials,
        )

ADD the following query lines before the render_template call:

    from models.certificates import Certificate, Badge

    certificates = (
        Certificate.query
        .filter_by(tenant_id=tenant.id, is_visible=True)
        .order_by(Certificate.sort_order.asc(), Certificate.issue_date.desc())
        .all()
    )

    badges = (
        Badge.query
        .filter_by(tenant_id=tenant.id, is_visible=True)
        .order_by(Badge.display_order.asc())
        .all()
    )

THEN add to the render_template call:

        certificates=certificates,
        badges=badges,

COMPLETE PATCHED EXAMPLE (minimal, adapt to your actual function):
"""

# ── Example (do not import this file directly) ───────────────────────────────

from models.certificates import Certificate, Badge
from models.tenant import Tenant  # already imported in your route
from services.tenant_resolver import resolve_active_tenant
from flask import render_template


def portfolio_view_example():
    """
    Illustrative example only — merge into your real view function.
    """
    tenant = resolve_active_tenant()

    # ... existing queries for profile, projects, skills, etc. ...

    certificates = (
        Certificate.query
        .filter_by(tenant_id=tenant.id, is_visible=True)
        .order_by(Certificate.sort_order.asc(), Certificate.issue_date.desc())
        .all()
    )

    badges = (
        Badge.query
        .filter_by(tenant_id=tenant.id, is_visible=True)
        .order_by(Badge.display_order.asc())
        .all()
    )

    # Determine active theme template path
    theme = tenant.active_theme or "default_clean"
    theme_template = f"themes/{theme}/portfolio.html"

    return render_template(
        theme_template,
        # --- existing context vars ---
        # profile=profile,
        # projects=projects,
        # skills=skills,
        # experiences=experiences,
        # services=services,
        # testimonials=testimonials,
        # --- new additions ---
        certificates=certificates,
        badges=badges,
    )
