"""
app/superadmin/routes/logs_monitor.py — Activity logs + subscription monitor dashboard (Phase 4b, batch 2)

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


from app.superadmin.blueprint import superadmin, superadmin_required

logger = logging.getLogger(__name__)


@superadmin.route('/logs')
@superadmin_required
def logs():
    """Cross-tenant activity & audit log with filter, pagination, and export."""
    import csv
    import json as _json
    from io import StringIO

    q           = request.args.get('q', '').strip()
    tenant_f    = request.args.get('tenant', '').strip()
    action_f    = request.args.get('action', '').strip()
    date_from   = request.args.get('date_from', '').strip()
    date_to     = request.args.get('date_to', '').strip()
    security_f  = request.args.get('security', '').strip()
    export_fmt  = request.args.get('export', '').strip().lower()
    page        = request.args.get('page', 1, type=int)
    per_page    = 50

    query = activity_log_repository.query.order_by(ActivityLog.created_at.desc())

    if tenant_f:
        query = query.filter(ActivityLog.tenant_slug == tenant_f)
    if action_f:
        query = query.filter(ActivityLog.action == action_f)
    if security_f == '1':
        query = query.filter(ActivityLog.action.in_(['login', 'logout', 'security', 'totp', '2fa']))
    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                ActivityLog.entity_name.ilike(like),
                ActivityLog.description.ilike(like),
                ActivityLog.username.ilike(like),
                ActivityLog.ip_address.ilike(like),
            )
        )
    if date_from:
        try:
            dt = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(ActivityLog.created_at >= dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc)
            query = query.filter(ActivityLog.created_at <= dt)
        except ValueError:
            pass

    # ── Export ──────────────────────────────────────────────────────────────
    if export_fmt in ('csv', 'json'):
        rows = query.limit(5000).all()
        if export_fmt == 'csv':
            buf = StringIO()
            w = csv.writer(buf)
            w.writerow(['id', 'tenant_slug', 'username', 'action', 'entity_type',
                        'entity_name', 'description', 'ip_address', 'created_at'])
            for r in rows:
                w.writerow([r.id, r.tenant_slug, r.username, r.action, r.entity_type,
                            r.entity_name, r.description, r.ip_address,
                            r.created_at.isoformat() if r.created_at else ''])
            return Response(
                buf.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': 'attachment; filename=activity_log.csv'},
            )
        else:
            data = [
                {
                    'id': r.id, 'tenant_slug': r.tenant_slug, 'username': r.username,
                    'action': r.action, 'entity_type': r.entity_type,
                    'entity_name': r.entity_name, 'description': r.description,
                    'ip_address': r.ip_address,
                    'created_at': r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
            return Response(
                _json.dumps(data, indent=2),
                mimetype='application/json',
                headers={'Content-Disposition': 'attachment; filename=activity_log.json'},
            )

    logs_page = query.paginate(page=page, per_page=per_page, error_out=False)

    # Dropdown choices
    all_tenants = (
        db.session.query(ActivityLog.tenant_slug)
        .filter(ActivityLog.tenant_slug.isnot(None))
        .distinct()
        .order_by(ActivityLog.tenant_slug)
        .all()
    )
    all_actions = (
        db.session.query(ActivityLog.action)
        .distinct()
        .order_by(ActivityLog.action)
        .all()
    )
    total_filtered = query.count()

    return render_template(
        'superadmin/logs.html',
        logs=logs_page,
        total_filtered=total_filtered,
        all_tenants=[r[0] for r in all_tenants],
        all_actions=[r[0] for r in all_actions],
        q=q, tenant_f=tenant_f, action_f=action_f,
        date_from=date_from, date_to=date_to,
        security_f=security_f,
    )

@superadmin.route('/subscription-monitor')
@superadmin_required
def subscription_monitor():
    """Unified subscription analytics and renewal monitor."""
    from datetime import datetime, timezone, timedelta
    from decimal import Decimal, InvalidOperation
    from sqlalchemy import func
    from app.models.portfolio import (
        Subscription, SubscriptionNotification, PaymentSubmission,
        WebhookEvent, Profile, normalize_plan_name,
    )
    from app.utils import get_public_billing_plans
    from app.system_plan import is_administrator_plan

    now = datetime.now(timezone.utc)
    plans = get_public_billing_plans()
    first_plan = next(iter(plans.values()), {})
    currency_symbol = first_plan.get('currency_symbol', '$') or '$'
    currency_code = (first_plan.get('currency_code', 'USD') or 'USD').upper()

    def _add_days_left(subs):
        for sub in subs:
            exp = sub.expires_at
            if exp:
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                sub.days_left = (exp - now).days
            else:
                sub.days_left = None
        return subs

    horizon_7 = now + timedelta(days=7)
    horizon_30 = now + timedelta(days=30)
    expiring_7_q = subscription_repository.query.filter(
        Subscription.status == 'active',
        Subscription.expires_at.between(now, horizon_7),
    ).order_by(Subscription.expires_at.asc())
    expiring_30_q = subscription_repository.query.filter(
        Subscription.status == 'active',
        Subscription.expires_at.between(now, horizon_30),
    ).order_by(Subscription.expires_at.asc())
    expired_q = subscription_repository.query.filter_by(status='expired').order_by(
        Subscription.expires_at.desc()
    ).limit(50)

    expiring_7 = _add_days_left(expiring_7_q.all())
    expiring_30 = _add_days_left(expiring_30_q.all())
    expired = expired_q.all()

    def _provider(sub):
        raw = str(getattr(sub, 'payment_provider', '') or '').lower()
        if raw == 'dodo' or getattr(sub, 'dodo_subscription_id', None):
            return 'dodo'
        if raw == 'paymongo' or getattr(sub, 'paymongo_subscription_id', None) or str(getattr(sub, 'payment_method', '') or '').lower() == 'paymongo':
            return 'paymongo'
        return 'manual'

    def _configured_amount(sub):
        key = normalize_plan_name(getattr(sub, 'plan', None) or '')
        data = plans.get(key) or plans.get(getattr(sub, 'plan', None)) or {}
        cycle = str(getattr(sub, 'billing_cycle', 'monthly') or 'monthly').lower()
        value = data.get('price_yearly', data.get('base_price_yearly_usd')) if cycle in ('yearly','annual','annually','year') else data.get('price_monthly', data.get('base_price_usd', data.get('price')))
        try:
            return float(Decimal(str(value or 0)))
        except (InvalidOperation, TypeError, ValueError):
            return 0.0

    # Keep only one current active subscription per tenant/provider. Duplicate
    # webhook rows must not double-count a single customer subscription.
    active_rows = Subscription.query.filter_by(status='active').order_by(
        Subscription.updated_at.desc() if hasattr(Subscription, 'updated_at') else Subscription.id.desc(),
        Subscription.id.desc(),
    ).all()
    unique_active = {}
    for sub in active_rows:
        if is_administrator_plan(getattr(sub, 'plan', None)):
            continue
        provider = _provider(sub)
        tenant_key = getattr(sub, 'tenant_id', None) or getattr(sub, 'profile_id', None) or getattr(sub, 'id', None)
        external = getattr(sub, 'dodo_subscription_id', None) or getattr(sub, 'paymongo_subscription_id', None)
        key = (provider, external or tenant_key)
        unique_active.setdefault(key, sub)

    provider_revenue = {'dodo': 0.0, 'paymongo': 0.0, 'manual': 0.0}
    provider_active = {'dodo': 0, 'paymongo': 0, 'manual': 0}
    provider_original = {'dodo': [], 'paymongo': [], 'manual': []}
    mrr = 0.0
    for sub in unique_active.values():
        provider = _provider(sub)
        amount = _configured_amount(sub)
        provider_revenue[provider] += amount
        provider_active[provider] += 1
        monthly = amount / 12.0 if str(getattr(sub, 'billing_cycle', 'monthly') or 'monthly').lower() in ('yearly','annual','annually','year') else amount
        mrr += monthly
        raw_amount = float(getattr(sub, 'amount_paid', 0) or 0)
        raw_currency = str(getattr(sub, 'provider_currency', '') or '').upper()
        if raw_amount and raw_currency and raw_currency != currency_code:
            provider_original[provider].append({'amount': raw_amount, 'currency': raw_currency})

    # Approved manual submissions are counted once by record id.
    approved_manual = PaymentSubmission.query.filter(
        PaymentSubmission.status.in_(['approved', 'paid', 'completed'])
    ).all()
    manual_seen = set()
    manual_total = 0.0
    for payment in approved_manual:
        key = getattr(payment, 'provider_payment_id', None) or getattr(payment, 'transaction_reference', None) or payment.id
        if key in manual_seen:
            continue
        manual_seen.add(key)
        if currency_code == 'USD' and getattr(payment, 'amount_usd', None) is not None:
            manual_total += float(payment.amount_usd or 0)
        elif str(getattr(payment, 'currency_code', '') or '').upper() in ('', currency_code):
            manual_total += float(getattr(payment, 'amount_paid', 0) or 0)
    provider_revenue['manual'] = manual_total

    total_active = len(unique_active)
    total_expired = Subscription.query.filter_by(status='expired').count()
    total_pending_checkout = Subscription.query.filter_by(status='pending').count()
    total_pending_review = PaymentSubmission.query.filter_by(status='pending').count()
    total_pending = total_pending_checkout + total_pending_review
    total_trial = Profile.query.filter(func.lower(Profile.plan) == 'trial').count()
    total_revenue = round(sum(provider_revenue.values()), 2)
    revenue_share = {k: round(v / total_revenue * 100, 1) if total_revenue else 0 for k, v in provider_revenue.items()}

    recent_webhooks = WebhookEvent.query.order_by(WebhookEvent.received_at.desc()).limit(20).all()
    since = now - timedelta(days=30)
    webhook_count = WebhookEvent.query.filter(WebhookEvent.received_at >= since).count()
    webhook_processed = WebhookEvent.query.filter(WebhookEvent.received_at >= since, WebhookEvent.processed.is_(True)).count()
    webhook_health = round(webhook_processed / webhook_count * 100, 1) if webhook_count else 100.0

    metrics = {
        'total_active': total_active,
        'total_expiring': expiring_7_q.count(),
        'total_expired': total_expired,
        'total_pending': total_pending,
        'total_pending_review': total_pending_review,
        'total_pending_checkout': total_pending_checkout,
        'total_trial': total_trial,
        'total_revenue': total_revenue,
        'mrr': round(mrr, 2),
        'arr': round(mrr * 12, 2),
    }

    recent_notifications = subscription_notification_repository.query.filter(
        SubscriptionNotification.notification_type != 'manual'
    ).order_by(SubscriptionNotification.created_at.desc()).limit(30).all()

    class _CountList(list):
        def count(self): return len(self)
    expiring_7 = _CountList(expiring_7)
    expiring_30 = _CountList(expiring_30)
    expired = _CountList(expired)

    return render_template(
        'superadmin/subscription_monitor.html',
        metrics=metrics,
        provider_revenue=provider_revenue,
        provider_active=provider_active,
        provider_original=provider_original,
        revenue_share=revenue_share,
        currency_symbol=currency_symbol,
        currency_code=currency_code,
        recent_webhooks=recent_webhooks,
        webhook_health=webhook_health,
        webhook_count=webhook_count,
        expiring_7=expiring_7,
        expiring_30=expiring_30,
        expired=expired,
        recent_notifications=recent_notifications,
        now=now,
    )
