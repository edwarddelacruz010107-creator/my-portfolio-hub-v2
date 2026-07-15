"""
app/admin/routes/skills.py — Skills CRUD + reorder (Phase 4b, batch 5)

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
    admin,
    admin_required,
    _active_tenant_slug,
    _tenant_slug_filter,
    _require_tenant_object,
    _active_tenant_plan_features,
    _active_tenant_plan_name,
)

logger = logging.getLogger(__name__)


@admin.route('/skills')
@admin_required
def skills():
    query      = _tenant_slug_filter(skill_repository.query)
    all_skills = query.order_by(Skill.category, Skill.order).all()
    by_category = {}
    for s in all_skills:
        by_category.setdefault(s.category, []).append(s)
    return render_template('admin/skills.html', skills=all_skills, by_category=by_category)

@admin.route('/skills/new', methods=['GET', 'POST'])
@admin_required
def new_skill():
    plan_features  = _active_tenant_plan_features()
    max_skills     = plan_features.get('max_skills')
    current_skills = _tenant_slug_filter(skill_repository.query).count()
    if max_skills is not None and current_skills >= max_skills:
        flash(
            f'Your current plan ({_active_tenant_plan_name()}) allows up to '
            f'{max_skills} skills. Upgrade to add more.',
            'warning',
        )
        return redirect(url_for('admin.skills'))

    form = SkillForm()
    if form.validate_on_submit():
        skill = Skill(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            name=form.name.data,
            proficiency=form.proficiency.data,
            category=form.category.data,
            icon=form.icon.data or '',
            color=form.color.data or '',
            order=form.order.data or 0,
            is_visible=form.is_visible.data,
        )
        db.session.add(skill)
        db.session.commit()
        try:
            from app.services.notification_service import publish_portfolio_completion_milestone
            publish_portfolio_completion_milestone(tenant_id=skill.tenant_id)
        except Exception:
            logger.exception('Portfolio milestone notification failed: tenant_id=%s', skill.tenant_id)
        log_activity('create', 'skill', skill.name, f'Added skill: {skill.name}')
        flash(f'Skill "{skill.name}" created!', 'success')
        return redirect(url_for('admin.skills'))
    return render_template('admin/skill_form.html', form=form, skill=None)

@admin.route('/skills/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_skill(id: int):
    skill = _require_tenant_object(db.session.get(Skill, id))
    if skill is None:
        flash('Skill not found.', 'warning')
        return redirect(url_for('admin.skills'))
    form = SkillForm(obj=skill)
    if form.validate_on_submit():
        skill.name        = form.name.data
        skill.proficiency = form.proficiency.data
        skill.category    = form.category.data
        skill.icon        = form.icon.data or ''
        skill.color       = form.color.data or ''
        skill.order       = form.order.data or 0
        skill.is_visible  = form.is_visible.data
        db.session.commit()
        try:
            from app.services.notification_service import publish_portfolio_completion_milestone
            publish_portfolio_completion_milestone(tenant_id=skill.tenant_id)
        except Exception:
            logger.exception('Portfolio milestone notification failed: tenant_id=%s', skill.tenant_id)
        log_activity('update', 'skill', skill.name)
        flash(f'Skill "{skill.name}" updated!', 'success')
        return redirect(url_for('admin.skills'))
    return render_template('admin/skill_form.html', form=form, skill=skill)

@admin.route('/skills/<int:id>/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_skill(id: int):
    skill = _require_tenant_object(db.session.get(Skill, id))
    if skill is None:
        flash('Skill not found.', 'warning')
        return redirect(url_for('admin.skills'))
    name = skill.name
    db.session.delete(skill)
    db.session.commit()
    log_activity('delete', 'skill', name)
    flash(f'Skill "{name}" deleted.', 'success')
    return redirect(url_for('admin.skills'))

@admin.route('/skills/reorder', methods=['POST'])
@admin_required
def reorder_skills():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        skill = _require_tenant_object(db.session.get(Skill, item.get('id')))
        if skill:
            skill.order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')
