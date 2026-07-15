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
import time
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for,
    abort, flash, request, session, current_app, Response,
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
                                   ActivityLog, Project, Inquiry, Tenant, PaymentInstruction, PAID_PLAN_NAMES)


from app.utils import log_activity
from app.security import log_security_event
from app.tenant_security import RESERVED_SLUGS, validate_slug, stamp_session_tenant
from app.models.portfolio import TenantCommunicationSettings
from app.models.portfolio import _utcnow
from app.system_plan import has_administrator_access
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
    """Restricted Phase 9 founder command center; all metrics come from one assembler."""
    from app.services.founder.access_control import has_founder_capability
    from app.services.founder.domain import FounderFilters
    from app.services.founder.dashboard_service import build_founder_dashboard

    if not has_founder_capability(current_user):
        abort(403)
    if request.args.get('export'):
        flash('Dashboard exports now require a recent password and TOTP reauthentication.', 'warning')
        return redirect(url_for('superadmin.founder_reauth'))
    filters = FounderFilters.from_mapping(request.args)
    founder = build_founder_dashboard(filters=filters)
    return render_template(
        'superadmin/dashboard.html',
        founder=founder,
        filters=founder['filters'],
        export_reauthenticated=request.args.get('reauth') == 'ok',
    )


@superadmin.route('/founder/reauth', methods=['GET', 'POST'])
@superadmin_required
@limiter.limit('5 per minute')
def founder_reauth():
    """Require current password plus a fresh, non-replayed TOTP before export."""
    from app.services.founder.access_control import (
        FOUNDER_EXPORT_CAPABILITY,
        has_founder_capability,
        mark_strong_reauth,
    )

    if not has_founder_capability(current_user, FOUNDER_EXPORT_CAPABILITY):
        abort(403)
    if not current_user.totp_enabled:
        flash('Enable TOTP before using founder dashboard exports.', 'danger')
        return redirect(url_for('superadmin.setup_2fa'))
    if request.method == 'POST':
        password = request.form.get('password') or ''
        totp_code = (request.form.get('totp_code') or '').strip()
        if current_user.verify_password(password) and current_user.verify_totp(totp_code):
            db.session.commit()
            mark_strong_reauth(session, current_user)
            log_security_event(
                'founder_export_reauth', current_user,
                'Strong reauthentication completed for aggregate founder export', 'info',
            )
            return redirect(url_for('superadmin.dashboard', reauth='ok'))
        db.session.rollback()
        log_security_event(
            'founder_export_reauth_failed', current_user,
            'Strong reauthentication failed for founder export', 'warning',
        )
        flash('Password or authenticator code was not accepted.', 'danger')
    return render_template('superadmin/founder_reauth.html', page_title='Confirm founder export')


@superadmin.route('/founder/export.csv', methods=['POST'])
@superadmin_required
@limiter.limit('10 per hour')
def founder_export():
    """Bounded aggregate export with explicit capability, reauth, and audit."""
    from app.services.founder.access_control import (
        FOUNDER_EXPORT_CAPABILITY,
        has_founder_capability,
        has_recent_strong_reauth,
    )
    from app.services.founder.domain import FounderFilters
    from app.services.founder.dashboard_service import build_founder_dashboard

    if not has_founder_capability(current_user, FOUNDER_EXPORT_CAPABILITY):
        abort(403)
    if not has_recent_strong_reauth(session, current_user):
        flash('Reauthenticate with your password and TOTP before exporting.', 'warning')
        return redirect(url_for('superadmin.founder_reauth'))
    filters = FounderFilters.from_mapping(request.form)
    founder = build_founder_dashboard(filters=filters)
    rows = [
        ('dashboard_version', founder['version']),
        ('generated_at_utc', founder['generated_at'].isoformat()),
        ('range_start_utc', founder['periods']['start_at'].isoformat()),
        ('range_end_utc', founder['periods']['end_at'].isoformat()),
        ('plan_filter', filters.plan),
        ('payment_provider_filter', filters.payment_provider),
        ('ai_provider_filter', filters.ai_provider),
        ('tenant_signups', founder['lifecycle']['signups']),
        ('activation_events', founder['lifecycle']['activation_events']),
        ('active_subscriptions', founder['lifecycle']['active_subscriptions']),
        ('trial_tenants', founder['lifecycle']['trial_tenants']),
        ('conversion_percent', founder['lifecycle']['conversion']['value'] if founder['lifecycle']['conversion']['available'] else 'unavailable'),
        ('churn_percent', founder['lifecycle']['churn']['value'] if founder['lifecycle']['churn']['available'] else 'unavailable'),
        ('gross_cash_usd', founder['financial']['gross_cash_revenue']),
        ('net_cash_usd', founder['financial']['net_cash_revenue']),
        ('refunds_usd', founder['financial']['refunds']),
        ('mrr_usd', founder['financial']['mrr']),
        ('arr_usd', founder['financial']['arr']),
        ('published_portfolios', founder['portfolio']['published_portfolios']),
        ('published_projects', founder['portfolio']['published_projects']),
        ('contact_inquiries', founder['portfolio']['contacts']['inquiries']),
        ('ai_requests', founder['ai']['requests']),
        ('ai_failures', founder['ai']['failures']),
        ('ai_known_cost_microunits', founder['ai']['known_cost_microunits'] if founder['ai']['known_cost_microunits'] is not None else 'unavailable'),
        ('ai_unavailable_cost_count', founder['ai']['unavailable_cost_count']),
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(('metric', 'value'))
    writer.writerows(rows)
    db.session.add(ActivityLog(
        user_id=int(current_user.id),
        username=current_user.username,
        action='export',
        entity_type='founder_dashboard',
        entity_name=founder['version'],
        description=f'Aggregate founder dashboard CSV export ({filters.cache_fragment()})',
        ip_address=request.remote_addr,
    ))
    db.session.commit()
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename="founder-dashboard.csv"'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
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
