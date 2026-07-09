"""
app/public/routes.py — Phase 1b: public SaaS surface, wired to real data.

Changes from the Phase 1a skeleton (see AUDIT_REPORT.md §3 for the
decision record):
  • Added: landing()      — real SaaS homepage. NOT mounted at '/' on this
                             blueprint (see app/public/__init__.py docstring
                             for why) — called from app/__init__.py::root()
                             so the existing `root` endpoint name, and the
                             18 call sites that do url_for('root'), keep
                             working unchanged.
  • Added: /u/<slug>       — canonical public creator link (additive alias,
                             not a route migration — see AUDIT_REPORT.md §4
                             on why Phase-8-as-written was rejected).
  • explore()/feed()       — now query real data via app/public/services/*,
                             per the "no queries in routes" rule from the
                             source spec.
  • pricing()/administrator_gateway() — unchanged from Phase 1a.
"""

import logging

from flask import render_template, redirect, url_for, request, jsonify, abort
from flask_login import current_user

from app.utils import BILLING_PLANS, is_paymongo_enabled
from . import public_bp
from .services import creator_service, feed_service, discovery_service
from .services.landing_service import (
    get_landing_stats,
    get_landing_content,
    get_administrator_card,
    get_community_stats,
)
from .services.pricing_service import get_pricing_content
from .services.theme_showcase_service import get_showcase_themes, get_theme_detail
from app.theme_preview_badge import inject_theme_preview_badge
from app.forms import LandingContactForm
from app.models.portfolio import Inquiry
from app.services.email.email_service import EmailService
from app import db, limiter
from app.utils import log_activity
from flask import current_app, flash, redirect, url_for, request

logger = logging.getLogger(__name__)


def render_landing_page():
    """
    Real SaaS homepage content. Called from app/__init__.py::root() — see
    module docstring for why this isn't a route on public_bp itself.
    Every list here is already public-safe (see services/serializers.py);
    do not add raw model instances to this context.
    """
    featured_creators = creator_service.get_featured_creators(limit=6)
    trending_projects = feed_service.get_trending_projects(
        limit=6,
        current_user_id=current_user.id if current_user.is_authenticated else None,
    )
    stats = get_landing_stats()
    community = get_community_stats()
    administrator = get_administrator_card()
    landing_content = get_landing_content()
    form = LandingContactForm()
    theme_showcase = get_showcase_themes(limit=8)
    return render_template(
        'public/index.html',
        featured_creators=featured_creators,
        trending_projects=trending_projects,
        plans=BILLING_PLANS,
        paymongo_enabled=is_paymongo_enabled(),
        stats=stats,
        community=community,
        administrator=administrator,
        landing_content=landing_content,
        contact_form=form,
        themes=theme_showcase['themes'],
        themes_total=theme_showcase['total'],
        themes_has_more=theme_showcase['has_more'],
    )



@public_bp.route('/contact', methods=['POST'])
@limiter.limit('5 per hour')
def contact():
    from app.services.custom_domain_service import resolve_verified_custom_domain
    domain_record = resolve_verified_custom_domain(request.host)
    if domain_record is not None:
        from app.services.custom_domain_public import handle_custom_domain_contact
        return handle_custom_domain_contact(domain_record)

    form = LandingContactForm()
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            current_app.logger.debug('Contact form error %s: %s', field, errors)
        flash('Please correct the errors in the form and try again.', 'danger')
        return redirect(url_for('root'))

    # Honeypot spam protection
    if form.honeypot.data:
        current_app.logger.warning('Honeypot triggered on contact form')
        flash('Message rejected.', 'warning')
        return redirect(url_for('root'))

    # Use centralized contact submission pipeline for consistent provider routing
    from app.services.communication.contact_service import process_contact_submission

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip and ',' in ip:
        ip = ip.split(',')[0].strip()

    import secrets
    raw_sid = request.headers.get('X-Request-Id') or request.form.get('submission_id') or None
    submission_id = f"landing:{raw_sid or secrets.token_urlsafe(8)}"

    result = process_contact_submission(
        tenant_slug='default',
        name=form.full_name.data.strip(),
        email=form.email.data.strip().lower(),
        subject=form.subject.data.strip(),
        message=form.message.data.strip(),
        phone=form.phone.data.strip() if form.phone.data else '',
        company=form.company.data.strip() if form.company.data else '',
        source='landing_page',
        ip_address=(ip or '')[:45],
        user_agent=(request.headers.get('User-Agent') or '')[:300],
        submission_id=submission_id,
    )

    # If the client expects JSON (AJAX / fetch) return structured JSON
    from flask import jsonify
    wants_json = request.accept_mimetypes.accept_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if wants_json:
        status_code = 200 if result.success else 400
        return jsonify({
            'status': 'success' if result.success else 'error',
            'message': result.user_message,
        }), status_code

    if not result.success:
        flash(result.user_message or 'Submission failed.', 'danger')
        return redirect(url_for('root'))

    flash(result.user_message or 'Thanks — your message has been received.', 'success')
    return redirect(url_for('root'))


