"""
app/superadmin/routes/core_auth.py — Login / logout / forgot-password / dashboard (core, stays imported first) (Phase 4b, batch 10)

Moved here verbatim from the former monolithic app/superadmin/__init__.py.
No behavior, route, or endpoint-name changes — see blueprint split plan.
"""

import csv
import io
import logging
import re
import secrets
import string
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, session, current_app, Response,
)
from urllib.parse import urlparse
from flask_login import current_user, logout_user, login_required
from pathlib import Path
from sqlalchemy import or_, func
from werkzeug.utils import secure_filename

from app.auth import _handle_login
from app.forms import (
    ChangePasswordForm, SuperadminAccountForm,
    TenantForm, SuperadminMessageForm, PaymentInstructionForm, PaymentMethodForm,
)
from app import db, limiter
from app.repositories import (
    profile_repository,
    tenant_repository,
    user_repository,
    project_repository,
    testimonial_repository,
    inquiry_repository,
    activity_log_repository,
    subscription_repository,
    payment_method_repository,
    payment_submission_repository,
    subscription_notification_repository,
    webhook_event_repository,
    global_email_config_repository,
)
from app.services.manual_billing import (
    approve_payment_submission,
    reject_payment_submission,
    save_billing_upload,
    set_default_payment_method,
)
from app.services.tenant_admin import delete_tenant_completely
from app.utils import is_paymongo_enabled, set_paymongo_enabled
from app.models import User
from app.models.portfolio import (Profile, PaymentMethod, PaymentSubmission, Subscription, WebhookEvent,
                                   ActivityLog, Project, Inquiry, Tenant, PaymentInstruction, PAID_PLAN_NAMES,
                                   normalize_plan_name)


from app.utils import log_activity, BILLING_PLANS, YEARLY_DISCOUNT
from app.security import log_security_event
from app.tenant_security import RESERVED_SLUGS, validate_slug, stamp_session_tenant
from app.models.portfolio import TenantCommunicationSettings
from app.models.portfolio import _utcnow
from app.services.billing import (
    compute_billing_metrics,
    tenant_billing_summary,
    force_activate_subscription,
    sync_subscription_from_paymongo,
)


from app.superadmin.blueprint import superadmin, superadmin_required, _safe_root

logger = logging.getLogger(__name__)


@superadmin.route('/login', methods=['GET', 'POST'])
def login():
    """
    Superadmin login page at /superadmin/login.
    FIX: Does NOT call url_for('root') before the app is fully initialized.
    """
    if current_user.is_authenticated:
        if current_user.is_superadmin:
            return redirect(url_for('superadmin.dashboard'))
        # Non-superadmin somehow hit this page → send to safe root
        return redirect(_safe_root())

    # Clear any lingering tenant context — superadmin login is global
    session.pop('tenant_slug', None)

    return _handle_login(
        require_superadmin=True,
        default_next=url_for('superadmin.dashboard'),
        action_url=url_for('superadmin.login'),
        page_title='Superadmin Portal',
        page_subtitle='Sign in to manage the entire tenant system',
        allow_google=False,  # explicit belt-and-suspenders: superadmin is password+TOTP only
    )

@superadmin.route('/logout')
def logout():
    """
    Superadmin logout — clears session and redirects to superadmin login.
    
    v4.0 FIX: Also clears session_token to revoke HMAC signatures.
    """
    if current_user.is_authenticated:
        log_activity('logout', 'user', current_user.username)
    session.pop('_session_token', None)  # v4.0: clear session token for HMAC revocation
    session.pop('totp_verified', None)
    session.pop('tenant_slug', None)
    session.pop('_tsig', None)
    session.pop('_tsig_created', None)
    session.pop('_tsig_user_id', None)
    session.clear()
    logout_user()
    flash('Signed out from superadmin.', 'info')
    return redirect(url_for('superadmin.login'))

@superadmin.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """
    Legacy entry point — redirects to the new DB-backed flow (HTTP 301).
    Kept so any existing bookmarks or links continue to work.
    """
    return redirect(url_for('superadmin.forgot_password_request'), code=301)

