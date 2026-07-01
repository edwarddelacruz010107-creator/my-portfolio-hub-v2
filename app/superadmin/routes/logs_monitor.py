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
    """
    Monitoring dashboard: Expiring in 7d, 30d, Expired, Pending, Revenue.
    """
    from datetime import datetime, timezone, timedelta
    from app.models.portfolio import Subscription, SubscriptionNotification
    from sqlalchemy import func

    now = datetime.now(timezone.utc)

    # Build queries with computed days_left as Python-side attribute
    def _add_days_left(subs):
        for s in subs:
            exp = s.expires_at
            if exp:
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                s.days_left = (exp - now).days
            else:
                s.days_left = None
        return subs

    horizon_7  = now + timedelta(days=7)
    horizon_30 = now + timedelta(days=30)

    expiring_7_q = (
        subscription_repository.query
        .filter(Subscription.status == 'active')
        .filter(Subscription.expires_at.between(now, horizon_7))
        .order_by(Subscription.expires_at.asc())
    )
    expiring_30_q = (
        subscription_repository.query
        .filter(Subscription.status == 'active')
        .filter(Subscription.expires_at.between(now, horizon_30))
        .order_by(Subscription.expires_at.asc())
    )
    expired_q = (
        subscription_repository.query
        .filter(Subscription.status == 'expired')
        .order_by(Subscription.expires_at.desc())
        .limit(50)
    )

    # Eager-load for display
    expiring_7  = _add_days_left(expiring_7_q.all())
    expiring_30 = _add_days_left(expiring_30_q.all())
    expired     = expired_q.all()

    # Metrics
    total_active   = subscription_repository.query.filter_by(status='active').count()
    total_expiring = expiring_7_q.count()
    total_expired  = subscription_repository.query.filter_by(status='expired').count()
    total_pending  = subscription_repository.query.filter_by(status='pending').count()
    revenue_row    = db.session.query(func.sum(Subscription.amount_paid)).filter_by(status='active').scalar()
    total_revenue  = float(revenue_row or 0)

    metrics = {
        'total_active':   total_active,
        'total_expiring': total_expiring,
        'total_expired':  total_expired,
        'total_pending':  total_pending,
        'total_revenue':  total_revenue,
    }

    recent_notifications = (
        subscription_notification_repository.query
        .filter(SubscriptionNotification.notification_type != 'manual')
        .order_by(SubscriptionNotification.created_at.desc())
        .limit(30)
        .all()
    )

    # Patch .count() onto list objects for template compatibility
    class _CountList(list):
        def count(self): return len(self)
    expiring_7  = _CountList(expiring_7)
    expiring_30 = _CountList(expiring_30)
    expired     = _CountList(expired)

    return render_template(
        'superadmin/subscription_monitor.html',
        metrics=metrics,
        expiring_7=expiring_7,
        expiring_30=expiring_30,
        expired=expired,
        recent_notifications=recent_notifications,
        now=now,
    )
