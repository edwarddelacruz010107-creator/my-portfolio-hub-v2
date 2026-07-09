"""Admin work experience timeline CRUD."""
from __future__ import annotations

import logging

from flask import jsonify, redirect, render_template, request, url_for, flash
from flask_login import current_user

from app import db, limiter
from app.admin.blueprint import (
    admin,
    admin_required,
    _active_tenant_slug,
    _active_tenant_plan_features,
    _active_tenant_plan_name,
    _require_tenant_object,
    _tenant_slug_filter,
)
from app.forms import WorkExperienceForm
from app.models.portfolio import WorkExperience
from app.utils import log_activity

logger = logging.getLogger(__name__)


@admin.route('/experiences')
@admin_required
def experiences():
    items = (
        _tenant_slug_filter(WorkExperience.query)
        .order_by(WorkExperience.display_order.asc(), WorkExperience.start_date.desc(), WorkExperience.id.desc())
        .all()
    )
    return render_template('admin/experiences.html', experiences=items)


@admin.route('/experiences/new', methods=['GET', 'POST'])
@admin_required
def new_experience():
    plan_features = _active_tenant_plan_features()
    if not plan_features.get('experiences', True):
        flash('Work Experience Timeline is not available on your current plan. Upgrade to unlock this section.', 'warning')
        return redirect(url_for('admin.experiences'))

    max_items = plan_features.get('max_experiences')
    current_items = _tenant_slug_filter(WorkExperience.query).count()
    if max_items is not None and current_items >= max_items:
        flash(
            f'Your current plan ({_active_tenant_plan_name()}) allows up to {max_items} experience timeline items. Upgrade to add more.',
            'warning',
        )
        return redirect(url_for('admin.experiences'))

    form = WorkExperienceForm()
    if form.validate_on_submit():
        exp = WorkExperience(
            tenant_id=current_user.tenant_id,
            tenant_slug=_active_tenant_slug(),
            role=form.role.data,
            company=form.company.data,
            employment_type=form.employment_type.data or 'Full-time',
            location=form.location.data or '',
            start_date=form.start_date.data,
            end_date=None if form.is_current.data else form.end_date.data,
            is_current=form.is_current.data,
            description=form.description.data or '',
            achievements=form.achievements.data or '',
            technologies=form.technologies.data or '',
            icon=form.icon.data or 'lucide:briefcase-business',
            display_order=form.display_order.data or 0,
            is_visible=form.is_visible.data,
        )
        db.session.add(exp)
        db.session.commit()
        log_activity('create', 'experience', exp.role, f'Added experience: {exp.role} at {exp.company}')
        flash(f'Experience "{exp.role}" created!', 'success')
        return redirect(url_for('admin.experiences'))
    return render_template('admin/experience_form.html', form=form, experience=None)


@admin.route('/experiences/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_experience(id: int):
    exp = _require_tenant_object(db.session.get(WorkExperience, id))
    if exp is None:
        flash('Experience item not found.', 'warning')
        return redirect(url_for('admin.experiences'))

    form = WorkExperienceForm(obj=exp)
    if form.validate_on_submit():
        exp.role = form.role.data
        exp.company = form.company.data
        exp.employment_type = form.employment_type.data or 'Full-time'
        exp.location = form.location.data or ''
        exp.start_date = form.start_date.data
        exp.end_date = None if form.is_current.data else form.end_date.data
        exp.is_current = form.is_current.data
        exp.description = form.description.data or ''
        exp.achievements = form.achievements.data or ''
        exp.technologies = form.technologies.data or ''
        exp.icon = form.icon.data or 'lucide:briefcase-business'
        exp.display_order = form.display_order.data or 0
        exp.is_visible = form.is_visible.data
        db.session.commit()
        log_activity('update', 'experience', exp.role)
        flash(f'Experience "{exp.role}" updated!', 'success')
        return redirect(url_for('admin.experiences'))
    return render_template('admin/experience_form.html', form=form, experience=exp)


@admin.route('/experiences/<int:id>/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_experience(id: int):
    exp = _require_tenant_object(db.session.get(WorkExperience, id))
    if exp is None:
        flash('Experience item not found.', 'warning')
        return redirect(url_for('admin.experiences'))
    title = f'{exp.role} at {exp.company}'
    db.session.delete(exp)
    db.session.commit()
    log_activity('delete', 'experience', title)
    flash(f'Experience "{title}" deleted.', 'success')
    return redirect(url_for('admin.experiences'))


@admin.route('/experiences/reorder', methods=['POST'])
@admin_required
def reorder_experiences():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        exp = _require_tenant_object(db.session.get(WorkExperience, item.get('id')))
        if exp:
            exp.display_order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')