@public_bp.route('/explore')
def explore():
    """Creator + project discovery. Real queries via discovery_service."""
    query = (request.args.get('q') or '').strip()
    category = (request.args.get('category') or '').strip() or None
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    ctx = discovery_service.explore_page(query=query, category=category, page=page)
    return render_template('public/explore.html', **ctx)


@public_bp.route('/projects')
def projects():
    """Premium public project browser. /feed remains a legacy alias."""
    query = (request.args.get('q') or '').strip()
    category = (request.args.get('category') or '').strip()
    sort = (request.args.get('sort') or 'latest').strip().lower()
    if sort not in {'latest', 'featured', 'popular', 'liked'}:
        sort = 'latest'
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    page_size = 12
    offset = (page - 1) * page_size
    project_rows, total = feed_service.browse_projects(
        limit=page_size,
        offset=offset,
        query=query,
        category=category or None,
        sort=sort,
        current_user_id=current_user.id if current_user.is_authenticated else None,
    )
    categories = feed_service.get_categories()

    return render_template(
        'public/projects.html',
        projects=project_rows,
        total=total,
        categories=categories,
        query=query,
        category=category,
        sort=sort,
        page=page,
        page_size=page_size,
        has_next=offset + page_size < total,
        has_prev=page > 1,
    )


@public_bp.route('/feed')
def feed():
    """Legacy alias for the premium project browser."""
    return redirect(url_for('public.projects', **request.args.to_dict(flat=True)))


@public_bp.route('/privacy')
def privacy():
    """Public privacy policy page."""
    return render_template('public/legal.html', page='privacy')


@public_bp.route('/terms')
def terms():
    """Public terms of service page."""
    return render_template('public/legal.html', page='terms')


@public_bp.route('/pricing')
def pricing():
    """
    Pricing page. Amounts still come from BILLING_PLANS — same source of
    truth as app/main and tenant_bp's billing views, so plan names/prices
    can never drift out of sync with the actual billing system. Marketing
    copy (badges, CTAs, feature-list overrides, section heading/subtitle)
    comes from the Pricing CMS (app/superadmin/routes/pricing_settings.py)
    via pricing_service.get_pricing_content(), published values only.
    """
    pricing_content = get_pricing_content(draft_first=False)
    return render_template(
        'public/pricing.html',
        section=pricing_content['section'],
        plans=pricing_content['plans'],
        yearly_toggle_enabled=pricing_content['yearly_toggle_enabled'],
        paymongo_enabled=is_paymongo_enabled(),
    )


@public_bp.route('/themes')
def themes():
    """Public theme gallery. Shows every active theme on its own page."""
    query = (request.args.get('q') or '').strip()
    category = (request.args.get('category') or '').strip()
    sort = (request.args.get('sort') or 'featured').strip().lower()
    if sort not in {'featured', 'name', 'premium', 'free'}:
        sort = 'featured'
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    page_size = 12
    showcase = get_showcase_themes(limit=None)
    all_themes = list(showcase.get('themes') or [])

    categories = sorted({
        (theme.get('category') or '').strip()
        for theme in all_themes
        if (theme.get('category') or '').strip()
    })

    if query:
        q = query.lower()
        all_themes = [
            theme for theme in all_themes
            if q in (theme.get('name') or '').lower()
            or q in (theme.get('description') or '').lower()
            or q in (theme.get('tagline') or '').lower()
            or q in (theme.get('category') or '').lower()
            or any(q in str(tag).lower() for tag in (theme.get('tags') or []))
        ]

    if category:
        all_themes = [
            theme for theme in all_themes
            if (theme.get('category') or '').strip().lower() == category.lower()
        ]

    def _theme_sort_order(theme):
        try:
            return int(theme.get('sort_order') or 0)
        except (TypeError, ValueError):
            return 0

    if sort == 'name':
        all_themes = sorted(all_themes, key=lambda theme: (theme.get('name') or '').lower())
    elif sort == 'premium':
        all_themes = sorted(all_themes, key=lambda theme: (not bool(theme.get('premium')), (theme.get('name') or '').lower()))
    elif sort == 'free':
        all_themes = sorted(all_themes, key=lambda theme: (bool(theme.get('premium')), (theme.get('name') or '').lower()))
    else:
        all_themes = sorted(
            all_themes,
            key=lambda theme: (
                not bool(theme.get('is_featured')),
                _theme_sort_order(theme),
                (theme.get('name') or '').lower(),
            ),
        )

    total = len(all_themes)
    offset = (page - 1) * page_size
    shown = all_themes[offset:offset + page_size]

    return render_template(
        'public/themes.html',
        themes=shown,
        total=total,
        categories=categories,
        query=query,
        category=category,
        sort=sort,
        page=page,
        page_size=page_size,
        has_next=offset + page_size < total,
        has_prev=page > 1,
    )


