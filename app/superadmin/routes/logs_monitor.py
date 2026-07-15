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
    from app.services.analytics.dashboard_analytics_service import build_superadmin_analytics
    from app.models.portfolio import Subscription
    from app.services.notification_service import list_recent_billing_activity

    analytics = build_superadmin_analytics()
    recent_notifications = list_recent_billing_activity(limit=30)

    class _CountList(list):
        def count(self):
            return len(self)

    return render_template(
        'superadmin/subscription_monitor.html',
        metrics=analytics['metrics'],
        provider_revenue=analytics['provider_revenue'],
        provider_active=analytics['provider_active'],
        provider_original=analytics['provider_original'],
        revenue_share=analytics['revenue_share'],
        provider_mix=analytics['provider_mix'],
        currency_symbol=analytics['currency_symbol'],
        currency_code=analytics['currency_code'],
        recent_webhooks=analytics['recent_webhooks'],
        webhook_health=analytics['webhook_health'],
        webhook_count=analytics['webhook_count'],
        expiring_7=_CountList(analytics['expiring_7']),
        expiring_30=_CountList(analytics['expiring_30']),
        expired=_CountList(Subscription.query.filter_by(status='expired').order_by(Subscription.expires_at.desc()).limit(50).all()),
        recent_notifications=recent_notifications,
        now=analytics['generated_at'],
        analytics=analytics,
    )
