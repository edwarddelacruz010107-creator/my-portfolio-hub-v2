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
from decimal import Decimal, InvalidOperation
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

    # Display plan analytics using the effective runtime plan, so active trials
    # do not appear as Basic just because Profile.plan has a fallback value.
    plan_breakdown = {}
    try:
        for profile in profile_repository.query.all():
            try:
                if has_administrator_access(profile):
                    label = 'Administrator'
                elif profile.is_trial_active() or ((getattr(getattr(profile, 'tenant', None), 'subscription_state', '') or '').strip().lower() == 'trial'):
                    label = 'Trial'
                else:
                    raw = profile.effective_plan() if callable(getattr(profile, 'effective_plan', None)) else (profile.plan or 'Basic')
                    label = {'starter': 'Basic', 'business': 'Business', 'pro': 'Pro', 'enterprise': 'Enterprise', 'trial': 'Trial', 'administrator': 'Administrator'}.get((raw or '').lower(), str(raw).title())
            except Exception:
                label = {'starter': 'Basic', 'business': 'Business', 'pro': 'Pro', 'enterprise': 'Enterprise', 'trial': 'Trial', 'administrator': 'Administrator'}.get((profile.plan or '').lower(), profile.plan or 'Unknown')
            plan_breakdown[label] = plan_breakdown.get(label, 0) + 1
    except Exception:
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

    # Currency and revenue analytics use the same normalization rules as the
    # dedicated subscription analytics page. Gateway charges may be localized
    # (for example PHP 65.98 for a USD 1.00 product), so raw provider amounts
    # must never be rendered under the plan-settings currency symbol.
    _any_plan = next(iter(BILLING_PLANS.values()), {})
    currency_symbol = _any_plan.get('currency_symbol', '$') or '$'
    currency_code = (_any_plan.get('currency_code', 'USD') or 'USD').upper()

    def _configured_plan_amount(sub):
        plan_key = normalize_plan_name(getattr(sub, 'plan', None) or '')
        plan_data = BILLING_PLANS.get(plan_key) or BILLING_PLANS.get(getattr(sub, 'plan', None)) or {}
        cycle = str(getattr(sub, 'billing_cycle', 'monthly') or 'monthly').lower()
        if cycle in ('yearly', 'annual', 'annually', 'year'):
            value = plan_data.get('price_yearly', plan_data.get('base_price_yearly_usd'))
        else:
            value = plan_data.get('price_monthly', plan_data.get('base_price_usd', plan_data.get('price')))
        try:
            return float(Decimal(str(value or 0)))
        except (InvalidOperation, TypeError, ValueError):
            return 0.0

    def _normalized_subscription_amount(sub):
        """Return revenue in the Plan Settings currency.

        Dodo may store the localized checkout amount (for example PHP 65.98)
        in ``amount_paid`` while older rows have no reliable currency snapshot.
        For automated subscriptions, the configured plan price is therefore
        the authoritative analytics amount. This prevents a localized charge
        from being displayed as USD 65.98 when the plan price is USD 1.00.
        """
        provider = str(getattr(sub, 'payment_provider', '') or '').strip().lower()
        is_dodo = provider == 'dodo' or bool(getattr(sub, 'dodo_subscription_id', None))
        is_paymongo = (
            provider == 'paymongo'
            or bool(getattr(sub, 'paymongo_subscription_id', None))
            or str(getattr(sub, 'payment_method', '') or '').strip().lower() == 'paymongo'
        )

        configured = _configured_plan_amount(sub)
        if (is_dodo or is_paymongo) and configured > 0:
            return configured

        raw = float(getattr(sub, 'amount_paid', 0) or 0)
        source_currency = str(getattr(sub, 'provider_currency', '') or '').upper()
        if source_currency and source_currency != currency_code.upper():
            return configured if configured > 0 else 0.0
        return raw

    all_subscriptions = Subscription.query.all()
    provider_revenue = {'dodo': 0.0, 'paymongo': 0.0, 'manual': 0.0}
    provider_active = {'dodo': 0, 'paymongo': 0, 'manual': 0}
    monthly_recurring_revenue = 0.0

    for sub in all_subscriptions:
        if str(getattr(sub, 'plan', '') or '').strip().lower() == 'administrator':
            continue
        provider = (
            'dodo' if getattr(sub, 'payment_provider', '') == 'dodo' or getattr(sub, 'dodo_subscription_id', None)
            else 'paymongo' if getattr(sub, 'payment_provider', '') == 'paymongo' or getattr(sub, 'paymongo_subscription_id', None) or getattr(sub, 'payment_method', '') == 'paymongo'
            else 'manual'
        )
        if getattr(sub, 'status', '') == 'active':
            provider_active[provider] += 1
            recurring = _configured_plan_amount(sub) or _normalized_subscription_amount(sub)
            if str(getattr(sub, 'billing_cycle', 'monthly') or 'monthly').lower() in ('yearly', 'annual', 'annually', 'year'):
                recurring = recurring / 12.0
            monthly_recurring_revenue += recurring
        if provider in ('dodo', 'paymongo'):
            provider_revenue[provider] += _normalized_subscription_amount(sub)

    approved_manual = PaymentSubmission.query.filter(
        PaymentSubmission.status.in_(['approved', 'paid', 'completed'])
    ).all()
    for payment in approved_manual:
        payment_currency = str(getattr(payment, 'currency_code', '') or '').upper()
        if currency_code == 'USD' and getattr(payment, 'amount_usd', None) is not None:
            provider_revenue['manual'] += float(payment.amount_usd or 0)
        elif not payment_currency or payment_currency == currency_code:
            provider_revenue['manual'] += float(payment.amount_paid or 0)

    recorded_revenue = round(sum(provider_revenue.values()), 2)
    active_rate = round((active_tenants / total_tenants * 100), 1) if total_tenants else 0.0
    churn_rate = round((expired_accounts / max(len(all_subscriptions), 1)) * 100, 1)

    # Six complete/current calendar months for compact trend charts.
    def _month_floor(value):
        return datetime(value.year, value.month, 1, tzinfo=timezone.utc)

    def _shift_month(value, delta):
        absolute = value.year * 12 + (value.month - 1) + delta
        return datetime(absolute // 12, absolute % 12 + 1, 1, tzinfo=timezone.utc)

    current_month = _month_floor(today)
    month_starts = [_shift_month(current_month, offset) for offset in range(-5, 1)]
    revenue_by_month = [0.0 for _ in month_starts]
    tenants_by_month = [0 for _ in month_starts]

    for sub in all_subscriptions:
        created = getattr(sub, 'created_at', None)
        if not created:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        for idx, start in enumerate(month_starts):
            end = _shift_month(start, 1)
            if start <= created < end and (getattr(sub, 'payment_provider', '') in ('dodo', 'paymongo') or getattr(sub, 'dodo_subscription_id', None) or getattr(sub, 'paymongo_subscription_id', None)):
                revenue_by_month[idx] += _normalized_subscription_amount(sub)
                break

    for payment in approved_manual:
        created = getattr(payment, 'reviewed_at', None) or getattr(payment, 'submitted_at', None)
        if not created:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        value = float(payment.amount_usd or 0) if currency_code == 'USD' and getattr(payment, 'amount_usd', None) is not None else float(payment.amount_paid or 0)
        for idx, start in enumerate(month_starts):
            if start <= created < _shift_month(start, 1):
                revenue_by_month[idx] += value
                break

    try:
        all_tenants_for_growth = tenant_repository.query.all()
        for tenant in all_tenants_for_growth:
            created = getattr(tenant, 'created_at', None)
            if not created:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            for idx, start in enumerate(month_starts):
                if start <= created < _shift_month(start, 1):
                    tenants_by_month[idx] += 1
                    break
    except Exception:
        pass

    chart_max = max(revenue_by_month) if revenue_by_month else 0
    revenue_chart = []
    for idx, (start, value) in enumerate(zip(month_starts, revenue_by_month)):
        x = 8 + (idx * (84 / max(len(month_starts) - 1, 1)))
        y = 84 - ((value / chart_max) * 66 if chart_max else 0)
        revenue_chart.append({
            'label': start.strftime('%b'),
            'value': round(value, 2),
            'x': round(x, 2),
            'y': round(y, 2),
        })
    revenue_polyline = ' '.join(f"{point['x']},{point['y']}" for point in revenue_chart)
    revenue_area = f"8,90 {revenue_polyline} 92,90" if revenue_polyline else ''
    tenant_chart_max = max(tenants_by_month) if tenants_by_month else 0
    tenant_growth_chart = [
        {
            'label': start.strftime('%b'),
            'value': value,
            'height': round((value / tenant_chart_max) * 100, 1) if tenant_chart_max else 0,
        }
        for start, value in zip(month_starts, tenants_by_month)
    ]

    provider_total = sum(provider_revenue.values())
    provider_mix = {
        key: {
            'amount': round(value, 2),
            'active': provider_active.get(key, 0),
            'share': round((value / provider_total * 100), 1) if provider_total else 0.0,
        }
        for key, value in provider_revenue.items()
    }

    stats = {
        'total_tenants': total_tenants,
        'active_tenants': active_tenants,
        'active_rate': active_rate,
        'revenue': recorded_revenue,
        'mrr': round(monthly_recurring_revenue, 2),
        'currency_symbol': currency_symbol,
        'currency_code': currency_code,
        'pending_payments': pending_payments,
        'expiring_accounts': expiring_accounts,
        'expired_accounts': expired_accounts,
        'churn_rate': churn_rate,
    }

    # Shared source of truth: Platform Overview uses the exact same billing,
    # provider, MRR, churn, and trend calculations as Subscription Monitor.
    from app.services.analytics.dashboard_analytics_service import build_superadmin_analytics
    analytics = build_superadmin_analytics()
    shared = analytics['metrics']
    stats.update({
        'total_tenants': shared['total_tenants'],
        'active_tenants': shared['active_tenants'],
        'active_rate': shared['active_rate'],
        'revenue': shared['total_revenue'],
        'mrr': shared['mrr'],
        'currency_symbol': analytics['currency_symbol'],
        'currency_code': analytics['currency_code'],
        'pending_payments': shared['total_pending'],
        'expiring_accounts': shared['expiring_30'],
        'expired_accounts': shared['total_expired'] + shared['total_cancelled'],
        'churn_rate': shared['churn_rate'],
    })
    provider_mix = analytics['provider_mix']
    revenue_chart = analytics['revenue_chart']
    revenue_polyline = analytics['revenue_polyline']
    revenue_area = analytics['revenue_area']
    tenant_growth_chart = analytics['tenant_growth_chart']

    # ── Monitoring: superadmin-only ops data ─────────────────────────────────
    heartbeat_state: dict = {}
    try:
        from app.heartbeat import get_heartbeat_state
        heartbeat_state = get_heartbeat_state() or {}
    except Exception:
        heartbeat_state = {}

    # Dashboard monitor should show live app/database state even when no external
    # BetterStack/self-ping has hit /heartbeat yet. The previous card depended
    # only on the in-memory heartbeat state, so a healthy deployed app could
    # incorrectly show "Not checked yet" until a monitor ping arrived.
    try:
        db.session.execute(db.text('SELECT 1'))
        db.session.remove()
        heartbeat_state['db_ok'] = True
        heartbeat_state['db_detail'] = 'Connected via live dashboard probe'
    except Exception as exc:
        try:
            db.session.rollback()
            db.session.remove()
        except Exception:
            pass
        heartbeat_state['db_ok'] = False
        heartbeat_state['db_detail'] = str(exc)

    try:
        start_time = heartbeat_state.get('start_time')
        if start_time is not None:
            heartbeat_state['uptime_seconds'] = max(0, int(time.monotonic() - float(start_time)))
    except Exception:
        pass
    heartbeat_state['dashboard_checked_at'] = datetime.now(timezone.utc).isoformat()

    # Make recent tenant plan labels reflect the active subscription state, not
    # only the stored profile.plan fallback. Trial users should show Trial.
    recent_tenant_plan_labels = {}
    recent_tenant_trial_days = {}
    for tenant in recent_tenants:
        try:
            if has_administrator_access(tenant):
                recent_tenant_plan_labels[tenant.tenant_slug] = 'Administrator'
                recent_tenant_trial_days[tenant.tenant_slug] = None
                continue
            tenant_obj = getattr(tenant, 'tenant', None)
            state = (getattr(tenant_obj, 'subscription_state', '') or '').strip().lower() if tenant_obj else ''
            if state == 'trial' or tenant.is_trial_active():
                recent_tenant_plan_labels[tenant.tenant_slug] = 'Trial'
                recent_tenant_trial_days[tenant.tenant_slug] = tenant.trial_days_remaining()
            else:
                label = tenant.effective_plan() if callable(getattr(tenant, 'effective_plan', None)) else (tenant.plan or 'Basic')
                label = {'starter': 'Basic', 'business': 'Business', 'pro': 'Pro', 'enterprise': 'Enterprise', 'trial': 'Trial', 'administrator': 'Administrator'}.get((label or '').lower(), str(label).title())
                recent_tenant_plan_labels[tenant.tenant_slug] = label
                recent_tenant_trial_days[tenant.tenant_slug] = None
        except Exception:
            recent_tenant_plan_labels[tenant.tenant_slug] = tenant.plan or 'Basic'
            recent_tenant_trial_days[tenant.tenant_slug] = None

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
        recent_tenant_plan_labels=recent_tenant_plan_labels,
        recent_tenant_trial_days=recent_tenant_trial_days,
        provider_mix=provider_mix,
        revenue_chart=revenue_chart,
        revenue_polyline=revenue_polyline,
        revenue_area=revenue_area,
        tenant_growth_chart=tenant_growth_chart,
        analytics=analytics,
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
