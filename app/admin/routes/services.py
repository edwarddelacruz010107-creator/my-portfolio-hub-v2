"""
app/admin/routes/services.py — Services CRUD + reorder (Phase 4b, batch 8)

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


from app.admin.blueprint import (
    admin, admin_required, _active_tenant_slug, _tenant_slug_filter, _require_tenant_object,
    _active_tenant_plan_features, _active_tenant_plan_name,
)

logger = logging.getLogger(__name__)


@admin.route('/services')
@admin_required
def services():
    all_services = (
        _tenant_slug_filter(service_repository.query)
        .order_by(Service.display_order.asc(), Service.id.asc())
        .all()
    )
    return render_template('admin/services.html', services=all_services)

@admin.route('/services/new', methods=['GET', 'POST'])
@admin_required
def new_service():
    plan_features = _active_tenant_plan_features()
    if not plan_features.get('services', True):
        flash('Services are not available on your current plan. Upgrade to unlock this section.', 'warning')
        return redirect(url_for('admin.services'))
    max_items = plan_features.get('max_services')
    current_items = _tenant_slug_filter(service_repository.query).count()
    if max_items is not None and current_items >= max_items:
        flash(
            f'Your current plan ({_active_tenant_plan_name()}) allows up to {max_items} services. Upgrade to add more.',
            'warning',
        )
        return redirect(url_for('admin.services'))
    form = ServiceForm()
    if form.validate_on_submit():
        svc = Service(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            title=form.title.data,
            description=form.description.data or '',
            icon=form.icon.data or 'lucide:briefcase',
            features=form.features.data or '',
            display_order=form.display_order.data or 0,
            is_visible=form.is_visible.data,
        )
        db.session.add(svc)
        db.session.commit()
        try:
            from app.services.notification_service import publish_portfolio_completion_milestone
            publish_portfolio_completion_milestone(tenant_id=svc.tenant_id)
        except Exception:
            logger.exception('Portfolio milestone notification failed: tenant_id=%s', svc.tenant_id)
        log_activity('create', 'service', svc.title, f'Added service: {svc.title}')
        flash(f'Service "{svc.title}" created!', 'success')
        return redirect(url_for('admin.services'))
    return render_template('admin/service_form.html', form=form, service=None)

@admin.route('/services/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_service(id: int):
    svc = _require_tenant_object(db.session.get(Service, id))
    if svc is None:
        flash('Service not found.', 'warning')
        return redirect(url_for('admin.services'))
    form = ServiceForm(obj=svc)
    if form.validate_on_submit():
        svc.title         = form.title.data
        svc.description   = form.description.data or ''
        svc.icon          = form.icon.data or 'lucide:briefcase'
        svc.features      = form.features.data or ''
        svc.display_order = form.display_order.data or 0
        svc.is_visible    = form.is_visible.data
        db.session.commit()
        try:
            from app.services.notification_service import publish_portfolio_completion_milestone
            publish_portfolio_completion_milestone(tenant_id=svc.tenant_id)
        except Exception:
            logger.exception('Portfolio milestone notification failed: tenant_id=%s', svc.tenant_id)
        log_activity('update', 'service', svc.title)
        flash(f'Service "{svc.title}" updated!', 'success')
        return redirect(url_for('admin.services'))
    return render_template('admin/service_form.html', form=form, service=svc)

@admin.route('/services/<int:id>/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_service(id: int):
    svc = _require_tenant_object(db.session.get(Service, id))
    if svc is None:
        flash('Service not found.', 'warning')
        return redirect(url_for('admin.services'))
    title = svc.title
    db.session.delete(svc)
    db.session.commit()
    log_activity('delete', 'service', title)
    flash(f'Service "{title}" deleted.', 'success')
    return redirect(url_for('admin.services'))

@admin.route('/services/reorder', methods=['POST'])
@admin_required
def reorder_services():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        svc = _require_tenant_object(db.session.get(Service, item.get('id')))
        if svc:
            svc.display_order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')
