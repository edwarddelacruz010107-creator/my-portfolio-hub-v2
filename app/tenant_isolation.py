"""
app/tenant_isolation.py — Tenant isolation enforcement helpers

Centralizes all tenant-scoped query patterns for tenant_data_db.
Import from blueprints instead of writing raw filter_by() calls inline.

Security guarantee: Every public-facing function here enforces
filter_by(tenant_id=...) and raises TenantIsolationError on any attempt
to query without a valid tenant_id in scope.

OWASP A01 (Broken Access Control) / IDOR prevention.
"""

from functools import wraps
from flask import session, abort, g
from app import db
from app.models import (
    Profile, Skill, Project, Testimonial, Service, TenantFormSettings,
)


class TenantIsolationError(Exception):
    """Raised when a query would escape tenant scope."""


def _require_tenant_id() -> int:
    """Extract tenant_id from session; abort 403 if missing."""
    tid = session.get('tenant_id')
    if not tid:
        abort(403)
    return int(tid)


# ─────────────────────────────────────────────────────────────────────────────
# Decorator: enforce tenant scope on blueprint routes
# ─────────────────────────────────────────────────────────────────────────────

def tenant_required(f):
    """
    Route decorator. Validates session['tenant_id'] is present.
    Sets g.tenant_id and g.tenant_slug for use in the view.

    Usage:
        @admin_bp.route('/projects')
        @login_required
        @tenant_required
        def projects():
            projs = tenant_projects()
            ...
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        tid = session.get('tenant_id')
        if not tid:
            abort(403)
        g.tenant_id   = int(tid)
        g.tenant_slug = session.get('tenant_slug', '')
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Scoped query helpers — all filter by session tenant_id
# ─────────────────────────────────────────────────────────────────────────────

def tenant_profile() -> Profile | None:
    """Return current tenant's Profile, or None."""
    return Profile.query.filter_by(tenant_id=_require_tenant_id()).first()


def tenant_projects(status: str = None):
    """Return Query for current tenant's Projects."""
    q = Project.query.filter_by(tenant_id=_require_tenant_id())
    if status:
        q = q.filter_by(status=status)
    return q


def tenant_skills(visible_only: bool = False):
    q = Skill.query.filter_by(tenant_id=_require_tenant_id())
    if visible_only:
        q = q.filter_by(is_visible=True)
    return q


def tenant_testimonials(visible_only: bool = False):
    q = Testimonial.query.filter_by(tenant_id=_require_tenant_id())
    if visible_only:
        q = q.filter_by(is_visible=True)
    return q


def tenant_services(visible_only: bool = False):
    q = Service.query.filter_by(tenant_id=_require_tenant_id())
    if visible_only:
        q = q.filter_by(is_visible=True)
    return q


def tenant_form_settings() -> TenantFormSettings | None:
    return TenantFormSettings.query.filter_by(tenant_id=_require_tenant_id()).first()


# ─────────────────────────────────────────────────────────────────────────────
# Safe object retrieval with ownership check (prevents IDOR)
# ─────────────────────────────────────────────────────────────────────────────

def get_project_or_403(project_id: int) -> Project:
    """
    Fetch a project by PK and verify it belongs to the current tenant.
    Aborts 404 if not found, 403 if owned by another tenant.
    """
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)
    if project.tenant_id != _require_tenant_id():
        abort(403)  # IDOR protection — never 404 here (would leak existence)
    return project


def get_skill_or_403(skill_id: int) -> Skill:
    skill = db.session.get(Skill, skill_id)
    if skill is None:
        abort(404)
    if skill.tenant_id != _require_tenant_id():
        abort(403)
    return skill


def get_testimonial_or_403(testimonial_id: int) -> Testimonial:
    t = db.session.get(Testimonial, testimonial_id)
    if t is None:
        abort(404)
    if t.tenant_id != _require_tenant_id():
        abort(403)
    return t


def get_service_or_403(service_id: int) -> Service:
    svc = db.session.get(Service, service_id)
    if svc is None:
        abort(404)
    if svc.tenant_id != _require_tenant_id():
        abort(403)
    return svc


# ─────────────────────────────────────────────────────────────────────────────
# Public portfolio resolution (no auth required, uses slug not session)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_public_portfolio(tenant_slug: str) -> tuple:
    """
    Resolve a public portfolio page by slug.
    Returns (tenant, profile) — both may be None if tenant doesn't exist.

    This is the ONLY place where we do the two-DB round-trip for public routes.
    core_db lookup → tenant_data_db lookup.
    """
    from app.models import Tenant
    tenant = Tenant.query.filter_by(slug=tenant_slug, status='active').first()
    if not tenant:
        return None, None
    profile = Profile.query.filter_by(tenant_id=tenant.id).first()
    return tenant, profile


def get_public_projects(tenant_id: int):
    """Published projects for a public portfolio page."""
    return (
        Project.query
        .filter_by(tenant_id=tenant_id, status='published')
        .order_by(Project.is_featured.desc(), Project.order.asc(), Project.created_at.desc())
    )


def get_public_skills(tenant_id: int):
    return (
        Skill.query
        .filter_by(tenant_id=tenant_id, is_visible=True)
        .order_by(Skill.category, Skill.order)
    )


def get_public_testimonials(tenant_id: int):
    return (
        Testimonial.query
        .filter_by(tenant_id=tenant_id, is_visible=True)
        .order_by(Testimonial.order)
    )


def get_public_services(tenant_id: int):
    return (
        Service.query
        .filter_by(tenant_id=tenant_id, is_visible=True)
        .order_by(Service.display_order)
    )
