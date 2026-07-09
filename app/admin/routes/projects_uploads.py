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

from app import db
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
    if not filename:
        return None
    path = os.path.join(current_app.static_folder, 'uploads', folder, filename)
    try:
        return os.path.getsize(path)
    except OSError:
        return None

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
    if form.validate_on_submit():
        project = Project(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            title=form.title.data,
            description=form.description.data or '',
            description_short=form.description_short.data or '',
            live_url=form.live_url.data or '',
            github_url=form.github_url.data or '',
            framework=form.framework.data or '',
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

        if is_upload_file(form.image.data):
            max_uploads = plan_features.get('max_media_uploads')
            if max_uploads is not None and not project.image and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                img, upload_error = save_image(form.image.data, 'projects')
                if img:
                    project.image = img
                elif upload_error:
                    flash(upload_error, 'warning')

        db.session.add(project)
        db.session.commit()
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
        project.title             = form.title.data
        project.description       = form.description.data or ''
        project.description_short = form.description_short.data or ''
        project.live_url          = form.live_url.data or ''
        project.github_url        = form.github_url.data or ''
        project.framework         = form.framework.data or ''
        project.language          = form.language.data or ''
        project.category          = form.category.data
        project.status            = form.status.data
        project.is_featured       = form.is_featured.data
        project.date_completed    = form.date_completed.data
        project.order             = form.order.data or 0
        project.tags = [t.strip() for t in (form.tags.data or '').split(',') if t.strip()]

        if is_upload_file(form.image.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and not project.image and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                new_img, upload_error = save_image(form.image.data, 'projects')
                if new_img:
                    if project.image:
                        delete_image(project.image, 'projects')
                    project.image = new_img
                elif upload_error:
                    flash(upload_error, 'warning')

        db.session.commit()
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
    if project.image:
        delete_image(project.image, 'projects')
    db.session.delete(project)
    db.session.commit()
    log_activity('delete', 'project', title)
    flash(f'Project "{title}" deleted.', 'success')
    return redirect(url_for('admin.projects'))

@admin.route('/uploads')
@admin_required
def uploads():
    profile    = _load_tenant_profile()
    asset_type = request.args.get('asset_type', 'all')
    allowed_types = {'all', 'profile', 'project', 'testimonial', 'certificate', 'badge'}
    if asset_type not in allowed_types:
        asset_type = 'all'

    project_images = (
        _tenant_slug_filter(project_repository.query)
        .filter(Project.image != None)
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
            'url': url_for('static', filename=f'uploads/profiles/{profile.profile_image}'),
        })

    for project in project_images:
        assets.append({
            'id': project.id, 'type': 'project', 'label': 'Project Image',
            'filename': project.image, 'folder': 'projects',
            'description': project.title,
            'url': url_for('static', filename=f'uploads/projects/{project.image}'),
        })

    for testimonial in testimonial_images:
        assets.append({
            'id': testimonial.id, 'type': 'testimonial', 'label': 'Testimonial Avatar',
            'filename': testimonial.author_avatar, 'folder': 'profiles',
            'description': testimonial.author_name,
            'url': url_for('static', filename=f'uploads/profiles/{testimonial.author_avatar}'),
        })

    for cert in certificate_images:
        assets.append({
            'id': cert.id, 'type': 'certificate', 'label': 'Certificate Image',
            'filename': cert.image_path, 'folder': 'certificates',
            'description': f'{cert.title} — {cert.issuer}' if cert.issuer else cert.title,
            'url': url_for('static', filename=f'uploads/certificates/{cert.image_path}'),
        })

    for cert in badge_images:
        assets.append({
            'id': cert.id, 'type': 'badge', 'label': 'Certificate Badge',
            'filename': cert.badge_path, 'folder': 'certificates',
            'description': f'{cert.title} — {cert.issuer}' if cert.issuer else cert.title,
            'url': url_for('static', filename=f'uploads/certificates/{cert.badge_path}'),
        })

    for asset in assets:
        size = _get_asset_size(asset['filename'], asset['folder'])
        asset['size_bytes'] = size
        asset['size_text']  = _format_filesize(size)

    filtered_assets = [a for a in assets if a['type'] == asset_type] if asset_type != 'all' else assets
    counts = {
        'profile':     sum(1 for a in assets if a['type'] == 'profile'),
        'project':     sum(1 for a in assets if a['type'] == 'project'),
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

    if asset_type == 'project':
        project = _require_tenant_object(db.session.get(Project, asset_id))
        if project and project.image:
            delete_image(project.image, 'projects')
            project.image = None
            db.session.commit()
            log_activity('delete', 'project', project.title, 'Deleted project image')
            flash(f'Image removed from project "{project.title}".', 'success')
        else:
            flash('Project image not found.', 'warning')
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
    return jsonify(status='ok')
