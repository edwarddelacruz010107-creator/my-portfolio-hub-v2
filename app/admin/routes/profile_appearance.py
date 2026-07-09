"""
app/admin/routes/profile_appearance.py — Profile editing + theme appearance (Phase 4b, batch 4)

Moved here verbatim from the former monolithic app/admin/__init__.py.
No behavior, route, or endpoint-name changes.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional

from flask import (session, Blueprint, render_template, redirect, url_for,
                   flash, request, jsonify, current_app, Response, abort)
from flask_login import login_required, current_user

from app import db
from app.repositories import (
    project_repository,
    profile_repository,
    tenant_repository,
    user_repository,
    testimonial_repository,
    skill_repository,
    service_repository,
    inquiry_repository,
    activity_log_repository,
    subscription_repository,
)
from app.models.portfolio import (Tenant, Profile, Skill, Project, Testimonial, Service,
                                   ActivityLog, Inquiry, InquiryReply, normalize_plan_name,
                                   get_plan_features)
from app.forms import (ProfileForm, SkillForm, ProjectForm,
                        TestimonialForm, ServiceForm, ChangePasswordForm,
                        PlanSelectionForm)
from app.security import FileUploadPolicy, log_security_event
from werkzeug.utils import secure_filename
import uuid
from pathlib import Path
from app.utils import BILLING_PLANS, is_paymongo_enabled, log_activity
from app.models.portfolio import Subscription
from app.services.billing import subscription_access_status
from app.services.billing_handlers import (
    billing_payment_context,
    billing_plans_context,
    handle_billing_payment_post,
    handle_billing_plans_post,
)
from app.services.manual_billing import get_payment_method_for_tenant
from app.utils import (save_image, delete_image, log_activity,
                        get_profile_completion, is_upload_file)
from app.tenant_security import (
    resolve_active_tenant, stamp_session_tenant,
    RESERVED_SLUGS, session_tenant_valid,
)
from app import limiter  # Flask-Limiter instance
from app.forms import ForgotPasswordForm  # Flask-WTF form for CSRF protection

logger = logging.getLogger(__name__)
admin  = Blueprint('admin', __name__)


from app.admin.blueprint import (admin, admin_required, _active_tenant_slug, _load_tenant_profile,
                                 _active_tenant_plan_features, _active_tenant_plan_name, _tenant_media_upload_count,
                                 _tenant_slug_filter)

logger = logging.getLogger(__name__)


@admin.route('/profile', methods=['GET', 'POST'])
@admin_required
def edit_profile():
    profile = _load_tenant_profile()
    if not profile:
        tenant_slug = _active_tenant_slug()
        tenant = tenant_repository.get_by_slug(tenant_slug)
        if not tenant:
            tenant = Tenant(
                slug=tenant_slug,
                company_name=tenant_slug.title(),
                status='active',
                plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()
        profile = Profile(tenant=tenant)
        db.session.add(profile)
        db.session.commit()

    form = ProfileForm(obj=profile)

    if request.method == 'GET':
        social = profile.social_links or {}
        for field in ['github', 'linkedin', 'facebook', 'twitter',
                      'instagram', 'youtube', 'website', 'dribbble']:
            getattr(form, field).data = social.get(field, '')

    if form.validate_on_submit():
        if is_upload_file(form.profile_image.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and not profile.profile_image and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                new_img, upload_error = save_image(form.profile_image.data, 'profiles', max_size=(800, 800), quality=90)
                if new_img:
                    if profile.profile_image:
                        delete_image(profile.profile_image, 'profiles')
                    profile.profile_image = new_img
                elif upload_error:
                    flash(upload_error, 'warning')

        profile.name                = form.name.data
        profile.title               = form.title.data
        profile.subtitle            = form.subtitle.data
        profile.bio                 = form.bio.data
        profile.bio_short           = form.bio_short.data
        profile.location            = form.location.data
        profile.email               = form.email.data
        profile.phone               = form.phone.data
        profile.resume_url          = form.resume_url.data or ''
        profile.years_experience    = form.years_experience.data or 0
        profile.experience_start_year = form.experience_start_year.data
        profile.clients_count       = form.clients_count.data or 0
        profile.hero_tagline        = form.hero_tagline.data
        profile.availability_status = form.availability_status.data
        profile.is_available        = form.is_available.data
        profile.social_links = {
            k: (getattr(form, k).data or '')
            for k in ['github', 'linkedin', 'facebook', 'twitter',
                       'instagram', 'youtube', 'website', 'dribbble']
        }
        db.session.commit()
        log_activity('update', 'profile', profile.name, 'Profile updated')
        flash('Profile saved!', 'success')
        return redirect(url_for('admin.edit_profile'))

    return render_template(
        'admin/profile.html',
        form=form,
        profile=profile,
        profile_completion=get_profile_completion(profile),
    )

@admin.route('/appearance/themes')
@admin_required
def themes_index():
    """Theme picker — Appearance -> Themes."""
    from app.theme_engine import get_theme_engine

    engine = get_theme_engine()
    profile = _load_tenant_profile()

    all_themes = engine.get_all_themes()
    active_theme_id = (getattr(profile, 'selected_theme', None) or 'default') if profile else 'default'

    categories = set()
    for theme in all_themes:
        theme['_can_use'] = engine.can_use_theme(profile, theme['id'])
        theme['_is_active'] = theme['id'] == active_theme_id
        cat = (theme.get('category') or '').strip()
        if cat:
            categories.add(cat)

    return render_template(
        'admin/themes/index.html',
        themes=all_themes,
        active_theme_id=active_theme_id,
        plan_name=_active_tenant_plan_name(),
        theme_categories=sorted(categories),
    )

@admin.route('/appearance/themes/apply', methods=['POST'])
@admin_required
def apply_theme():
    """Apply a theme to the active tenant. Never touches portfolio content."""
    from app.theme_engine import get_theme_engine, is_valid_theme_id

    engine = get_theme_engine()
    profile = _load_tenant_profile()
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
    if not engine.can_use_theme(profile, theme_id):
        return _fail('Upgrade your plan to unlock this theme.', 403)

    profile.selected_theme = theme_id

    # Increment install analytics counter on the catalog entry (if present).
    # Must be done before commit so both writes land in the same transaction.
    try:
        from app.models.core import ThemeCatalogEntry
        catalog_entry = ThemeCatalogEntry.get_by_slug(theme_id)
        if catalog_entry:
            catalog_entry.increment_installs()
    except Exception:
        pass  # analytics are non-critical — never block a theme switch

    db.session.commit()
    log_activity('update', 'theme', theme_id, f'Theme switched to {theme_id}')

    meta = engine.get_theme_meta(theme_id)
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(success=True, theme=meta, message=f'Theme "{meta["name"]}" applied!')

    flash(f'Theme "{meta["name"]}" applied! Your live portfolio is now using it.', 'success')
    return redirect(url_for('admin.themes_index'))

@admin.route('/appearance/themes/<theme_id>/preview')
@admin_required
def preview_theme(theme_id):
    """Live, read-only preview of a theme rendered with the tenant's real
    portfolio content. Never persists — does not call db.session.commit()."""
    from types import SimpleNamespace
    from app.theme_engine import get_theme_engine, is_valid_theme_id
    from app.theme_context import build_portfolio_view
    from app.models.portfolio import Project, Skill, Testimonial, Service, Certificate, WorkExperience

    if not is_valid_theme_id(theme_id):
        abort(404)

    engine = get_theme_engine()
    meta = engine.get_theme_meta(theme_id)
    if not meta:
        abort(404)

    profile = _load_tenant_profile()
    tenant_slug = _active_tenant_slug()

    all_projects = _tenant_slug_filter(project_repository.query).filter_by(status='published').all()
    skills = _tenant_slug_filter(skill_repository.query).order_by(Skill.category.asc(), Skill.order.asc()).all()
    testimonials = _tenant_slug_filter(testimonial_repository.query).filter_by(is_visible=True).all()
    services = _tenant_slug_filter(service_repository.query).filter_by(is_visible=True).all()
    certificates = (
        _tenant_slug_filter(Certificate.query)
        .filter_by(is_visible=True)
        .order_by(Certificate.display_order.asc(), Certificate.id.asc())
        .all()
    )
    experiences = (
        _tenant_slug_filter(WorkExperience.query)
        .filter_by(is_visible=True)
        .order_by(WorkExperience.display_order.asc(), WorkExperience.start_date.desc(), WorkExperience.id.desc())
        .all()
    )

    skills_by_category = {}
    for skill in skills:
        skills_by_category.setdefault(skill.category, []).append(skill)

    stats = {
        'projects_count': len(all_projects),
        'years_experience': profile.get_years_experience() if profile else 0,
        'clients_count': profile.clients_count if profile else 0,
    }

    portfolio_view, name_parts, categories = build_portfolio_view(
        profile,
        projects=all_projects,
        skills_by_category=skills_by_category,
        services=services,
        testimonials=testimonials,
        certificates=certificates,
        experiences=experiences,
        stats=stats,
        tenant_slug=tenant_slug,
        contact_url='#',
    )

    # Render with the previewed theme without persisting it -- a throwaway
    # shim object carries the override through resolve_theme() unchanged.
    preview_profile = SimpleNamespace(
        selected_theme=theme_id,
        is_administrator=True,  # preview always bypasses the plan gate visually...
        plan=getattr(profile, 'plan', 'free') if profile else 'free',
    )
    # ...but still block rendering a theme the tenant truly can't use, to
    # avoid the preview link itself becoming a paywall bypass.
    if not engine.can_use_theme(profile, theme_id):
        flash('Upgrade your plan to preview this theme.', 'warning')
        return redirect(url_for('admin.themes_index'))

    rendered_preview = engine.render(
        preview_profile,
        'index.html',
        profile=profile,
        portfolio=portfolio_view,
        name_parts=name_parts,
        featured_projects=[p for p in all_projects if p.is_featured],
        other_projects=[p for p in all_projects if not p.is_featured],
        skills=skills,
        skills_by_category=skills_by_category,
        testimonials=testimonials,
        certificates=certificates,
        services=services,
        experiences=experiences,
        stats=stats,
        categories=categories,
        tenant_slug=tenant_slug,
        contact_url='#',
        is_root_domain=False,
        preview_mode=True,
    )
    if not rendered_preview or not str(rendered_preview).strip():
        current_app.logger.error('Theme preview returned empty HTML for theme_id=%s tenant=%s', theme_id, tenant_slug)
        return Response(
            '<!doctype html><html><head><title>Theme preview unavailable</title>'
            '<style>body{margin:0;background:#090b12;color:#eef2ff;font-family:system-ui;display:grid;place-items:center;min-height:100vh}'
            '.card{max-width:560px;padding:32px;border:1px solid rgba(255,255,255,.14);border-radius:20px;background:#101421}'
            'a{color:#8b7bff}</style></head><body><div class="card"><h1>Theme preview unavailable</h1>'
            '<p>The selected theme returned an empty preview. Try applying another theme or refresh after saving your profile content.</p>'
            '<p><a href="' + url_for('admin.themes_index') + '">Back to Themes</a></p></div></body></html>',
            mimetype='text/html',
        )
    return rendered_preview
