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
from sqlalchemy import or_

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
from app.forms import (ProfileForm, SEOSettingsForm, SkillForm, ProjectForm,
                        TestimonialForm, ServiceForm, ChangePasswordForm,
                        PlanSelectionForm)
from app.security import FileUploadPolicy, log_security_event
from app.theme_preview_badge import inject_theme_preview_badge
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

@admin.route('/seo', methods=['GET', 'POST'])
@admin_required
def seo_settings():
    """Portfolio-level search and social sharing controls."""
    profile = _load_tenant_profile()
    if not profile:
        flash('Create your profile before configuring SEO.', 'warning')
        return redirect(url_for('admin.edit_profile'))

    form = SEOSettingsForm(obj=profile)
    if request.method == 'GET':
        form.seo_indexable.data = bool(getattr(profile, 'seo_indexable', True))

    if form.validate_on_submit():
        if form.remove_og_image.data and profile.og_image:
            delete_image(profile.og_image, 'profiles')
            profile.og_image = ''

        if is_upload_file(form.og_image.data):
            new_image, upload_error = save_image(
                form.og_image.data,
                'profiles',
                max_size=(1600, 900),
                quality=88,
            )
            if new_image:
                if profile.og_image:
                    delete_image(profile.og_image, 'profiles')
                profile.og_image = new_image
            elif upload_error:
                flash(upload_error, 'warning')

        profile.meta_title = (form.meta_title.data or '').strip()
        profile.meta_description = (form.meta_description.data or '').strip()
        profile.seo_keywords = (form.seo_keywords.data or '').strip()
        profile.profile_image_alt = (form.profile_image_alt.data or '').strip()
        profile.seo_indexable = bool(form.seo_indexable.data)
        db.session.commit()

        try:
            from app import cache
            cache.delete(f'portfolio_page:{profile.tenant_slug}')
        except Exception:
            logger.debug('Could not clear portfolio cache after SEO update', exc_info=True)

        log_activity('update', 'seo', profile.name or profile.tenant_slug, 'Portfolio SEO settings updated')
        flash('SEO settings saved.', 'success')
        return redirect(url_for('admin.seo_settings'))

    return render_template('admin/seo.html', form=form, profile=profile)

def _persist_theme_selection(profile: Profile, theme_id: str) -> Profile:
    """Persist a theme selection on every duplicate row for the same tenant.

    A few older production databases can contain a duplicate Profile row after
    bootstrap/import work.  Updating every row that belongs to the exact same
    tenant keeps the Admin card, public portfolio, and custom-domain renderer
    consistent while the canonical repository lookup selects one stable row.
    """
    slug = (getattr(profile, 'tenant_slug', None) or _active_tenant_slug()).strip().lower()
    tenant_id = getattr(profile, 'tenant_id', None)

    filters = [Profile.tenant_slug == slug]
    if tenant_id is not None:
        filters.append(Profile.tenant_id == tenant_id)

    rows = Profile.query.filter(or_(*filters)).all()
    if not rows:
        rows = [profile]

    seen = set()
    for row in rows:
        if row.id in seen:
            continue
        seen.add(row.id)
        row.selected_theme = theme_id
        db.session.add(row)

    db.session.commit()
    db.session.expire_all()

    refreshed = profile_repository.get_by_tenant_slug(slug)
    if refreshed is None or (refreshed.selected_theme or 'default') != theme_id:
        raise RuntimeError('Theme selection did not persist to the canonical profile row.')
    return refreshed


def _clear_theme_page_cache(tenant_slug: str) -> None:
    """Clear cached public portfolio output after a theme switch."""
    try:
        from app import cache
        cache.delete(f'portfolio_page:{tenant_slug}')
    except Exception:
        logger.debug('Could not clear portfolio cache for theme switch tenant=%s', tenant_slug, exc_info=True)


@admin.route('/appearance/themes')
@admin_required
def themes_index():
    """Theme picker — Appearance -> Themes."""
    from app.theme_engine import get_theme_engine, is_supported_theme_id

    engine = get_theme_engine()
    profile = profile_repository.get_by_tenant_slug(_active_tenant_slug())

    active_theme_id = (getattr(profile, 'selected_theme', None) or 'default') if profile else 'default'
    if profile and not is_supported_theme_id(active_theme_id):
        # A retired theme can remain in older DB rows after deployment. Repair
        # it immediately so the UI and live portfolio have one active theme.
        try:
            profile = _persist_theme_selection(profile, 'default')
            active_theme_id = 'default'
        except Exception:
            db.session.rollback()
            logger.exception('Failed to normalize retired selected_theme=%s', active_theme_id)
            active_theme_id = 'default'

    all_themes = engine.get_all_themes()
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
    """Apply a supported theme to the active tenant and verify persistence."""
    from app.theme_engine import get_theme_engine, is_valid_theme_id, is_supported_theme_id

    engine = get_theme_engine()
    tenant_slug = _active_tenant_slug()
    profile = profile_repository.get_by_tenant_slug(tenant_slug)
    theme_id = (request.form.get('theme_id') or '').strip().lower()

    def _wants_json() -> bool:
        return bool(request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest')

    def _fail(message, status):
        if _wants_json():
            return jsonify(success=False, error=message), status
        flash(message, 'warning')
        return redirect(url_for('admin.themes_index'))

    if not profile:
        return _fail('No profile found for this tenant yet — set up your profile first.', 400)
    if not is_valid_theme_id(theme_id) or not is_supported_theme_id(theme_id):
        return _fail('This theme is not available.', 404)

    meta = engine.get_theme_meta(theme_id)
    if not meta:
        return _fail('Theme files are incomplete or missing.', 404)
    if not engine.can_use_theme(profile, theme_id):
        return _fail('Upgrade your plan to unlock this theme.', 403)

    try:
        profile = _persist_theme_selection(profile, theme_id)
    except Exception as exc:
        db.session.rollback()
        logger.exception('Theme apply failed tenant=%s theme=%s: %s', tenant_slug, theme_id, exc)
        return _fail('The theme could not be saved. Please try again.', 500)

    # Analytics is deliberately a separate best-effort transaction. A catalog
    # counter failure must never roll back the already-persisted theme choice.
    try:
        from app.models.core import ThemeCatalogEntry
        catalog_entry = ThemeCatalogEntry.get_by_slug(theme_id)
        if catalog_entry:
            catalog_entry.increment_installs()
            db.session.commit()
    except Exception:
        db.session.rollback()
        logger.debug('Theme install analytics update failed for %s', theme_id, exc_info=True)

    engine.clear_cache()
    _clear_theme_page_cache(tenant_slug)
    log_activity('update', 'theme', theme_id, f'Theme switched to {theme_id}')

    message = f'Theme "{meta["name"]}" applied! Your live portfolio is now using it.'
    if _wants_json():
        return jsonify(
            success=True,
            theme=meta,
            active_theme_id=profile.selected_theme,
            message=message,
            portfolio_url=url_for('public.administrator_portfolio') if tenant_slug == 'default'
                          else url_for('tenant.portfolio', tenant_slug=tenant_slug),
        )

    flash(message, 'success')
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
    # Preview is read-only: allow tenants to inspect locked themes.
    # The apply_theme() route remains the permanent plan gate.

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
    return inject_theme_preview_badge(rendered_preview, meta, label='Admin preview')
