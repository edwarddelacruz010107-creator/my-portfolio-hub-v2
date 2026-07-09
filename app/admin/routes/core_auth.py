"""
app/admin/routes/core_auth.py — License gate, dashboard, login/reset-password aliases, forgot-password flow (Phase 4b, batch 1)

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
from app.models.portfolio import (Tenant, Profile, Skill, Project, Testimonial, Service, WorkExperience,
                                   ActivityLog, Inquiry, InquiryReply, normalize_plan_name,
                                   get_plan_features)
from sqlalchemy import func
from app.forms import (ProfileForm, SkillForm, ProjectForm,
                        TestimonialForm, ServiceForm, ChangePasswordForm,
                        PlanSelectionForm)
from app.security import FileUploadPolicy, log_security_event
from werkzeug.utils import secure_filename
import uuid
from pathlib import Path
from app.utils import BILLING_PLANS, is_paymongo_enabled, log_activity
from app.models.portfolio import Subscription
from app.services.studio.dashboard_service import DashboardService
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
    _safe_root,
    LICENSE_PLANS,
    _load_tenant_profile,
    _tenant_slug_filter,
    _active_tenant_plan_name,
    _DEFAULT_TENANT_SLUG,
)

logger = logging.getLogger(__name__)


def _license_plan_details(plan: str) -> dict:
    """Alias into BILLING_PLANS for backward compatibility."""
    return BILLING_PLANS.get(normalize_plan_name(plan), BILLING_PLANS['Basic'])

def _normalize_datetime(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def _license_expiration_info(profile):
    plan_name = profile.license_plan or profile.plan or 'Basic'
    details = _license_plan_details(plan_name)
    expires_at = None
    expires_in_days = None
    activated_at = _normalize_datetime(profile.license_activated_at)
    if profile.license_active and activated_at:
        expires_at = activated_at + timedelta(days=details['duration_days'])
        expires_in_days = max(0, (expires_at - datetime.now(timezone.utc)).days)
    return details, expires_at, expires_in_days

@admin.route('/license', methods=['GET', 'POST'])
@admin_required
def license():
    """Deprecated — subscriptions activate automatically via PayMongo."""
    flash('License keys are no longer required. Manage your subscription on the Billing page.', 'info')
    return redirect(url_for('admin.billing_index'))

@admin.route('/')
@admin_required
def dashboard():
    profile           = _load_tenant_profile()
    project_query     = _tenant_slug_filter(project_repository.query)
    skill_query       = _tenant_slug_filter(skill_repository.query)
    testimonial_query = _tenant_slug_filter(testimonial_repository.query)
    inquiry_query     = _tenant_slug_filter(inquiry_repository.query)

    from datetime import date as _date
    _now_utc     = datetime.now(timezone.utc)
    _today_start = _now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    _week_start  = _now_utc - timedelta(days=7)

    stats = {
        'total_projects':     project_query.count(),
        'published_projects': project_query.filter_by(status='published').count(),
        'total_skills':       skill_query.filter_by(is_visible=True).count(),
        'total_testimonials': testimonial_query.count(),
        'total_experiences': _tenant_slug_filter(WorkExperience.query).count(),
        'profile_completion': get_profile_completion(profile) if profile else 0,
        'featured_projects':  project_query.filter_by(is_featured=True).count(),
        # Message counters — use explicit COUNT() to avoid selecting missing columns
        'unread_messages':    inquiry_query.filter_by(is_read=False).with_entities(func.count()).scalar(),
        'total_messages':     inquiry_query.with_entities(func.count()).scalar(),
        'today_messages':     inquiry_query.filter(Inquiry.created_at >= _today_start).with_entities(func.count()).scalar(),
        'week_messages':      inquiry_query.filter(Inquiry.created_at >= _week_start).with_entities(func.count()).scalar(),
    }
    recent_activity = (
        _tenant_slug_filter(activity_log_repository.query)
        .order_by(ActivityLog.created_at.desc())
        .limit(15).all()
    )
    recent_projects = (
        _tenant_slug_filter(project_repository.query)
        .order_by(Project.created_at.desc())
        .limit(5).all()
    )
    subscription = profile.current_subscription() if profile else None
    dashboard_service = DashboardService()
    tenant_context_payload = dashboard_service.build_context(current_user)
    tenant_context = tenant_context_payload['tenant_context']

    return render_template(
        'admin/dashboard.html',
        stats=stats,
        recent_activity=recent_activity,
        recent_projects=recent_projects,
        profile=profile,
        subscription=subscription,
        subscription_status=tenant_context_payload['subscription_state'],
        plan_name=getattr(tenant_context, 'plan', None) or _active_tenant_plan_name(),
        project_count=stats['total_projects'],
        unread_messages=stats['unread_messages'],
        tenant_context=tenant_context,
        subscription_badge=tenant_context_payload['subscription_badge'],
        trial_days_left=tenant_context_payload['trial_days_left'],
    )

@admin.route('/dashboard')
def dashboard_alias():
    """Backward-compatible alias for /admin/dashboard."""
    return redirect(url_for('admin.dashboard'))

@admin.route('/reset-password-required', methods=['GET', 'POST'])
@login_required
def reset_password_required():
    """
    Force-password-change wall shown after first login or admin-triggered reset.

    Flow:
      • If current_user.require_password_reset is False → redirect to dashboard.
      • On valid POST: update password, clear flag, redirect to login so a fresh
        session is established with the new credentials.
    """
    if not getattr(current_user, 'require_password_reset', False):
        return redirect(url_for('admin.dashboard'))

    form = ChangePasswordForm()
    if form.validate_on_submit():
        current_user.password = form.new_password.data
        current_user.require_password_reset = False
        db.session.commit()
        log_activity(
            current_user,
            'password_reset_required_completed',
            'User completed forced password reset',
        )
        flash('Password updated successfully. Please log in with your new password.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('admin/reset_password_required.html', form=form)

@admin.route('/login')
def login_alias():
    """
    Redirect /admin/login → appropriate login page.

    FIX v3.4.2: Explicit guard for 'default' and empty string — both map to
    auth.login (the root-domain login page).  Falsy or 'default' slugs must
    NEVER be passed to url_for('tenant.auth_login') because 'default' is in
    _RESERVED_SLUGS and would 404 or redirect to /.
    """
    next_page = request.args.get('next') or url_for('admin.dashboard')
    tenant    = session.get('tenant_slug', _DEFAULT_TENANT_SLUG)

    if tenant and tenant != _DEFAULT_TENANT_SLUG:
        try:
            return redirect(url_for('tenant.auth_login', tenant_slug=tenant, next=next_page))
        except Exception:
            pass  # Tenant blueprint not available — fall through to default login

    return redirect(url_for('auth.login', next=next_page))

@admin.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
@limiter.limit("100 per hour")
def forgot_password():
    """Step 1: Admin enters username + email — OTP dispatched (v5.6: username required)."""
    from flask_login import current_user as _cu
    if _cu.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    from flask import session as _session
    form = ForgotPasswordForm()
    if request.method == 'POST':
        logger.info('[ADMIN RESET][route] POST received at /admin/forgot-password')
        if not form.validate_on_submit():
            logger.warning(
                '[ADMIN RESET][route] validate_on_submit() FAILED — csrf_or_field_errors=%s',
                form.errors,
            )
            flash('Form validation failed. Please try again.', 'danger')
            return render_template('admin/forgot_password_request.html', form=form)
        logger.info('[ADMIN RESET][route] validate_on_submit() OK (CSRF + field validators passed)')
        from app.services.password_reset_service import initiate_admin_reset
        email    = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        if not email or not username:
            flash('Username and email are both required.', 'danger')
            return render_template('admin/forgot_password_request.html', form=form)

        ok, msg = initiate_admin_reset(email, username)
        flash(msg, 'info' if ok else 'danger')
        if ok:
            _session['_admin_pw_reset_email'] = email
            return redirect(url_for('admin.forgot_password_verify'))
    return render_template('admin/forgot_password_request.html', form=form)

@admin.route('/forgot-password/verify', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
@limiter.limit("20 per hour")
def forgot_password_verify():
    """Step 2: OTP verification."""
    from flask_login import current_user as _cu
    if _cu.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    from flask import session as _session
    email = _session.get('_admin_pw_reset_email', '')
    if not email:
        return redirect(url_for('admin.forgot_password'))

    if request.method == 'POST':
        from app.services.password_reset_service import verify_admin_otp
        raw_otp = request.form.get('otp_code', '').strip()
        ok, msg, token = verify_admin_otp(email, raw_otp)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            _session['_admin_pw_reset_token'] = token
            return redirect(url_for('admin.forgot_password_reset'))

    from app.services.password_reset_service import _get_ttl_minutes
    return render_template('admin/forgot_password_verify.html', email=email,
                           otp_ttl_minutes=_get_ttl_minutes())

@admin.route('/forgot-password/reset', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
@limiter.limit("10 per hour")
def forgot_password_reset():
    """Step 3: Set new password."""
    from flask_login import current_user as _cu
    if _cu.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    from flask import session as _session
    token = _session.get('_admin_pw_reset_token', '')
    if not token:
        return redirect(url_for('admin.forgot_password'))

    if request.method == 'POST':
        from app.services.password_reset_service import complete_admin_reset
        pw  = request.form.get('password', '').strip()
        pw2 = request.form.get('password_confirm', '').strip()
        if not pw or len(pw) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('admin/forgot_password_reset.html')
        if pw != pw2:
            flash('Passwords do not match.', 'danger')
            return render_template('admin/forgot_password_reset.html')
        ok, msg = complete_admin_reset(token, pw)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            _session.pop('_admin_pw_reset_email', None)
            _session.pop('_admin_pw_reset_token', None)
            return redirect(url_for('admin.login_alias'))

    return render_template('admin/forgot_password_reset.html')