@superadmin.route('/')
@superadmin_required
def dashboard():
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all').strip()
    plan_filter = request.args.get('plan', 'all').strip()
    export_format = request.args.get('export', '').strip().lower()

    total_tenants = profile_repository.query.count()
    active_tenants = profile_repository.query.filter(Profile.is_available == True).count()   # noqa: E712
    revenue = db.session.query(func.coalesce(func.sum(Subscription.amount_paid), 0.0))
    revenue = revenue.filter(Subscription.status == 'active').scalar() or 0.0
    pending_payments = payment_submission_repository.query.filter_by(status='pending').count()

    today = datetime.now(timezone.utc)
    expiring_threshold = today + timedelta(days=30)
    expiring_accounts = (
        subscription_repository.query
        .filter(
            Subscription.status == 'active',
            Subscription.expires_at.isnot(None),
            Subscription.expires_at > today,
            Subscription.expires_at <= expiring_threshold,
        )
        .count()
    )
    expired_accounts = (
        subscription_repository.query
        .filter(Subscription.status.in_(['expired', 'cancelled']))
        .count()
    )

    query = profile_repository.query.order_by(Profile.updated_at.desc())
    if q:
        query = query.filter(
            or_(
                Profile.name.ilike(f'%{q}%'),
                Profile.tenant_slug.ilike(f'%{q}%'),
                Profile.email.ilike(f'%{q}%'),
            )
        )
    if status_filter == 'active':
        query = query.filter(Profile.is_available == True)   # noqa: E712
    elif status_filter == 'inactive':
        query = query.filter(Profile.is_available == False)   # noqa: E712
    if plan_filter != 'all':
        query = query.filter(Profile.plan == plan_filter)

    if export_format in ('csv', 'excel'):
        return _dashboard_export(query.all(), export_format, q, status_filter, plan_filter)

    plan_breakdown_rows = (
        db.session.query(Profile.plan, func.count(Profile.id))
        .group_by(Profile.plan)
        .all()
    )
    plan_breakdown = {row[0] or 'Unknown': row[1] for row in plan_breakdown_rows}

    subscription_status_rows = (
        db.session.query(Subscription.status, func.count(Subscription.id))
        .group_by(Subscription.status)
        .all()
    )
    subscription_status = {row[0]: row[1] for row in subscription_status_rows}

    recent_tenants = query.limit(6).all()
    recent_activity = (
        activity_log_repository.query
        .order_by(ActivityLog.created_at.desc())
        .limit(6)
        .all()
    )
    recent_payments = (
        payment_submission_repository.query
        .order_by(PaymentSubmission.submitted_at.desc())
        .limit(5)
        .all()
    )

    # Read currency symbol from BILLING_PLANS (set in Plan Settings by superadmin)
    _any_plan = next(iter(BILLING_PLANS.values()), {})
    currency_symbol = _any_plan.get('currency_symbol', '₱') or '₱'

    stats = {
        'total_tenants':    total_tenants,
        'active_tenants':   active_tenants,
        'revenue':          revenue,
        'currency_symbol':  currency_symbol,
        'pending_payments': pending_payments,
        'expiring_accounts': expiring_accounts,
        'expired_accounts': expired_accounts,
    }

    # ── Monitoring: superadmin-only ops data ─────────────────────────────────
    heartbeat_state: dict = {}
    try:
        from app.heartbeat import get_heartbeat_state
        heartbeat_state = get_heartbeat_state() or {}
    except Exception:
        pass

    return render_template(
        'superadmin/dashboard.html',
        stats=stats,
        recent_tenants=recent_tenants,
        recent_activity=recent_activity,
        recent_payments=recent_payments,
        plan_breakdown=plan_breakdown,
        subscription_status=subscription_status,
        q=q,
        status_filter=status_filter,
        plan_filter=plan_filter,
        heartbeat_state=heartbeat_state,
    )

def _dashboard_export(tenants, export_format, q, status_filter, plan_filter):
    filename_base = 'tenant_export'
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = f'{filename_base}_{timestamp}.{"xlsx" if export_format == "excel" else "csv"}'
    headers = ['Tenant', 'Slug', 'Email', 'Plan', 'Status', 'Updated']

    if export_format == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        for tenant in tenants:
            writer.writerow([
                tenant.name or '',
                tenant.tenant_slug or '',
                tenant.email or '',
                tenant.plan or '',
                'Active' if tenant.is_available else 'Inactive',
                tenant.updated_at.strftime('%Y-%m-%d %H:%M:%S') if tenant.updated_at else '',
            ])
        payload = output.getvalue()
        mimetype = 'text/csv'
    else:
        output = io.StringIO()
        output.write('<table><tr>')
        for header in headers:
            output.write(f'<th>{header}</th>')
        output.write('</tr>')
        for tenant in tenants:
            output.write('<tr>')
            output.write(f'<td>{tenant.name or ""}</td>')
            output.write(f'<td>{tenant.tenant_slug or ""}</td>')
            output.write(f'<td>{tenant.email or ""}</td>')
            output.write(f'<td>{tenant.plan or ""}</td>')
            output.write(f'<td>{"Active" if tenant.is_available else "Inactive"}</td>')
            output.write(f'<td>{tenant.updated_at.strftime("%Y-%m-%d %H:%M:%S") if tenant.updated_at else ""}</td>')
            output.write('</tr>')
        output.write('</table>')
        payload = output.getvalue()
        mimetype = 'application/vnd.ms-excel'

    response = Response(payload, mimetype=mimetype)
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response

