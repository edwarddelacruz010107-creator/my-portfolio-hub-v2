"""
app/admin/routes/settings_2fa.py — Settings, activity log, data export, TOTP 2FA (Phase 4b, batch 9)

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
                        get_profile_completion, is_upload_file, )
from app.tenant_security import (
    resolve_active_tenant, stamp_session_tenant,
    RESERVED_SLUGS, session_tenant_valid,
)
from app import limiter  # Flask-Limiter instance
from app.forms import ForgotPasswordForm  # Flask-WTF form for CSRF protection

logger = logging.getLogger(__name__)
admin  = Blueprint('admin', __name__)


from app.admin.blueprint import(admin,
                             admin_required,
                             _active_tenant_slug,
                             _tenant_slug_filter,
                             _require_tenant_object,
                             _load_tenant_profile,
                             _get_client_ip,
                        )

logger = logging.getLogger(__name__)


@admin.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.verify_password(form.current_password.data):
            current_user.password = form.new_password.data
            db.session.commit()
            log_activity('update', 'user', current_user.username, 'Password changed')
            flash('Password changed successfully!', 'success')
        else:
            flash('Current password is incorrect.', 'danger')
    # Pass tenant + the real per-tenant form settings (TenantFormSettings is
    # what app/tenant/__init__.py:contact() actually reads at delivery time;
    # tenant.form_provider/basin_endpoint are legacy display-only columns).
    from app.models.portfolio import Tenant
    from app.models.tenant_form_settings import TenantFormSettings
    tenant = tenant_repository.get_by_slug(_active_tenant_slug())
    form_settings = TenantFormSettings.get_or_create(tenant.id) if tenant else None
    return render_template('admin/settings.html', form=form, tenant=tenant, form_settings=form_settings)

@admin.route('/activity')
@admin_required
def activity():
    page = request.args.get('page', 1, type=int)
    logs = (
        _tenant_slug_filter(activity_log_repository.query)
        .order_by(ActivityLog.created_at.desc())
        .paginate(page=page, per_page=30, error_out=False)
    )
    return render_template('admin/activity.html', logs=logs)

@admin.route('/export')
@admin_required
def export_data():
    profile   = _load_tenant_profile()
    skills    = _tenant_slug_filter(skill_repository.query).order_by(Skill.order).all()
    projects  = _tenant_slug_filter(project_repository.query).order_by(Project.order).all()
    testi     = _tenant_slug_filter(testimonial_repository.query).order_by(Testimonial.order).all()
    inquiries = (
        _tenant_slug_filter(inquiry_repository.query)
        .order_by(Inquiry.created_at.desc()).limit(200).all()
    )

    def p_dict(obj):
        d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    payload = {
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'profile':     p_dict(profile) if profile else None,
        'skills':      [p_dict(s) for s in skills],
        'projects':    [p_dict(p) for p in projects],
        'testimonials': [p_dict(t) for t in testi],
        'inquiries':   [p_dict(i) for i in inquiries],
    }

    log_activity('export', 'portfolio', 'full export')
    return Response(
        json.dumps(payload, indent=2, default=str),
        mimetype='application/json',
        headers={
            'Content-Disposition': (
                f'attachment; filename=portfolio_export_'
                f'{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.json'
            )
        },
    )

@admin.route('/profile/2fa/setup', methods=['GET', 'POST'])
@admin_required
def setup_2fa():
    from app.forms import TOTPSetupForm
    from app.auth.totp import generate_setup_context
    form = TOTPSetupForm()
    ctx  = generate_setup_context(user=current_user)
    return render_template('admin/2fa_setup.html', form=form, **ctx)

@admin.route('/profile/2fa/enable', methods=['POST'])
@admin_required
def enable_2fa():
    from app.forms import TOTPSetupForm
    from app.auth.totp import (
        commit_2fa_enable, rate_limit_totp_verify,
        record_totp_failure, clear_totp_attempts, TotpRateLimitError,
    )
    form = TOTPSetupForm()
    ip   = _get_client_ip()

    try:
        rate_limit_totp_verify(ip)
    except TotpRateLimitError as e:
        flash(str(e), 'danger')
        session.pop('_pending_totp_secret', None)
        session.pop('_pending_backup_codes', None)
        return redirect(url_for('admin.settings'))

    if not form.validate_on_submit():
        flash('Invalid form submission.', 'danger')
        return redirect(url_for('admin.setup_2fa'))

    success, error = commit_2fa_enable(current_user, form.code.data or '')

    if success:
        clear_totp_attempts(ip)
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to persist 2FA: %s', exc)
            flash('Database error saving 2FA settings. Please try again.', 'danger')
            return redirect(url_for('admin.setup_2fa'))
        session['totp_verified'] = True
        log_activity('security', 'user', current_user.username, '2FA enabled via TOTP setup')
        flash('Two-factor authentication enabled successfully!', 'success')
        return redirect(url_for('admin.settings'))

    record_totp_failure(ip)
    flash(error or 'Code incorrect — please try again.', 'danger')
    return redirect(url_for('admin.setup_2fa'))

@admin.route('/profile/2fa/disable', methods=['POST'])
@admin_required
def disable_2fa():
    from app.forms import TOTPDisableForm
    form = TOTPDisableForm()
    if form.validate_on_submit():
        if current_user.verify_password(form.password.data):
            current_user.totp_enabled      = False
            current_user.totp_secret       = None
            current_user.totp_backup_codes = None
            db.session.commit()
            session.pop('totp_verified', None)
            log_activity('security', 'user', current_user.username, '2FA disabled')
            flash('Two-factor authentication has been disabled.', 'success')
        else:
            flash('Incorrect password. 2FA was not disabled.', 'danger')
    return redirect(url_for('admin.settings'))

@admin.route('/profile/2fa/regenerate-backup', methods=['POST'])
@admin_required
def regenerate_backup_codes():
    if not current_user.totp_enabled:
        flash('2FA is not enabled.', 'warning')
        return redirect(url_for('admin.settings'))
    codes = current_user.generate_backup_codes()
    db.session.commit()
    log_activity('security', 'user', current_user.username, 'Backup codes regenerated')
    session['_new_backup_codes'] = codes
    flash('Backup codes regenerated. Save them somewhere safe!', 'success')
    return redirect(url_for('admin.show_new_backup_codes'))

@admin.route('/profile/2fa/backup-codes')
@admin_required
def show_new_backup_codes():
    codes = session.pop('_new_backup_codes', None)
    if not codes:
        flash('No new backup codes to display.', 'warning')
        return redirect(url_for('admin.settings'))
    return render_template('admin/2fa_backup_codes.html', backup_codes=codes)