@public_bp.route('/themes/<theme_id>/preview')
@limiter.limit('30 per hour')
def theme_preview(theme_id: str):
    """
    Public, unauthenticated, READ-ONLY preview of a theme rendered with
    static sample content (never real tenant data -- see
    app/public/services/theme_preview_data.py). Mirrors the pattern in
    app/admin/routes/profile_appearance.py::preview_theme, minus the
    plan/ownership gate: a marketing-page visitor previewing a premium
    theme's look carries no privilege-escalation risk since nothing here
    persists or touches a real tenant's `selected_theme`.
    """
    from types import SimpleNamespace
    from app.theme_engine import get_theme_engine, is_valid_theme_id
    from .services.theme_preview_data import build_sample_context

    if not is_valid_theme_id(theme_id):
        abort(404)

    engine = get_theme_engine()
    meta = engine.get_theme_meta(theme_id)
    if not meta or not meta.get('catalog_active', True):
        abort(404)

    # Throwaway shim carries the requested theme through resolve_theme()
    # without ever touching a real Profile row. is_administrator=True
    # bypasses the plan gate for this read-only preview by design (see
    # docstring above) -- it only affects which template renders, never
    # persists, never authenticates as anything.
    preview_profile = SimpleNamespace(
        selected_theme=theme_id,
        is_administrator=True,
        plan='enterprise',
    )

    sample_ctx = build_sample_context(
        tenant_slug='preview',
        contact_url=url_for('public.contact'),
    )
    sample_ctx['preview_mode'] = True

    rendered_preview = engine.render(preview_profile, 'index.html', **sample_ctx)
    return inject_theme_preview_badge(rendered_preview, meta, label='Public preview')


@public_bp.route('/administrator-portfolio')
@public_bp.route('/administrator-portfolio/')
def administrator_portfolio():
    """Public owner portfolio for the protected default administrator tenant."""
    from app import _render_default_portfolio
    return _render_default_portfolio()


@public_bp.route('/administrator-portfolio/project/<slug>')
def administrator_project_detail(slug: str):
    """Public project detail page for the protected default administrator tenant."""
    from app import _render_default_project_detail
    return _render_default_project_detail(slug)


@public_bp.route('/administrator')
def administrator_gateway():
    """
    Auth-aware dashboard gateway.

    NOT a new auth surface — delegates entirely to the existing
    @admin_required / @superadmin_required decorators on the target views.
    """
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    if getattr(current_user, 'is_superadmin', False):
        return redirect(url_for('superadmin.dashboard'))
    return redirect(url_for('admin.dashboard'))




def _load_public_project_or_404(project_id: int):
    """Load a project visible on public portfolio pages.

    Regular tenants require Published status. The protected default owner
    portfolio also allows featured Drafts, matching /administrator-portfolio.
    """
    from app.models.core import Tenant
    from app.models.tenant_data import Project

    project = Project.query.filter_by(id=project_id).first_or_404()
    visible = project.status == 'published'
    if (project.tenant_slug or '').strip().lower() == 'default':
        visible = visible or (project.status == 'draft' and bool(project.is_featured))
    if not visible:
        abort(404)
    if not Tenant.query.filter_by(slug=project.tenant_slug, status='active').first():
        abort(404)
    return project