@superadmin.route('/dashboard')
def dashboard_alias():
    """Backward-compatible alias for /superadmin/dashboard."""
    return redirect(url_for('superadmin.dashboard'))

@superadmin.route('/forgot-password/request', methods=['GET', 'POST'])
@limiter.limit('3 per minute', error_message='Too many requests. Please wait a moment.')
@limiter.limit('5 per hour', error_message='Too many requests. Please try again later.')
def forgot_password_request():
    """Step 1: username + email submission → OTP dispatch (v5.6: username required)."""
    if current_user.is_authenticated:
        return redirect(url_for('superadmin.dashboard'))

    if request.method == 'POST':
        logger.info('[SUPERADMIN RESET][route] POST received at /superadmin/forgot-password/request')
        from app.services.password_reset_service import initiate_superadmin_reset
        email    = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        if not email or not username:
            logger.warning('[SUPERADMIN RESET][route] missing required field(s) — username_present=%s email_present=%s', bool(username), bool(email))
            flash('Username and email are both required.', 'danger')
            return render_template('superadmin/forgot_password_request.html')
        ok, msg = initiate_superadmin_reset(email, username)
        logger.info('[SUPERADMIN RESET][route] initiate_superadmin_reset() returned ok=%s', ok)
        flash(msg, 'info' if ok else 'danger')
        if ok:
            from flask import session as _session
            _session['_pw_reset_email'] = email
            _session['_pw_reset_type']  = 'superadmin'
            return redirect(url_for('superadmin.forgot_password_verify'))

    return render_template('superadmin/forgot_password_request.html')

@superadmin.route('/forgot-password/verify', methods=['GET', 'POST'])
@limiter.limit('5 per minute', error_message='Too many OTP attempts. Please wait.')
@limiter.limit('10 per hour', error_message='Too many OTP attempts. Please try again later.')
def forgot_password_verify():
    """Step 2: OTP entry."""
    if current_user.is_authenticated:
        return redirect(url_for('superadmin.dashboard'))
    from flask import session as _session
    email = _session.get('_pw_reset_email', '')
    if not email:
        return redirect(url_for('superadmin.forgot_password_request'))
 
    if request.method == 'POST':
        from app.services.password_reset_service import verify_superadmin_otp
        raw_otp = request.form.get('otp_code', '').strip()
        ok, msg, token = verify_superadmin_otp(email, raw_otp)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            _session['_pw_reset_token'] = token
            return redirect(url_for('superadmin.forgot_password_reset'))
 
    # FIX: was 'superadmin/forgot_password.html' — wrong template, bad resend link
    from app.services.password_reset_service import _get_ttl_minutes
    return render_template('superadmin/forgot_password_verify.html',
                           otp_ttl_minutes=_get_ttl_minutes())

@superadmin.route('/forgot-password/reset', methods=['GET', 'POST'])
@limiter.limit('5 per minute', error_message='Too many requests. Please wait a moment.')
@limiter.limit('10 per hour', error_message='Too many requests. Please try again later.')
def forgot_password_reset():
    """Step 3: new password form."""
    if current_user.is_authenticated:
        return redirect(url_for('superadmin.dashboard'))
    from flask import session as _session
    token = _session.get('_pw_reset_token', '')
    if not token:
        return redirect(url_for('superadmin.forgot_password_request'))
 
    if request.method == 'POST':
        from app.services.password_reset_service import complete_superadmin_reset
        pw  = request.form.get('password', '').strip()
        pw2 = request.form.get('password_confirm', '').strip()
        if not pw or len(pw) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('superadmin/forgot_password_reset.html')
        if pw != pw2:
            flash('Passwords do not match.', 'danger')
            return render_template('superadmin/forgot_password_reset.html')
        ok, msg = complete_superadmin_reset(token, pw)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            _session.pop('_pw_reset_email', None)
            _session.pop('_pw_reset_token', None)
            _session.pop('_pw_reset_type', None)
            return redirect(url_for('superadmin.login'))
 
    return render_template('superadmin/forgot_password_reset.html')
