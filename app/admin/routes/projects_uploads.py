"""
app/admin/routes/projects_uploads.py — Projects CRUD + media uploads (Phase 4b, batch 6)

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
                   flash, request, jsonify, current_app, Response)
from flask_login import login_required, current_user

from app import db, cache
from app.repositories import (
    project_repository,
    profile_repository,
    tenant_repository,
    user_repository,
    testimonial_repository,
    certificate_repository,
    skill_repository,
    service_repository,
    inquiry_repository,
    activity_log_repository,
    subscription_repository,
)
from app.models.portfolio import (Tenant, Profile, Skill, Project, Testimonial, Certificate, Service,
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
from app.services.content_sanitizer import sanitize_rich_text

logger = logging.getLogger(__name__)
admin  = Blueprint('admin', __name__)


from app.admin.blueprint import (admin,
                                admin_required,
                                _active_tenant_slug,
                                _tenant_slug_filter, 
                                _require_tenant_object, 
                                _active_tenant_plan_features, 
                                _tenant_media_upload_count, 
                                _load_tenant_profile, 
                                _active_tenant_plan_name, 
                                _active_tenant_plan_features, 
                                _tenant_media_upload_count,
                                )

logger = logging.getLogger(__name__)


def _get_asset_size(filename: str, folder: str) -> int | None:
    from app.services.media.upload_storage import upload_size
    return upload_size(filename, folder)


def _asset_public_url(filename: str, folder: str) -> str:
    """Return the same resilient upload URL used by public themes."""
    from app.services.media.upload_storage import build_upload_url
    try:
        return build_upload_url(filename, folder)
    except Exception:
        logger.exception('Could not build upload URL for folder=%s filename=%s', folder, filename)
        return ''


def _asset_exists(filename: str, folder: str) -> bool:
    from app.services.media.upload_storage import upload_exists
    return upload_exists(filename, folder)



def _active_tenant_id() -> int | None:
    """Return the core Tenant.id for the active admin tenant.

    Using current_user.tenant_id alone is fragile for the protected default
    administrator portfolio after imports/redeploys. The active slug is the
    source of truth in this blueprint, so resolve the core Tenant row by slug
    before falling back to the user field.
    """
    tenant_slug = _active_tenant_slug()
    try:
        tenant = tenant_repository.get_by_slug(tenant_slug)
        if tenant is not None:
            return tenant.id
    except Exception:
        logger.exception('Could not resolve tenant id for slug=%s', tenant_slug)
    return getattr(current_user, 'tenant_id', None)


def _clear_portfolio_cache(tenant_slug: str | None = None) -> None:
    """Clear cached public portfolio pages after project changes."""
    slug = (tenant_slug or _active_tenant_slug() or 'default').strip().lower()
    try:
        cache.delete(f'portfolio_page:{slug}')
    except Exception:
        logger.debug('Could not clear portfolio cache for slug=%s', slug, exc_info=True)

def _apply_project_uploads(project: Project, form: ProjectForm, plan_features: dict) -> None:
    """Save cover and comparison images with one quota-aware code path."""
    max_uploads = plan_features.get('max_media_uploads')
    used_uploads = _tenant_media_upload_count()
    media_fields = (
        ('image', 'image', 'Project image'),
        ('before_image', 'before_image', 'Before comparison image'),
        ('after_image', 'after_image', 'After comparison image'),
    )
    for form_name, attribute, label in media_fields:
        file_data = getattr(form, form_name).data
        if not is_upload_file(file_data):
            continue
        current_value = getattr(project, attribute, '') or ''
        adds_slot = not bool(current_value)
        if max_uploads is not None and adds_slot and used_uploads >= max_uploads:
            flash(
                f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                f'{max_uploads} uploads. {label} was not uploaded.',
                'warning',
            )
            continue
        new_img, upload_error = save_image(file_data, 'projects')
        if new_img:
            if current_value:
                delete_image(current_value, 'projects')
            setattr(project, attribute, new_img)
            if adds_slot:
                used_uploads += 1
        elif upload_error:
            flash(upload_error, 'warning')


def _format_filesize(size: int | None) -> str:
    if size is None:
        return 'n/a'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'

@admin.route('/projects')
@admin_required
def projects():
    q               = request.args.get('q', '').strip()
    status_filter   = request.args.get('status', 'all')
    category_filter = request.args.get('category', 'all')

    # Tenant scope FIRST, then additional filters
    query = _tenant_slug_filter(project_repository.query)
    if q:
        query = query.filter(Project.title.ilike(f'%{q}%'))
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    if category_filter != 'all':
        query = query.filter_by(category=category_filter)

    all_projects = query.order_by(Project.order.asc(), Project.created_at.desc()).all()
    categories   = sorted({
        c[0] for c in _tenant_slug_filter(db.session.query(Project.category)).distinct()
        if c[0]
    })

    return render_template(
        'admin/projects.html',
        projects=all_projects,
        q=q,
        status_filter=status_filter,
        category_filter=category_filter,
        categories=categories,
    )

@admin.route('/projects/new', methods=['GET', 'POST'])
@admin_required
def new_project():
    plan_features    = _active_tenant_plan_features()
    max_projects     = plan_features.get('max_projects')
    current_projects = _tenant_slug_filter(project_repository.query).count()
    if max_projects is not None and current_projects >= max_projects:
        flash(
            f'Your current plan ({_active_tenant_plan_name()}) allows up to '
            f'{max_projects} projects. Upgrade to add more.',
            'warning',
        )
        return redirect(url_for('admin.projects'))

    form = ProjectForm()
    if request.method == 'GET' and not form.status.data:
        # Public portfolio renders Published projects. Default new projects to
        # Published so owners do not accidentally create invisible Draft cards.
        form.status.data = 'published'

    if form.validate_on_submit():
        tenant_slug = _active_tenant_slug()
        tenant_id = _active_tenant_id()
        if tenant_id is None:
            flash('Cannot save project because the active tenant record was not found.', 'danger')
            return redirect(url_for('admin.projects'))

        project = Project(
            tenant_id=tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=tenant_slug,
            title=form.title.data,
            description=sanitize_rich_text(form.description.data),
            description_short=form.description_short.data or '',
            image_alt=form.image_alt.data or '',
            before_image_alt=form.before_image_alt.data or '',
            after_image_alt=form.after_image_alt.data or '',
            live_url=form.live_url.data or '',
            github_url=form.github_url.data or '',
            prototype_url=form.prototype_url.data or '',
            framework=form.framework.data or '',
            problem_statement=sanitize_rich_text(form.problem_statement.data),
            solution_overview=sanitize_rich_text(form.solution_overview.data),
            outcome_summary=sanitize_rich_text(form.outcome_summary.data),
            client_quote=form.client_quote.data or '',
            client_name=form.client_name.data or '',
            client_role=form.client_role.data or '',
            meta_title=form.meta_title.data or '',
            meta_description=form.meta_description.data or '',
            case_study_enabled=bool(form.case_study_enabled.data),
            language=form.language.data or '',
            category=form.category.data,
            status=form.status.data,
            is_featured=form.is_featured.data,
            date_completed=form.date_completed.data,
            order=form.order.data or 0,
        )
        project.tags = [t.strip() for t in (form.tags.data or '').split(',') if t.strip()]

        base_slug = project.generate_slug()
        slug      = base_slug
        counter   = 1
        while project_repository.slug_exists(slug):
            slug = f'{base_slug}-{counter}'
            counter += 1
        project.slug = slug

        _apply_project_uploads(project, form, plan_features)

        db.session.add(project)
        db.session.commit()
        _clear_portfolio_cache(project.tenant_slug)
        log_activity('create', 'project', project.title)
        flash(f'Project "{project.title}" created!', 'success')
        return redirect(url_for('admin.projects'))

    return render_template('admin/project_form.html', form=form, project=None)

@admin.route('/projects/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_project(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        flash('Project not found.', 'warning')
        return redirect(url_for('admin.projects'))
    form = ProjectForm(obj=project)

    if request.method == 'GET':
        form.tags.data = ', '.join(project.tags or [])

    if form.validate_on_submit():
        tenant_id = _active_tenant_id()
        if tenant_id is not None:
            project.tenant_id = tenant_id
        project.tenant_slug = _active_tenant_slug()

        project.title             = form.title.data
        project.description       = sanitize_rich_text(form.description.data)
        project.description_short = form.description_short.data or ''
        project.image_alt         = form.image_alt.data or ''
        project.before_image_alt  = form.before_image_alt.data or ''
        project.after_image_alt   = form.after_image_alt.data or ''
        project.live_url          = form.live_url.data or ''
        project.github_url        = form.github_url.data or ''
        project.prototype_url     = form.prototype_url.data or ''
        project.framework         = form.framework.data or ''
        project.problem_statement = sanitize_rich_text(form.problem_statement.data)
        project.solution_overview = sanitize_rich_text(form.solution_overview.data)
        project.outcome_summary   = sanitize_rich_text(form.outcome_summary.data)
        project.client_quote      = form.client_quote.data or ''
        project.client_name       = form.client_name.data or ''
        project.client_role       = form.client_role.data or ''
        project.meta_title        = form.meta_title.data or ''
        project.meta_description  = form.meta_description.data or ''
        project.case_study_enabled = bool(form.case_study_enabled.data)
        project.language          = form.language.data or ''
        project.category          = form.category.data
        project.status            = form.status.data
        project.is_featured       = form.is_featured.data
        project.date_completed    = form.date_completed.data
        project.order             = form.order.data or 0
        project.tags = [t.strip() for t in (form.tags.data or '').split(',') if t.strip()]

        _apply_project_uploads(project, form, _active_tenant_plan_features())

        db.session.commit()
        _clear_portfolio_cache(project.tenant_slug)
        log_activity('update', 'project', project.title)
        flash(f'Project "{project.title}" updated!', 'success')
        return redirect(url_for('admin.projects'))

    return render_template('admin/project_form.html', form=form, project=project)

@admin.route('/projects/<int:id>/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_project(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        flash('Project not found.', 'warning')
        return redirect(url_for('admin.projects'))
    title = project.title
    for media_value in (project.image, project.before_image, project.after_image):
        if media_value:
            delete_image(media_value, 'projects')
    tenant_slug = project.tenant_slug
    db.session.delete(project)
    db.session.commit()
    _clear_portfolio_cache(tenant_slug)
    log_activity('delete', 'project', title)
    flash(f'Project "{title}" deleted.', 'success')
    return redirect(url_for('admin.projects'))

@admin.route('/uploads')
@admin_required
def uploads():
    profile    = _load_tenant_profile()
    asset_type = request.args.get('asset_type', 'all')
    allowed_types = {'all', 'profile', 'project', 'comparison', 'testimonial', 'certificate', 'badge', 'seo'}
    if asset_type not in allowed_types:
        asset_type = 'all'

    project_images = (
        _tenant_slug_filter(project_repository.query)
        .filter(Project.image != None)
        .order_by(Project.created_at.desc())
        .all()
    )
    comparison_projects = (
        _tenant_slug_filter(project_repository.query)
        .filter(db.or_(Project.before_image != '', Project.after_image != ''))
        .order_by(Project.created_at.desc())
        .all()
    )
    testimonial_images = (
        _tenant_slug_filter(testimonial_repository.query)
        .filter(Testimonial.author_avatar != None)
        .filter(Testimonial.author_avatar != '')
        .order_by(Testimonial.created_at.desc())
        .all()
    )
    certificate_images = (
        _tenant_slug_filter(certificate_repository.query)
        .filter(Certificate.image_path != None)
        .filter(Certificate.image_path != '')
        .order_by(Certificate.created_at.desc())
        .all()
    )
    badge_images = (
        _tenant_slug_filter(certificate_repository.query)
        .filter(Certificate.badge_path != None)
        .filter(Certificate.badge_path != '')
        .order_by(Certificate.created_at.desc())
        .all()
    )

    assets = []
    if profile and profile.profile_image:
        assets.append({
            'id': profile.id, 'type': 'profile', 'label': 'Profile Image',
            'filename': profile.profile_image, 'folder': 'profiles',
            'description': profile.name or profile.tenant_slug,
            'url': _asset_public_url(profile.profile_image, 'profiles'),
        })

    if profile and profile.og_image:
        assets.append({
            'id': profile.id, 'type': 'seo', 'label': 'Social Share Image',
            'filename': profile.og_image, 'folder': 'profiles',
            'description': profile.meta_title or profile.name or profile.tenant_slug,
            'url': _asset_public_url(profile.og_image, 'profiles'),
        })

    for project in project_images:
        assets.append({
            'id': project.id, 'type': 'project', 'label': 'Project Image',
            'filename': project.image, 'folder': 'projects', 'field': 'image',
            'description': project.title,
            'url': _asset_public_url(project.image, 'projects'),
        })

    for project in comparison_projects:
        if project.before_image:
            assets.append({
                'id': project.id, 'type': 'comparison', 'label': 'Before Image',
                'filename': project.before_image, 'folder': 'projects', 'field': 'before_image',
                'description': project.title,
                'url': _asset_public_url(project.before_image, 'projects'),
            })
        if project.after_image:
            assets.append({
                'id': project.id, 'type': 'comparison', 'label': 'After Image',
                'filename': project.after_image, 'folder': 'projects', 'field': 'after_image',
                'description': project.title,
                'url': _asset_public_url(project.after_image, 'projects'),
            })

    for testimonial in testimonial_images:
        assets.append({
            'id': testimonial.id, 'type': 'testimonial', 'label': 'Testimonial Avatar',
            'filename': testimonial.author_avatar, 'folder': 'profiles',
            'description': testimonial.author_name,
            'url': _asset_public_url(testimonial.author_avatar, 'profiles'),
        })

    for cert in certificate_images:
        assets.append({
            'id': cert.id, 'type': 'certificate', 'label': 'Certificate Image',
            'filename': cert.image_path, 'folder': 'certificates',
            'description': f'{cert.title} — {cert.issuer}' if cert.issuer else cert.title,
            'url': _asset_public_url(cert.image_path, 'certificates'),
        })

    for cert in badge_images:
        assets.append({
            'id': cert.id, 'type': 'badge', 'label': 'Certificate Badge',
            'filename': cert.badge_path, 'folder': 'certificates',
            'description': f'{cert.title} — {cert.issuer}' if cert.issuer else cert.title,
            'url': _asset_public_url(cert.badge_path, 'certificates'),
        })

    for asset in assets:
        size = _get_asset_size(asset['filename'], asset['folder'])
        asset['size_bytes'] = size
        asset['size_text']  = _format_filesize(size)
        asset['exists']     = _asset_exists(asset['filename'], asset['folder'])

    filtered_assets = [a for a in assets if a['type'] == asset_type] if asset_type != 'all' else assets
    counts = {
        'profile':     sum(1 for a in assets if a['type'] == 'profile'),
        'project':     sum(1 for a in assets if a['type'] == 'project'),
        'comparison':  sum(1 for a in assets if a['type'] == 'comparison'),
        'seo':         sum(1 for a in assets if a['type'] == 'seo'),
        'testimonial': sum(1 for a in assets if a['type'] == 'testimonial'),
        'certificate': sum(1 for a in assets if a['type'] == 'certificate'),
        'badge':       sum(1 for a in assets if a['type'] == 'badge'),
        'all':         len(assets),
    }
    total_size = sum(a['size_bytes'] for a in assets if a['size_bytes'] is not None)

    return render_template(
        'admin/uploads.html',
        assets=filtered_assets,
        asset_type=asset_type,
        counts=counts,
        total_assets=counts['all'],
        total_size=_format_filesize(total_size),
    )

@admin.route('/uploads/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_upload():
    asset_type   = request.form.get('asset_type')
    asset_field  = (request.form.get('asset_field') or '').strip()
    asset_id_raw = request.form.get('asset_id')
    try:
        asset_id = int(asset_id_raw) if asset_id_raw is not None else None
    except (TypeError, ValueError):
        asset_id = None

    if asset_type == 'profile':
        profile = _load_tenant_profile()
        if profile and profile.profile_image:
            delete_image(profile.profile_image, 'profiles')
            profile.profile_image = None
            db.session.commit()
            log_activity('delete', 'profile', profile.name or profile.tenant_slug, 'Deleted profile image')
            flash('Profile image deleted.', 'success')
        else:
            flash('Nothing to delete.', 'warning')
        return redirect(url_for('admin.uploads'))

    if asset_type in {'project', 'comparison'}:
        project = _require_tenant_object(db.session.get(Project, asset_id))
        field_name = asset_field if asset_field in {'image', 'before_image', 'after_image'} else 'image'
        media_value = getattr(project, field_name, '') if project else ''
        if project and media_value:
            delete_image(media_value, 'projects')
            setattr(project, field_name, '')
            db.session.commit()
            log_activity('delete', 'project', project.title, f'Deleted project media: {field_name}')
            flash(f'Image removed from project "{project.title}".', 'success')
        else:
            flash('Project image not found.', 'warning')
        return redirect(url_for('admin.uploads'))

    if asset_type == 'seo':
        profile = _load_tenant_profile()
        if profile and profile.og_image:
            delete_image(profile.og_image, 'profiles')
            profile.og_image = ''
            db.session.commit()
            log_activity('delete', 'profile', profile.name or profile.tenant_slug, 'Deleted social share image')
            flash('Social share image deleted.', 'success')
        else:
            flash('Social share image not found.', 'warning')
        return redirect(url_for('admin.uploads'))

    if asset_type == 'testimonial':
        testimonial = _require_tenant_object(db.session.get(Testimonial, asset_id))
        if testimonial and testimonial.author_avatar:
            delete_image(testimonial.author_avatar, 'profiles')
            testimonial.author_avatar = None
            db.session.commit()
            log_activity('delete', 'testimonial', testimonial.author_name, 'Deleted testimonial avatar')
            flash(f'Avatar removed for testimonial "{testimonial.author_name}".', 'success')
        else:
            flash('Testimonial avatar not found.', 'warning')
        return redirect(url_for('admin.uploads'))

    if asset_type == 'certificate':
        cert = _require_tenant_object(db.session.get(Certificate, asset_id))
        if cert and cert.image_path:
            delete_image(cert.image_path, 'certificates')
            cert.image_path = ''
            db.session.commit()
            log_activity('delete', 'certificate', cert.title, 'Deleted certificate image')
            flash(f'Certificate image removed for "{cert.title}".', 'success')
        else:
            flash('Certificate image not found.', 'warning')
        return redirect(url_for('admin.uploads'))

    if asset_type == 'badge':
        cert = _require_tenant_object(db.session.get(Certificate, asset_id))
        if cert and cert.badge_path:
            delete_image(cert.badge_path, 'certificates')
            cert.badge_path = ''
            db.session.commit()
            log_activity('delete', 'certificate', cert.title, 'Deleted certificate badge')
            flash(f'Certificate badge removed for "{cert.title}".', 'success')
        else:
            flash('Certificate badge not found.', 'warning')
        return redirect(url_for('admin.uploads'))

    flash('Unsupported upload type.', 'danger')
    return redirect(url_for('admin.uploads'))

@admin.route('/projects/<int:id>/toggle', methods=['POST'])
@admin_required
def toggle_project(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        return jsonify(error='Not found'), 404
    project.status = 'published' if project.status != 'published' else 'draft'
    db.session.commit()
    _clear_portfolio_cache(project.tenant_slug)
    action = 'publish' if project.status == 'published' else 'unpublish'
    log_activity(action, 'project', project.title)
    return jsonify(status=project.status, title=project.title)

@admin.route('/projects/<int:id>/toggle-featured', methods=['POST'])
@admin_required
def toggle_featured(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        return jsonify(error='Not found'), 404
    project.is_featured = not project.is_featured
    db.session.commit()
    _clear_portfolio_cache(project.tenant_slug)
    return jsonify(featured=project.is_featured)

@admin.route('/projects/reorder', methods=['POST'])
@admin_required
def reorder_projects():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        p = _require_tenant_object(db.session.get(Project, item.get('id')))
        if p:
            p.order = item.get('order', 0)
    db.session.commit()
    _clear_portfolio_cache()
    return jsonify(status='ok')