@public_bp.route('/api/projects/<int:project_id>/reaction-state')
def project_reaction_state(project_id: int):
    from app.models.tenant_data import ProjectReaction

    project = _load_public_project_or_404(project_id)

    liked = False
    if current_user.is_authenticated:
        liked = bool(
            ProjectReaction.query.filter_by(project_id=project.id, user_id=current_user.id).first()
        )

    return jsonify({
        'project_id': project.id,
        'liked': liked,
        'like_count': int(project.like_count or 0),
        'view_count': int(project.view_count or 0),
    })


@public_bp.route('/api/projects/<int:project_id>/like', methods=['POST'])
@public_bp.route('/api/projects/<int:project_id>/react', methods=['POST'])
def project_like(project_id: int):
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError
    from app.models.tenant_data import ProjectReaction

    if not current_user.is_authenticated:
        return jsonify({'success': False, 'message': 'Login required'}), 401

    project = _load_public_project_or_404(project_id)

    existing = ProjectReaction.query.filter_by(project_id=project.id, user_id=current_user.id).first()
    if existing:
        return jsonify({'success': True, 'liked': True, 'like_count': int(project.like_count or 0)})

    reaction = ProjectReaction(
        tenant_id=project.tenant_id,
        project_id=project.id,
        user_id=current_user.id,
        ip_address=request.remote_addr,
        reaction_type='like',
    )
    project.like_count = int((project.like_count or 0) + 1)
    db.session.add(reaction)
    db.session.add(project)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        current_app.logger.warning(
            'Duplicate reaction prevented: user_id=%s project_id=%s err=%s',
            current_user.id, project_id, exc,
        )
        existing = ProjectReaction.query.filter_by(project_id=project.id, user_id=current_user.id).first()
        return jsonify({'success': True, 'liked': bool(existing), 'like_count': int(project.like_count or 0)})
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            'Reaction create failed: user_id=%s project_id=%s',
            current_user.id, project_id,
        )
        return jsonify({'success': False, 'message': 'Unable to save reaction. Please try again.'}), 500

    return jsonify({'success': True, 'liked': True, 'like_count': project.like_count})


@public_bp.route('/api/projects/<int:project_id>/unlike', methods=['POST'])
@public_bp.route('/api/projects/<int:project_id>/react', methods=['DELETE'])
def project_unlike(project_id: int):
    from sqlalchemy.exc import SQLAlchemyError
    from app.models.tenant_data import ProjectReaction

    if not current_user.is_authenticated:
        return jsonify({'success': False, 'message': 'Login required'}), 401

    project = _load_public_project_or_404(project_id)

    existing = ProjectReaction.query.filter_by(project_id=project.id, user_id=current_user.id).first()
    if not existing:
        return jsonify({'success': True, 'liked': False, 'like_count': int(project.like_count or 0)})

    db.session.delete(existing)
    project.like_count = max(int((project.like_count or 0) - 1), 0)
    db.session.add(project)
    try:
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            'Reaction delete failed: user_id=%s project_id=%s',
            current_user.id, project_id,
        )
        return jsonify({'success': False, 'message': 'Unable to remove reaction. Please try again.'}), 500

    return jsonify({'success': True, 'liked': False, 'like_count': project.like_count})


@public_bp.route('/u/<tenant_slug>')
def creator_link(tenant_slug: str):
    """
    Canonical public creator link — ADDITIVE alias, not a replacement for
    /<tenant_slug>/. See AUDIT_REPORT.md §4: standardizing every tenant
    route (billing, auth, admin included) under /u/ as the source spec's
    Phase 8 literally specifies would require moving tenant_bp's entire
    live route tree, which conflicts with that same spec's Phase 13
    ("DO NOT rewrite billing architecture / auth system / break existing
    tenant URLs"). This gives creators the clean /u/<name> link the spec
    wants for sharing, without touching tenant_bp at all:
      • slug == 'default' → 301 to /administrator-portfolio, the clean
        public URL for the platform owner's portfolio.
      • any other slug     → 301 to the existing, unchanged
        /<tenant_slug>/ route. Zero risk to tenant_bp's session/HMAC/
        reserved-slug logic.
    """
    tenant_slug = tenant_slug.strip().lower()

    if tenant_slug == 'default':
        return redirect(url_for('public.administrator_portfolio'), 301)

    from app.services.custom_domain_service import tenant_portfolio_public_url
    return redirect(tenant_portfolio_public_url(tenant_slug), 301)
