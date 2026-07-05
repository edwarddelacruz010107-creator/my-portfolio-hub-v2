"""
app/admin/routes/testimonials.py — Testimonials CRUD + reorder (Phase 4b, batch 7)

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


from app.admin.blueprint import admin, admin_required, _active_tenant_slug, _tenant_slug_filter, _require_tenant_object, _tenant_media_upload_count

logger = logging.getLogger(__name__)


@admin.route('/testimonials')
@admin_required
def testimonials():
    all_t = _tenant_slug_filter(testimonial_repository.query).order_by(Testimonial.order).all()
    return render_template('admin/testimonials.html', testimonials=all_t)

@admin.route('/testimonials/new', methods=['GET', 'POST'])
@admin_required
def new_testimonial():
    form = TestimonialForm()
    if form.validate_on_submit():
        t = Testimonial(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            author_name=form.author_name.data,
            author_title=form.author_title.data or '',
            author_company=form.author_company.data or '',
            content=form.content.data,
            rating=form.rating.data,
            is_featured=form.is_featured.data,
            is_visible=form.is_visible.data,
            order=form.order.data or 0,
        )
        if is_upload_file(form.author_avatar.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                img, upload_error = save_image(form.author_avatar.data, 'profiles', max_size=(200, 200))
                if img:
                    t.author_avatar = img
                elif upload_error:
                    flash(upload_error, 'warning')
        db.session.add(t)
        db.session.commit()
        log_activity('create', 'testimonial', t.author_name)
        flash('Testimonial added!', 'success')
        return redirect(url_for('admin.testimonials'))
    return render_template('admin/testimonial_form.html', form=form, testimonial=None)

@admin.route('/testimonials/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_testimonial(id: int):
    t = _require_tenant_object(db.session.get(Testimonial, id))
    if t is None:
        flash('Testimonial not found.', 'warning')
        return redirect(url_for('admin.testimonials'))
    form = TestimonialForm(obj=t)
    if form.validate_on_submit():
        t.author_name    = form.author_name.data
        t.author_title   = form.author_title.data or ''
        t.author_company = form.author_company.data or ''
        t.content        = form.content.data
        t.rating         = form.rating.data
        t.is_featured    = form.is_featured.data
        t.is_visible     = form.is_visible.data
        t.order          = form.order.data or 0
        if is_upload_file(form.author_avatar.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and not t.author_avatar and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                new_img, upload_error = save_image(form.author_avatar.data, 'profiles', max_size=(200, 200))
                if new_img:
                    if t.author_avatar:
                        delete_image(t.author_avatar, 'profiles')
                    t.author_avatar = new_img
                elif upload_error:
                    flash(upload_error, 'warning')
        db.session.commit()
        log_activity('update', 'testimonial', t.author_name)
        flash('Testimonial updated!', 'success')
        return redirect(url_for('admin.testimonials'))
    return render_template('admin/testimonial_form.html', form=form, testimonial=t)

@admin.route('/testimonials/<int:id>/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_testimonial(id: int):
    t = _require_tenant_object(db.session.get(Testimonial, id))
    if t is None:
        flash('Testimonial not found.', 'warning')
        return redirect(url_for('admin.testimonials'))
    name = t.author_name
    if t.author_avatar:
        delete_image(t.author_avatar, 'profiles')
    db.session.delete(t)
    db.session.commit()
    log_activity('delete', 'testimonial', name)
    flash(f'Testimonial from "{name}" deleted.', 'success')
    return redirect(url_for('admin.testimonials'))

@admin.route('/testimonials/reorder', methods=['POST'])
@admin_required
def reorder_testimonials():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        t = _require_tenant_object(db.session.get(Testimonial, item.get('id')))
        if t:
            t.order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')
