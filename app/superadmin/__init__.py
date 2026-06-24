"""
app/superadmin/__init__.py — Superadmin dashboard blueprint (v3.1)

FIXES from v3.0:
  • superadmin_required decorator: redirect to 'superadmin.login' instead of
    url_for('root') to avoid circular import / missing endpoint errors.
  • login() route: fixed redirect target — uses url_for('root') only after
    confirming it exists, otherwise falls back to '/'.
  • logout() delegates to auth blueprint correctly.
  • All url_for('root') calls replaced with url_for('root') guarded fallback.

Routes:
  GET/POST /superadmin/login                    — Superadmin login
  GET      /superadmin/                         — Platform dashboard
  GET      /superadmin/tenants                  — Tenant list
  GET/POST /superadmin/tenants/new              — Create tenant + owner user
  GET/POST /superadmin/tenants/<id>/edit        — Edit tenant
  POST     /superadmin/tenants/<id>/delete      — Delete tenant
  GET/POST /superadmin/settings                 — Superadmin settings
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


def _normalize_timestamp(value):
    """Return a UTC-aware datetime for arithmetic, preserving UTC if naive."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
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

logger = logging.getLogger(__name__)
superadmin = Blueprint('superadmin', __name__)


# ── Auth guard ────────────────────────────────────────────────────────────────

def superadmin_required(f):
    """Decorator: requires authenticated superadmin. Safe redirect on failure."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            # FIX: always redirect to superadmin.login, not 'root'
            return redirect(url_for('superadmin.login', next=request.url))
        if not current_user.is_superadmin:
            flash('Superadmin access required.', 'danger')
            # FIX: safe fallback — 'root' endpoint is defined in create_app
            return redirect(_safe_root())
        return f(*args, **kwargs)
    return decorated


def _safe_root():
    """Return root URL safely without risking BuildError."""
    try:
        return url_for('root')
    except Exception:
        return '/'


# ── Context processor ─────────────────────────────────────────────────────────

@superadmin.context_processor
def inject_tenant_count():
    try:
        count = Profile.query.count()
    except Exception:
        count = 0
    return dict(tenant_count=count)


# ── Slug helper ───────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def _generate_license_key(plan: str, slug: str) -> str:
    """Delegate to shared utils.generate_license_key."""
    return _utils_generate_license_key(plan, slug)


def _license_plan_details(plan: str) -> dict:
    return BILLING_PLANS.get(normalize_plan_name(plan), BILLING_PLANS['Basic'])


def _license_expiration_info(profile):
    plan_name = profile.license_plan or profile.plan or 'Basic'
    details = _license_plan_details(plan_name)
    expires_at = None
    expires_in_days = None
    activated_at = _normalize_timestamp(profile.license_activated_at)
    if profile.license_active and activated_at:
        expires_at = activated_at + timedelta(days=details['duration_days'])
        expires_in_days = max(0, (expires_at - datetime.now(timezone.utc)).days)
    return details, expires_at, expires_in_days


# ── Login / Logout ────────────────────────────────────────────────────────────

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
    )


@superadmin.route('/logout')
def logout():
    """Superadmin logout — clears session and redirects to superadmin login."""
    if current_user.is_authenticated:
        log_activity('logout', 'user', current_user.username)
    session.pop('totp_verified', None)
    session.pop('tenant_slug', None)
    logout_user()
    flash('Signed out from superadmin.', 'info')
    return redirect(url_for('superadmin.login'))


# ── Superadmin OTP Forgot-Password ───────────────────────────────────────────
# FIX HP#3: Legacy in-memory OTP store REMOVED.
# The old /superadmin/forgot-password route used a dict-based in-memory OTP
# that was not persistent, not rate-limited by Flask-Limiter, and reset on
# every worker restart (invisible to health checks).
#
# The replacement DB-backed flow lives at:
#   /superadmin/forgot-password/request  → forgot_password_request()
#   /superadmin/forgot-password/verify   → forgot_password_verify()
#   /superadmin/forgot-password/reset    → forgot_password_reset()
#
# The old route is now a permanent redirect to the new flow.


@superadmin.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """
    Legacy entry point — redirects to the new DB-backed flow (HTTP 301).
    Kept so any existing bookmarks or links continue to work.
    """
    return redirect(url_for('superadmin.forgot_password_request'), code=301)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@superadmin.route('/')
@superadmin_required
def dashboard():
    q = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all').strip()
    plan_filter = request.args.get('plan', 'all').strip()
    export_format = request.args.get('export', '').strip().lower()

    total_tenants = Profile.query.count()
    active_tenants = Profile.query.filter(Profile.is_available == True).count()   # noqa: E712
    revenue = db.session.query(func.coalesce(func.sum(Subscription.amount_paid), 0.0))
    revenue = revenue.filter(Subscription.status == 'active').scalar() or 0.0
    pending_payments = PaymentSubmission.query.filter_by(status='pending').count()

    today = datetime.now(timezone.utc)
    expiring_threshold = today + timedelta(days=30)
    expiring_accounts = (
        Subscription.query
        .filter(
            Subscription.status == 'active',
            Subscription.expires_at.isnot(None),
            Subscription.expires_at > today,
            Subscription.expires_at <= expiring_threshold,
        )
        .count()
    )
    expired_accounts = (
        Subscription.query
        .filter(Subscription.status.in_(['expired', 'cancelled']))
        .count()
    )

    query = Profile.query.order_by(Profile.updated_at.desc())
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
        ActivityLog.query
        .order_by(ActivityLog.created_at.desc())
        .limit(6)
        .all()
    )
    recent_payments = (
        PaymentSubmission.query
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

# ── Tenant list ───────────────────────────────────────────────────────────────

@superadmin.route('/tenants')
@superadmin_required
def tenants():
    q             = request.args.get('q', '').strip()
    status_filter = request.args.get('status', 'all').strip()
    page          = request.args.get('page', 1, type=int)

    query = Profile.query.order_by(Profile.updated_at.desc())

    if q:
        query = query.filter(
            or_(
                Profile.name.ilike(f'%{q}%'),
                Profile.tenant_slug.ilike(f'%{q}%'),
                Profile.email.ilike(f'%{q}%'),
            )
        )

    if status_filter == 'active':
        try:
            query = query.filter(Profile.is_available == True)   # noqa: E712
        except Exception:
            pass
    elif status_filter == 'inactive':
        try:
            query = query.filter(Profile.is_available == False)   # noqa: E712
        except Exception:
            pass

    tenant_page = query.paginate(page=page, per_page=15, error_out=False)

    slugs = [t.tenant_slug for t in tenant_page.items]

    project_counts = {}
    if slugs:
        rows = (
            db.session.query(Project.tenant_slug, func.count(Project.id))
            .filter(Project.tenant_slug.in_(slugs))
            .group_by(Project.tenant_slug)
            .all()
        )
        project_counts = {r[0]: r[1] for r in rows}

    tenant_owners = {}
    if slugs:
        owners = (
            User.query
            .filter(User.tenant_slug.in_(slugs), User.is_admin == True)   # noqa: E712
            .all()
        )
        for o in owners:
            if o.tenant_slug not in tenant_owners:
                tenant_owners[o.tenant_slug] = o

    try:
        active_count = Profile.query.filter(Profile.is_available == True).count()   # noqa: E712
    except Exception:
        active_count = tenant_page.total

    days_active_map = {}
    now = datetime.now(timezone.utc)
    for tenant in tenant_page.items:
        updated_at = _normalize_timestamp(tenant.updated_at)
        if updated_at:
            delta = now - updated_at
            days_active_map[tenant.tenant_slug] = max(0, delta.days)
        else:
            days_active_map[tenant.tenant_slug] = None

    return render_template(
        'superadmin/tenants.html',
        tenants=tenant_page,
        q=q,
        status_filter=status_filter,
        project_counts=project_counts,
        tenant_owners=tenant_owners,
        active_count=active_count,
        days_active_map=days_active_map,
    )


@superadmin.route('/messages/send', methods=['GET', 'POST'])
@superadmin_required
def send_message():
    form = SuperadminMessageForm()
    tenants = Profile.query.order_by(Profile.name.asc()).all()
    form.tenant_slug.choices = [
        ('all', 'All Tenants'),
        *[(t.tenant_slug, f"{t.name or t.tenant_slug} ({t.tenant_slug})") for t in tenants]
    ]

    if request.method == 'GET':
        requested_tenant = request.args.get('tenant_slug')
        if requested_tenant:
            form.tenant_slug.data = requested_tenant

    if form.validate_on_submit():
        selected = form.tenant_slug.data
        type_label = {
            'alert': 'Alert',
            'billing': 'Billing Update',
            'maintenance': 'Maintenance Notice',
            'account': 'Account Reminder',
            'general': 'General Message',
        }.get(form.message_type.data, 'Message')

        formatted_subject = f"[{type_label}] {form.subject.data.strip()}"
        recipients = []
        if selected == 'all':
            recipients = tenants
        else:
            tenant = Profile.query.filter_by(tenant_slug=selected).first()
            if not tenant:
                flash('Selected tenant not found.', 'danger')
                return render_template('superadmin/send_message.html', form=form)
            recipients = [tenant]

        if not recipients:
            flash('No tenants available to receive the message.', 'danger')
            return render_template('superadmin/send_message.html', form=form)

        inquiries = []
        for recipient in recipients:
            inquiries.append(Inquiry(
                tenant_slug=recipient.tenant_slug,
                name=current_user.username,
                email=current_user.email or 'superadmin@platform',
                subject=formatted_subject,
                message=form.message.data.strip(),
                sender='superadmin',
                is_read=False,
            ))

        db.session.add_all(inquiries)
        db.session.commit()

        log_activity('create', 'inquiry', selected,
                     f"Superadmin sent {type_label.lower()} message to {selected}")

        if selected == 'all':
            flash('Message broadcast to all tenants.', 'success')
        else:
            flash('Message sent to tenant admin.', 'success')

        return redirect(url_for('superadmin.send_message', tenant_slug=selected))

    return render_template('superadmin/send_message.html', form=form)


# ── Superadmin Inbox (v3.8) ───────────────────────────────────────────────────
# Lists ALL messages — both superadmin-sent and tenant-sent replies.
# Grouped by tenant for easy per-tenant triage.

@superadmin.route('/messages')
@superadmin_required
def messages_inbox():
    """
    Superadmin inbox: shows all Inquiry threads across all tenants.
    Tabs: All | From Tenants | Sent by Me | Unread
    """
    from app.models.portfolio import InquiryReply
    tab    = request.args.get('tab', 'all')
    search = request.args.get('q', '').strip()
    page   = request.args.get('page', 1, type=int)

    query = Inquiry.query

    if tab == 'from_tenants':
        # Threads where tenant has replied (has at least one 'tenant' direction reply)
        # OR original message was sent by tenant/visitor
        replied_ids = db.session.query(InquiryReply.inquiry_id).filter_by(
            direction='tenant'
        ).distinct().subquery()
        query = query.filter(
            db.or_(
                Inquiry.sender.in_(['tenant', 'visitor']),
                Inquiry.id.in_(replied_ids),
            )
        )
    elif tab == 'sent':
        query = query.filter_by(sender='superadmin')
    elif tab == 'unread':
        # Messages with unread tenant replies
        query = query.filter(Inquiry.thread_unread_super > 0)

    if search:
        like = f'%{search}%'
        query = query.filter(
            db.or_(
                Inquiry.name.ilike(like),
                Inquiry.subject.ilike(like),
                Inquiry.tenant_slug.ilike(like),
                Inquiry.message.ilike(like),
            )
        )

    msgs = (
        query
        .order_by(
            db.case((Inquiry.thread_unread_super > 0, 0), else_=1),
            Inquiry.updated_at.desc().nulls_last(),
            Inquiry.created_at.desc(),
        )
        .paginate(page=page, per_page=25, error_out=False)
    )

    # Unread counts for tab badges
    unread_total = Inquiry.query.filter(Inquiry.thread_unread_super > 0).count()

    return render_template(
        'superadmin/messages_inbox.html',
        messages=msgs,
        tab=tab,
        search=search,
        unread_total=unread_total,
        page_title='Messages Inbox',
    )


@superadmin.route('/messages/<int:msg_id>', methods=['GET', 'POST'])
@superadmin_required
def message_thread(msg_id):
    """
    Full thread view for superadmin: original message + all replies.
    POST → add a reply from superadmin to tenant.
    """
    from app.models.portfolio import InquiryReply
    from app.forms import ReplyForm

    msg  = db.session.get(Inquiry, msg_id)
    if not msg:
        flash('Message not found.', 'warning')
        return redirect(url_for('superadmin.messages_inbox'))

    form = ReplyForm()

    if form.validate_on_submit():
        reply = InquiryReply(
            inquiry_id  = msg.id,
            tenant_slug = msg.tenant_slug,
            direction   = 'superadmin',
            sender_name = current_user.username,
            message     = form.message.data.strip(),
            is_read     = False,  # tenant hasn't read it yet
        )
        db.session.add(reply)

        # Bump thread_unread_tenant counter
        msg.thread_unread_tenant = (msg.thread_unread_tenant or 0) + 1
        msg.updated_at = _utcnow()

        # Mark original as read by superadmin if not yet
        if not msg.is_read:
            msg.is_read = True
        # Clear our own unread counter
        msg.thread_unread_super = 0

        db.session.commit()
        log_activity('reply', 'inquiry', msg.tenant_slug,
                     f'Superadmin replied to thread #{msg.id}')
        flash('Reply sent.', 'success')
        return redirect(url_for('superadmin.message_thread', msg_id=msg.id))

    # Mark thread as read by superadmin on view
    if msg.thread_unread_super > 0:
        msg.thread_unread_super = 0
        db.session.commit()
    if not msg.is_read:
        msg.is_read = True
        db.session.commit()

    replies = msg.replies.all()

    return render_template(
        'superadmin/message_thread.html',
        msg=msg,
        replies=replies,
        form=form,
        page_title=f'Thread — {msg.subject or "No subject"}',
    )


@superadmin.route('/messages/<int:msg_id>/delete', methods=['POST'])
@superadmin_required
def delete_message_thread(msg_id):
    msg = db.session.get(Inquiry, msg_id)
    if msg:
        db.session.delete(msg)
        db.session.commit()
        log_activity('delete', 'inquiry', str(msg_id), 'Superadmin deleted message thread')
        flash('Message thread deleted.', 'success')
    return redirect(url_for('superadmin.messages_inbox'))


@superadmin.route('/messages/tenant-stats')
@superadmin_required
def tenant_message_stats():
    """
    Superadmin-only view: per-tenant message/unread statistics (v5.2).

    Shows ONLY aggregate counts — no message content or visitor data.
    Tenant isolation is preserved: superadmin cannot read message bodies here.

    Returns JSON when ?format=json is requested (for dashboard widgets),
    otherwise renders the tenant_message_stats.html template.
    """
    from flask import jsonify
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func

    _now = datetime.now(timezone.utc)
    _week_start = _now - timedelta(days=7)

    # Build per-tenant stats with a single aggregated query
    rows = (
        db.session.query(
            Inquiry.tenant_slug,
            func.count(Inquiry.id).label('total'),
            func.sum(
                db.cast(Inquiry.is_read == False, db.Integer)  # noqa: E712
            ).label('unread'),
            func.sum(
                db.cast(Inquiry.created_at >= _week_start, db.Integer)
            ).label('last_7_days'),
        )
        .filter(
            Inquiry.tenant_slug.isnot(None),
            Inquiry.sender == 'visitor',       # visitor-submitted only; not superadmin messages
        )
        .group_by(Inquiry.tenant_slug)
        .order_by(func.sum(db.cast(Inquiry.is_read == False, db.Integer)).desc())  # noqa: E712
        .all()
    )

    stats = [
        {
            'tenant_slug': r.tenant_slug,
            'total':       int(r.total or 0),
            'unread':      int(r.unread or 0),
            'last_7_days': int(r.last_7_days or 0),
        }
        for r in rows
    ]

    if request.args.get('format') == 'json':
        return jsonify({'stats': stats, 'total_tenants': len(stats)})

    return render_template(
        'superadmin/tenant_message_stats.html',
        stats=stats,
        total_tenants=len(stats),
        total_unread=sum(s['unread'] for s in stats),
    )


@superadmin.route('/billing')
@superadmin_required
def billing_overview():
    """Subscription dashboard: MRR, active subs, webhook log, tenant billing table."""
    metrics = compute_billing_metrics()
    tenants = [tenant_billing_summary(p) for p in Profile.query.order_by(Profile.tenant_slug).all()]
    recent_webhooks = (
        WebhookEvent.query
        .order_by(WebhookEvent.received_at.desc())
        .limit(25)
        .all()
    )
    return render_template(
        'superadmin/billing_overview.html',
        metrics=metrics,
        tenants=tenants,
        recent_webhooks=recent_webhooks,
        billing_plans=BILLING_PLANS,
        page_title='Subscription Overview',
    )


@superadmin.route('/billing/sync/<int:profile_id>', methods=['POST'])
@superadmin_required
def billing_sync_tenant(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    ok, message = sync_subscription_from_paymongo(profile)
    flash(message, 'success' if ok else 'warning')
    return redirect(url_for('superadmin.billing_overview'))


@superadmin.route('/billing/activate/<int:profile_id>', methods=['POST'])
@superadmin_required
def billing_force_activate(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    plan = request.form.get('plan') or profile.plan or 'Basic'
    force_activate_subscription(profile, plan, actor=current_user.username)
    flash(f'Subscription force-activated for {profile.tenant_slug}.', 'success')
    return redirect(url_for('superadmin.billing_overview'))


@superadmin.route('/billing/payment-methods')
@superadmin_required
def billing_payment_methods():
    methods = PaymentMethod.query.order_by(
        PaymentMethod.tenant_id.asc().nullsfirst(),
        PaymentMethod.is_default.desc(),
        PaymentMethod.display_order.asc(),
        PaymentMethod.name.asc(),
    ).all()
    return render_template(
        'superadmin/billing_payment_methods.html',
        methods=methods,
        paymongo_enabled=is_paymongo_enabled(),
        paymongo_configured=bool(current_app.config.get('PAYMONGO_SECRET_KEY')),
        page_title='Payment Methods',
    )


@superadmin.route('/billing/paymongo/toggle', methods=['POST'])
@superadmin_required
def billing_paymongo_toggle():
    currently = is_paymongo_enabled()
    set_paymongo_enabled(not currently)
    from app.utils import log_billing_event
    state = 'enabled' if not currently else 'disabled'
    log_billing_event('paymongo_toggle', 'global', f'PayMongo checkout {state} by {current_user.username}')
    flash(f'PayMongo checkout {state}.', 'success')
    return redirect(url_for('superadmin.billing_payment_methods'))


@superadmin.route('/billing/payment-methods/toggle/<int:method_id>', methods=['POST'])
@superadmin_required
def billing_payment_method_toggle(method_id):
    method = db.session.get(PaymentMethod, method_id)
    if method is None:
        flash('Payment method not found.', 'danger')
        return redirect(url_for('superadmin.billing_payment_methods'))
    method.is_active = not method.is_active
    db.session.commit()
    from app.utils import log_billing_event
    log_billing_event(
        'payment_method_toggle',
        method.tenant.slug if method.tenant else 'global',
        f'{"Activated" if method.is_active else "Deactivated"} payment method: {method.name}',
    )
    flash(f'Payment method "{method.name}" is now {"active" if method.is_active else "inactive"}.', 'success')
    return redirect(url_for('superadmin.billing_payment_methods'))


@superadmin.route('/billing/payment-methods/default/<int:method_id>', methods=['POST'])
@superadmin_required
def billing_payment_method_set_default(method_id):
    method = db.session.get(PaymentMethod, method_id)
    if method is None:
        flash('Payment method not found.', 'danger')
        return redirect(url_for('superadmin.billing_payment_methods'))
    set_default_payment_method(method)
    flash(f'"{method.name}" is now the default payment method.', 'success')
    return redirect(url_for('superadmin.billing_payment_methods'))


def _populate_payment_method_form_choices(form: PaymentMethodForm) -> None:
    tenants = Tenant.query.order_by(Tenant.slug.asc()).all()
    form.tenant_slug.choices = [
        ('', 'Global (all tenants)'),
        *[(t.slug, f'{t.company_name or t.slug} ({t.slug})') for t in tenants],
    ]


@superadmin.route('/billing/payment-methods/new', methods=['GET', 'POST'])
@superadmin_required
def billing_payment_method_new():
    form = PaymentMethodForm()
    _populate_payment_method_form_choices(form)
    if form.validate_on_submit():
        tenant = None
        if form.tenant_slug.data:
            tenant = Tenant.query.filter_by(slug=form.tenant_slug.data).first()
        method = PaymentMethod(
            tenant=tenant,
            name=form.name.data.strip(),
            method_type=form.method_type.data,
            instructions=(form.instructions.data or '').strip(),
            account_name=(form.account_name.data or '').strip(),
            account_number=(form.account_number.data or '').strip(),
            mobile_number=(form.mobile_number.data or '').strip(),
            bank_name=(form.bank_name.data or '').strip(),
            notes=(form.notes.data or '').strip(),
            display_order=form.display_order.data or 0,
            is_active=form.is_active.data,
            is_default=form.is_default.data,
        )
        if form.qr_image.data:
            filename, err = save_billing_upload(form.qr_image.data, image_only=True)
            if err:
                flash(err, 'danger')
                return render_template(
                    'superadmin/billing_payment_method_form.html',
                    form=form,
                    page_title='New Payment Method',
                )
            method.qr_image = filename or ''
        db.session.add(method)
        db.session.flush()
        if method.is_default:
            set_default_payment_method(method)
        else:
            db.session.commit()
        from app.utils import log_billing_event
        log_billing_event(
            'payment_method_create',
            tenant.slug if tenant else 'global',
            f'Created payment method: {method.name}',
        )
        flash('Payment method created successfully.', 'success')
        return redirect(url_for('superadmin.billing_payment_methods'))
    return render_template(
        'superadmin/billing_payment_method_form.html',
        form=form,
        page_title='New Payment Method',
    )


@superadmin.route('/billing/payment-methods/<int:method_id>/edit', methods=['GET', 'POST'])
@superadmin_required
def billing_payment_method_edit(method_id):
    method = db.session.get(PaymentMethod, method_id)
    if method is None:
        from flask import abort
        abort(404)
    form = PaymentMethodForm(obj=method)
    _populate_payment_method_form_choices(form)
    if request.method == 'GET':
        form.tenant_slug.data = method.tenant.slug if method.tenant else ''
    if form.validate_on_submit():
        tenant = None
        if form.tenant_slug.data:
            tenant = Tenant.query.filter_by(slug=form.tenant_slug.data).first()
        method.tenant = tenant
        method.name = form.name.data.strip()
        method.method_type = form.method_type.data
        method.instructions = (form.instructions.data or '').strip()
        method.account_name = (form.account_name.data or '').strip()
        method.account_number = (form.account_number.data or '').strip()
        method.mobile_number = (form.mobile_number.data or '').strip()
        method.bank_name = (form.bank_name.data or '').strip()
        method.notes = (form.notes.data or '').strip()
        method.display_order = form.display_order.data or 0
        method.is_active = form.is_active.data
        if form.qr_image.data:
            filename, err = save_billing_upload(form.qr_image.data, image_only=True)
            if err:
                flash(err, 'danger')
                return render_template(
                    'superadmin/billing_payment_method_form.html',
                    form=form,
                    page_title='Edit Payment Method',
                    method=method,
                )
            method.qr_image = filename or method.qr_image
        if form.is_default.data:
            set_default_payment_method(method)
        else:
            method.is_default = False
            db.session.commit()
        from app.utils import log_billing_event
        log_billing_event(
            'payment_method_update',
            method.tenant.slug if method.tenant else 'global',
            f'Updated payment method: {method.name}',
        )
        flash('Payment method updated successfully.', 'success')
        return redirect(url_for('superadmin.billing_payment_methods'))
    return render_template(
        'superadmin/billing_payment_method_form.html',
        form=form,
        page_title='Edit Payment Method',
        method=method,
    )


@superadmin.route('/billing/instructions')
@superadmin_required
def billing_instructions():
    return redirect(url_for('superadmin.billing_payment_methods'))


@superadmin.route('/billing/instructions/new', methods=['GET', 'POST'])
@superadmin_required
def billing_instruction_new():
    form = PaymentInstructionForm()
    if form.validate_on_submit():
        instruction = PaymentInstruction(
            method=form.method.data,
            title=form.title.data.strip(),
            description=form.description.data.strip(),
            account_name=form.account_name.data.strip(),
            account_number=form.account_number.data.strip(),
            bank_name=form.bank_name.data.strip(),
            is_active=form.is_active.data,
        )
        if form.qr_image.data:
            # FIX D: use validated upload path — enforces jpg/jpeg/png/webp + magic bytes
            from app.services.manual_billing import save_billing_upload
            qr_filename, qr_err = save_billing_upload(form.qr_image.data, image_only=True)
            if qr_err:
                flash(f'QR image upload failed: {qr_err}', 'danger')
                return render_template(
                    'superadmin/billing_instruction_form.html',
                    form=form,
                    page_title='New Payment Instruction',
                )
            if qr_filename:
                instruction.qr_image = qr_filename
        db.session.add(instruction)
        db.session.commit()
        flash('Payment instruction created successfully.', 'success')
        return redirect(url_for('superadmin.billing_instructions'))
    return render_template(
        'superadmin/billing_instruction_form.html',
        form=form,
        page_title='New Payment Instruction',
    )


@superadmin.route('/billing/instructions/<int:instruction_id>/edit', methods=['GET', 'POST'])
@superadmin_required
def billing_instruction_edit(instruction_id):
    instruction = db.session.get(PaymentInstruction, instruction_id)
    if instruction is None:
        from flask import abort
        abort(404)
    form = PaymentInstructionForm(obj=instruction)
    if form.validate_on_submit():
        instruction.method = form.method.data
        instruction.title = form.title.data.strip()
        instruction.description = form.description.data.strip()
        instruction.account_name = form.account_name.data.strip()
        instruction.account_number = form.account_number.data.strip()
        instruction.bank_name = form.bank_name.data.strip()
        instruction.is_active = form.is_active.data
        if form.qr_image.data:
            # FIX D: use validated upload path — enforces jpg/jpeg/png/webp + magic bytes
            from app.services.manual_billing import save_billing_upload
            qr_filename, qr_err = save_billing_upload(form.qr_image.data, image_only=True)
            if qr_err:
                flash(f'QR image upload failed: {qr_err}', 'danger')
                return render_template(
                    'superadmin/billing_instruction_form.html',
                    form=form,
                    page_title='Edit Payment Instruction',
                    instruction=instruction,
                )
            if qr_filename:
                instruction.qr_image = qr_filename
        db.session.commit()
        flash('Payment instruction updated successfully.', 'success')
        return redirect(url_for('superadmin.billing_instructions'))
    return render_template(
        'superadmin/billing_instruction_form.html',
        form=form,
        page_title='Edit Payment Instruction',
        instruction=instruction,
    )


@superadmin.route('/billing/submissions')
@superadmin_required
def billing_submissions():
    submissions = (
        PaymentSubmission.query
        .order_by(PaymentSubmission.submitted_at.desc())
        .all()
    )
    return render_template(
        'superadmin/billing_submissions.html',
        submissions=submissions,
        page_title='Payment Submissions',
    )


@superadmin.route('/billing/submissions/<int:submission_id>/review', methods=['POST'])
@superadmin_required
def billing_submission_review(submission_id):
    submission = db.session.get(PaymentSubmission, submission_id)
    if submission is None:
        flash('Submission not found.', 'danger')
        return redirect(url_for('superadmin.billing_submissions'))

    action = request.form.get('action', '')
    review_notes = (request.form.get('review_notes') or '').strip()
    reviewer = current_user.username

    if action == 'approve':
        ok, message = approve_payment_submission(
            submission, reviewer=reviewer, review_notes=review_notes,
        )
        flash(message, 'success' if ok else 'danger')
    elif action == 'reject':
        ok, message = reject_payment_submission(
            submission, reviewer=reviewer, review_notes=review_notes,
        )
        flash(message, 'success' if ok else 'warning')
    else:
        flash('Invalid review action.', 'danger')

    return redirect(url_for('superadmin.billing_submissions'))


# ── Create tenant ─────────────────────────────────────────────────────────────

@superadmin.route('/tenants/new', methods=['GET', 'POST'])
@superadmin_required
def tenant_new():
    form = TenantForm()

    if form.validate_on_submit():
        slug = form.tenant_slug.data.strip().lower()

        # v3.7: use canonical validate_slug() from tenant_security (covers RESERVED_SLUGS + format)
        slug_ok, slug_err = validate_slug(slug)
        if not slug_ok:
            flash(slug_err, 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        if Profile.query.filter_by(tenant_slug=slug).first():
            flash(f'Slug "{slug}" is already taken. Choose a different one.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        username = form.admin_username.data.strip()
        email    = form.admin_email.data.strip().lower()
        password = request.form.get('admin_password', '').strip()
        password_confirm = request.form.get('admin_password_confirm', '').strip()

        if not password:
            flash('Initial password is required for new tenants.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        # FIX PWD: enforce full PasswordPolicy (12+ chars, upper/lower/number/special)
        from app.security import PasswordPolicy
        pwd_ok, pwd_err = PasswordPolicy.validate(password)
        if not pwd_ok:
            flash(f'Password does not meet policy: {pwd_err}', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        if password != password_confirm:
            flash('Passwords do not match.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        if User.query.filter(
            or_(User.username == username, User.email == email)
        ).first():
            flash('Username or email already exists.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        monthly_rate_val = 0.0
        try:
            monthly_rate_val = float(form.monthly_rate.data or 0)
        except (ValueError, TypeError):
            pass

        free_trial_days_val = 0
        try:
            free_trial_days_val = int(form.free_trial_days.data or 0)
        except (ValueError, TypeError):
            free_trial_days_val = 0

        free_trial_ends = (
            datetime.now(timezone.utc) + timedelta(days=free_trial_days_val)
            if free_trial_days_val > 0 else None
        )

        plan_choice = (form.plan.data or 'Trial').strip()
        normalized_plan = normalize_plan_name(plan_choice)
        tenant = Tenant(
            slug=slug,
            company_name=form.name.data.strip(),
            email=email,
            contact_email=form.contact_email.data.strip().lower() if form.contact_email.data else email,
            status='active' if request.form.get('is_active') == 'on' else 'inactive',
            plan=normalized_plan,
        )
        db.session.add(tenant)
        # ── CRITICAL: flush to core_db so PostgreSQL assigns tenant.id ──────────
        # Profile lives in the TENANT database (cross-DB, no SQLAlchemy FK).
        # SQLAlchemy cannot back-populate tenant_id automatically across binds.
        # Without flush(), tenant.id is None at Profile construction time, which
        # violates the NOT NULL constraint on profile.tenant_id.
        db.session.flush()

        # Guard: if id is still None the sequence/autoincrement is broken
        if tenant.id is None:
            raise RuntimeError(
                "Tenant id is None after flush — check core_db sequence/autoincrement."
            )

        logger.warning(
            "Tenant flushed to core_db: id=%s slug=%s",
            tenant.id,
            tenant.slug,
        )

        # Trial tenants rely on free_trial_ends — no subscription until they pay
        if plan_choice == 'Trial' and free_trial_days_val <= 0:
            free_trial_days_val = 14
            free_trial_ends = datetime.now(timezone.utc) + timedelta(days=free_trial_days_val)

        # ── Profile construction ─────────────────────────────────────────────────
        # Pass tenant_id and tenant_slug EXPLICITLY (post-flush, id is valid).
        # Also pass tenant=tenant so the in-memory cache is set; the setter will
        # overwrite tenant_id with value.id — which is now a real integer.
        profile = Profile(
            tenant=tenant,
            tenant_id=tenant.id,       # explicit — guards against setter ordering
            tenant_slug=tenant.slug,   # use tenant.slug (canonical source of truth)
            name=form.name.data.strip(),
            plan=normalized_plan,
            monthly_rate=monthly_rate_val,
            free_trial_days=free_trial_days_val,
            free_trial_ends=free_trial_ends,
            internal_notes=form.internal_notes.data or '',
            email=email,
        )

        # Belt-and-suspenders: assert tenant_id was not nulled out by the setter
        if profile.tenant_id is None:
            profile.tenant_id = tenant.id
            profile.tenant_slug = tenant.slug
            logger.warning(
                "tenant_id was None after Profile() constructor; re-applied: id=%s",
                tenant.id,
            )

        if plan_choice in PAID_PLAN_NAMES:
            subscription = Subscription(
                tenant=tenant,
                plan=normalized_plan,
                status='pending',
                payment_method='admin-provisioned',
            )
            db.session.add(subscription)
            if free_trial_days_val <= 0:
                now = datetime.now(timezone.utc)
                from app.services.billing import plan_duration_days
                subscription.status = 'active'
                subscription.started_at = now
                subscription.expires_at = now + timedelta(days=plan_duration_days(normalized_plan))
                subscription.payment_method = 'admin-provisioned'

        if hasattr(profile, 'is_available'):
            profile.is_available = (request.form.get('is_active') == 'on')

        db.session.add(profile)

        logger.warning(
            "Profile staged for tenant db: tenant_id=%s tenant_slug=%s profile.tenant_id=%s",
            tenant.id,
            tenant.slug,
            profile.tenant_id,
        )

        user = User(
            username=username,
            email=email,
            tenant_slug=slug,
            tenant=tenant,
            is_admin=True,
            is_superadmin=False,
        )
        user.password = password
        db.session.add(user)

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to create tenant: %s', exc)
            flash('Database error while creating tenant. Please try again.', 'danger')
            return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')

        log_activity('create', 'tenant', slug, f'Created tenant "{form.name.data}" ({slug})')
        flash(f'Tenant "{form.name.data}" created successfully!', 'success')
        return redirect(url_for('superadmin.tenants'))

    return render_template('superadmin/tenant_form.html', form=form, page_title='Create Tenant')


# ── Edit tenant ───────────────────────────────────────────────────────────────

@superadmin.route('/tenants/<int:tenant_id>/edit', methods=['GET', 'POST'])
@superadmin_required
def tenant_edit(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)
    form    = TenantForm(obj=profile)

    owner = User.query.filter_by(
        tenant_slug=profile.tenant_slug, is_admin=True
    ).first()

    if request.method == 'GET' and owner:
        form.admin_username.data = owner.username
        form.admin_email.data    = owner.email
        # Pre-populate contact_email from Tenant model
        if profile.tenant and profile.tenant.contact_email:
            form.contact_email.data = profile.tenant.contact_email

    if form.validate_on_submit():
        new_slug = form.tenant_slug.data.strip().lower()
        old_slug = profile.tenant_slug

        if new_slug != old_slug:
            # v3.7 VULN-02 FIX: enforce RESERVED_SLUGS on rename too
            slug_ok, slug_err = validate_slug(new_slug)
            if not slug_ok:
                flash(slug_err, 'danger')
                return render_template(
                    'superadmin/tenant_form.html', form=form,
                    page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                )
            if Profile.query.filter_by(tenant_slug=new_slug).first():
                flash(f'Slug "{new_slug}" is already taken.', 'danger')
                return render_template(
                    'superadmin/tenant_form.html', form=form,
                    page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                )

        monthly_rate_val = 0.0
        try:
            monthly_rate_val = float(form.monthly_rate.data or 0)
        except (ValueError, TypeError):
            pass

        profile.name           = form.name.data.strip()
        profile.tenant.slug    = new_slug
        profile.monthly_rate   = monthly_rate_val
        profile.internal_notes = form.internal_notes.data or ''

        free_trial_days_val = 0
        try:
            free_trial_days_val = int(form.free_trial_days.data or 0)
        except (ValueError, TypeError):
            free_trial_days_val = 0

        old_trial_days = profile.free_trial_days or 0
        profile.free_trial_days = free_trial_days_val
        if free_trial_days_val != old_trial_days or profile.free_trial_ends is None:
            profile.free_trial_ends = (
                datetime.now(timezone.utc) + timedelta(days=free_trial_days_val)
                if free_trial_days_val > 0 else None
            )

        plan_choice = (form.plan.data or 'Trial').strip()
        normalized_plan = normalize_plan_name(plan_choice)
        profile.plan = normalized_plan
        if profile.tenant:
            profile.tenant.plan = normalized_plan
            # Update contact_email if provided
            new_contact_email = request.form.get('contact_email', '').strip().lower()
            if new_contact_email:
                import re as _re
                if _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', new_contact_email):
                    profile.tenant.contact_email = new_contact_email
                else:
                    flash('Invalid contact email format.', 'warning')

        if plan_choice in PAID_PLAN_NAMES:
            subscription = profile.current_subscription()
            if not subscription:
                subscription = Subscription(
                    tenant=profile.tenant,
                    plan=normalized_plan,
                    status='pending',
                    payment_method='admin-provisioned',
                )
                db.session.add(subscription)
            else:
                subscription.plan = normalized_plan
        elif plan_choice == 'Trial' and profile.current_subscription():
            # Switching back to trial — remove pending admin-provisioned sub
            sub = profile.current_subscription()
            if sub and sub.status in ('pending', 'expired', 'cancelled'):
                db.session.delete(sub)

        is_active_raw = request.form.get('is_active')
        if hasattr(profile, 'is_available'):
            profile.is_available = (is_active_raw == 'on')

        new_username = form.admin_username.data.strip()
        new_email    = form.admin_email.data.strip().lower()

        if owner:
            if new_username != owner.username:
                if User.query.filter(User.username == new_username, User.id != owner.id).first():
                    flash('Username already taken.', 'danger')
                    return render_template(
                        'superadmin/tenant_form.html', form=form,
                        page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                    )
                owner.username = new_username

            if new_email != owner.email:
                if User.query.filter(User.email == new_email, User.id != owner.id).first():
                    flash('Email already in use.', 'danger')
                    return render_template(
                        'superadmin/tenant_form.html', form=form,
                        page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
                    )
                owner.email = new_email

            if new_slug != old_slug:
                owner.tenant_slug = new_slug

        # FIX: CASCADE slug rename to ALL tenant-scoped tables.
        # Without this, a slug rename orphans all projects/skills/etc.
        if new_slug != old_slug:
            profile.tenant.slug = new_slug
            from app.models.portfolio import Skill, Testimonial, ActivityLog, Inquiry
            for model in (Project, Skill, Testimonial, ActivityLog, Inquiry):
                try:
                    db.session.query(model).filter_by(tenant_slug=old_slug).update(
                        {'tenant_slug': new_slug}, synchronize_session='fetch'
                    )
                except Exception as exc:
                    logger.warning('Slug cascade update failed for %s: %s', model.__name__, exc)
            # Also update any other admin users for this tenant
            db.session.query(User).filter_by(tenant_slug=old_slug).update(
                {'tenant_slug': new_slug}, synchronize_session='fetch'
            )

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to update tenant: %s', exc)
            flash('Database error. Please try again.', 'danger')
            return render_template(
                'superadmin/tenant_form.html', form=form,
                page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
            )

        log_activity('update', 'tenant', new_slug, f'Updated tenant "{profile.name}"')
        flash(f'Tenant "{profile.name}" updated successfully!', 'success')
        return redirect(url_for('superadmin.tenants'))

    days_active = None
    updated_at = _normalize_timestamp(profile.updated_at)
    if updated_at:
        days_active = max(0, (datetime.now(timezone.utc) - updated_at).days)

    return render_template(
        'superadmin/tenant_form.html', form=form,
        page_title='Edit Tenant', tenant_id=tenant_id, profile=profile,
        days_active=days_active,
    )


# ── Delete tenant ─────────────────────────────────────────────────────────────

@superadmin.route('/tenants/<int:tenant_id>/delete', methods=['POST'])
@superadmin_required
def tenant_delete(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)

    if profile.tenant_slug == 'default':
        flash('The default tenant cannot be deleted.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    tenant = profile.tenant
    if tenant is None:
        flash('Tenant record not found.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    slug = profile.tenant_slug
    name = profile.name or slug

    try:
        delete_tenant_completely(tenant)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('superadmin.tenants'))
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to delete tenant %s: %s', slug, exc)
        flash('Error deleting tenant. Please try again.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    log_activity('delete', 'tenant', slug, f'Deleted tenant "{name}" ({slug})')
    flash(f'Tenant "{name}" has been deleted.', 'success')
    return redirect(url_for('superadmin.tenants'))


# ── Reset tenant admin password ─────────────────────────────────────────────────

@superadmin.route('/tenants/<int:tenant_id>/reset-password', methods=['POST'])
@superadmin_required
def tenant_reset_password(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)

    owner = User.query.filter_by(
        tenant_slug=profile.tenant_slug,
        is_admin=True,
    ).first()
    if owner is None:
        flash('No tenant admin user found for this tenant.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    # FIX PWD: include special chars so generated password can pass PasswordPolicy
    # if the tenant ever logs in and attempts to change it through a validated flow.
    # Also mark require_password_reset so the tenant must change it on first login.
    _temp_charset = string.ascii_letters + string.digits + '!@#$%^&*'
    new_password = ''.join(secrets.choice(_temp_charset) for _ in range(16))
    owner.password = new_password
    if hasattr(owner, 'require_password_reset'):
        owner.require_password_reset = True

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to reset tenant admin password: %s', exc)
        flash('Unable to reset tenant admin password. Please try again.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    log_activity(
        'security', 'user', owner.username,
        f'Reset password for tenant admin {owner.username} of {profile.tenant_slug}'
    )
    flash(
        f'Tenant admin password for "{owner.username}" has been reset. '
        f'New temporary password: {new_password}',
        'success'
    )
    return redirect(url_for('superadmin.tenants'))


# ── Toggle tenant suspension ───────────────────────────────────────────────────

@superadmin.route('/tenants/<int:tenant_id>/toggle-suspend', methods=['POST'])
@superadmin_required
def tenant_toggle_suspend(tenant_id):
    profile = db.session.get(Profile, tenant_id)
    if profile is None:
        from flask import abort
        abort(404)

    if not hasattr(profile, 'is_available'):
        flash('Tenant suspension is not supported by this platform version.', 'warning')
        return redirect(url_for('superadmin.tenants'))

    profile.is_available = not profile.is_available
    status = 'unsuspended' if profile.is_available else 'suspended'

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to toggle tenant suspension: %s', exc)
        flash('Unable to update tenant status. Please try again.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    log_activity(
        'update', 'tenant', profile.tenant_slug,
        f'{status.title()} tenant {profile.tenant_slug}'
    )
    flash(f'Tenant "{profile.name or profile.tenant_slug}" has been {status}.', 'success')
    return redirect(url_for('superadmin.tenants'))


# ── Media & Uploads ────────────────────────────────────────────────────────────

def _sa_format_filesize(size) -> str:
    """Human-readable filesize for the superadmin media manager."""
    if size is None:
        return 'n/a'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


@superadmin.route('/media')
@superadmin_required
def media():
    """Cross-tenant image & upload manager — list, filter, delete, compress."""
    import os
    from app.models.portfolio import Testimonial

    asset_type    = request.args.get('asset_type', 'all')
    tenant_filter = request.args.get('tenant', 'all')
    allowed_types = {'all', 'profile', 'project', 'testimonial', 'billing', 'proof'}
    if asset_type not in allowed_types:
        asset_type = 'all'

    all_profiles     = Profile.query.order_by(Profile.tenant_slug).all()
    all_projects     = (Project.query
                        .filter(Project.image != None, Project.image != '')
                        .order_by(Project.tenant_slug, Project.created_at.desc())
                        .all())
    all_testimonials = (Testimonial.query
                        .filter(Testimonial.author_avatar != None,
                                Testimonial.author_avatar != '')
                        .order_by(Testimonial.tenant_slug, Testimonial.created_at.desc())
                        .all())
    all_billing = (PaymentMethod.query
                   .filter(PaymentMethod.qr_image != None,
                           PaymentMethod.qr_image != '')
                   .order_by(PaymentMethod.id)
                   .all())

    assets = []

    for p in all_profiles:
        if p.profile_image:
            assets.append({
                'id': p.id, 'type': 'profile', 'label': 'Profile Image',
                'tenant': p.tenant_slug,
                'filename': p.profile_image, 'folder': 'profiles',
                'description': p.name or p.tenant_slug,
                'url': url_for('static', filename=f'uploads/profiles/{p.profile_image}'),
            })

    for proj in all_projects:
        assets.append({
            'id': proj.id, 'type': 'project', 'label': 'Project Image',
            'tenant': proj.tenant_slug,
            'filename': proj.image, 'folder': 'projects',
            'description': proj.title,
            'url': url_for('static', filename=f'uploads/projects/{proj.image}'),
        })

    for t in all_testimonials:
        assets.append({
            'id': t.id, 'type': 'testimonial', 'label': 'Testimonial Avatar',
            'tenant': t.tenant_slug,
            'filename': t.author_avatar, 'folder': 'profiles',
            'description': t.author_name,
            'url': url_for('static', filename=f'uploads/profiles/{t.author_avatar}'),
        })

    for pm in all_billing:
        assets.append({
            'id': pm.id, 'type': 'billing', 'label': 'Payment QR Code',
            'tenant': '(superadmin)',
            'filename': pm.qr_image, 'folder': 'billing',
            'description': pm.name,
            'url': url_for('static', filename=f'uploads/billing/{pm.qr_image}'),
        })

    # ── Payment submission proof images ───────────────────────────────
    all_proofs = (PaymentSubmission.query
                  .filter(PaymentSubmission.payment_proof != None,
                          PaymentSubmission.payment_proof != '')
                  .order_by(PaymentSubmission.submitted_at.desc())
                  .all())
    for sub in all_proofs:
        tenant_slug = sub.tenant.slug if sub.tenant else '(unknown)'
        assets.append({
            'id': sub.id, 'type': 'proof', 'label': 'Payment Proof',
            'tenant': tenant_slug,
            'filename': sub.payment_proof, 'folder': 'billing',
            'description': f'{sub.payment_method} — {sub.plan} — {sub.status}',
            'url': url_for('static', filename=f'uploads/billing/{sub.payment_proof}'),
        })

    for asset in assets:
        path = os.path.join(current_app.static_folder, 'uploads', asset['folder'], asset['filename'])
        try:
            asset['size_bytes'] = os.path.getsize(path)
        except OSError:
            asset['size_bytes'] = None
        asset['size_text'] = _sa_format_filesize(asset['size_bytes'])
        asset['exists']    = asset['size_bytes'] is not None

    all_tenant_slugs = sorted({a['tenant'] for a in assets})

    filtered = assets
    if asset_type != 'all':
        filtered = [a for a in filtered if a['type'] == asset_type]
    if tenant_filter != 'all':
        filtered = [a for a in filtered if a['tenant'] == tenant_filter]

    counts = {
        'all':         len(assets),
        'profile':     sum(1 for a in assets if a['type'] == 'profile'),
        'project':     sum(1 for a in assets if a['type'] == 'project'),
        'testimonial': sum(1 for a in assets if a['type'] == 'testimonial'),
        'billing':     sum(1 for a in assets if a['type'] == 'billing'),
        'proof':       sum(1 for a in assets if a['type'] == 'proof'),
    }
    total_bytes  = sum(a['size_bytes'] for a in assets if a['size_bytes'])
    orphan_count = sum(1 for a in assets if not a['exists'])

    return render_template(
        'superadmin/media.html',
        assets=filtered,
        asset_type=asset_type,
        tenant_filter=tenant_filter,
        all_tenant_slugs=all_tenant_slugs,
        counts=counts,
        total_assets=counts['all'],
        total_size=_sa_format_filesize(total_bytes),
        orphan_count=orphan_count,
    )


@superadmin.route('/media/delete', methods=['POST'])
@superadmin_required
def media_delete():
    """Delete a single uploaded file and clear the DB reference."""
    import os
    from app.models.portfolio import Testimonial

    asset_type   = request.form.get('asset_type')
    try:
        asset_id = int(request.form.get('asset_id') or 0) or None
    except (TypeError, ValueError):
        asset_id = None

    def _rm(folder: str, filename: str) -> None:
        if not filename:
            return
        path = os.path.join(current_app.static_folder, 'uploads', folder, filename)
        try:
            os.remove(path)
        except OSError:
            pass

    if asset_type == 'profile':
        p = db.session.get(Profile, asset_id)
        if p and p.profile_image:
            _rm('profiles', p.profile_image)
            p.profile_image = None
            db.session.commit()
            flash(f'Profile image deleted for tenant "{p.tenant_slug}".', 'success')
        else:
            flash('Profile image not found.', 'warning')
    elif asset_type == 'project':
        proj = db.session.get(Project, asset_id)
        if proj and proj.image:
            _rm('projects', proj.image)
            proj.image = None
            db.session.commit()
            flash(f'Project image deleted: "{proj.title}".', 'success')
        else:
            flash('Project image not found.', 'warning')
    elif asset_type == 'testimonial':
        t = db.session.get(Testimonial, asset_id)
        if t and t.author_avatar:
            _rm('profiles', t.author_avatar)
            t.author_avatar = None
            db.session.commit()
            flash(f'Testimonial avatar deleted: "{t.author_name}".', 'success')
        else:
            flash('Testimonial avatar not found.', 'warning')
    elif asset_type == 'billing':
        pm = db.session.get(PaymentMethod, asset_id)
        if pm and pm.qr_image:
            _rm('billing', pm.qr_image)
            pm.qr_image = ''
            db.session.commit()
            flash(f'QR code deleted for payment method "{pm.name}".', 'success')
        else:
            flash('Payment QR image not found.', 'warning')
    elif asset_type == 'proof':
        sub = db.session.get(PaymentSubmission, asset_id)
        if sub and sub.payment_proof:
            _rm('billing', sub.payment_proof)
            sub.payment_proof = ''
            db.session.commit()
            flash(f'Payment proof deleted for submission #{sub.id} ({sub.tenant.slug if sub.tenant else "?"}).', 'success')
        else:
            flash('Payment proof not found.', 'warning')
    else:
        flash('Unknown asset type.', 'danger')

    return redirect(url_for('superadmin.media',
                            asset_type=request.form.get('asset_type', 'all'),
                            tenant=request.form.get('tenant_filter', 'all')))


@superadmin.route('/media/compress', methods=['POST'])
@superadmin_required
def media_compress():
    """Re-compress a JPEG/PNG in-place to reduce file size."""
    import os
    from pathlib import Path
    from PIL import Image as PilImage

    folder   = request.form.get('folder', '')
    filename = request.form.get('filename', '')

    # ── Security: folder allowlist + filename traversal guard ─────────────────
    ALLOWED_COMPRESS_FOLDERS = {'profiles', 'projects', 'billing'}

    if (
        not filename
        or folder not in ALLOWED_COMPRESS_FOLDERS
        or '/' in filename
        or '..' in filename
    ):
        flash('Invalid compress request.', 'danger')
        return redirect(url_for('superadmin.media'))

    upload_root = Path(current_app.static_folder) / 'uploads'
    candidate   = (upload_root / folder / filename).resolve()

    # Containment check: resolved path must remain inside upload_root
    try:
        candidate.relative_to(upload_root.resolve())
    except ValueError:
        flash('Path traversal detected — request rejected.', 'danger')
        return redirect(url_for('superadmin.media'))

    path = str(candidate)
    if not os.path.exists(path):
        flash('File not found on disk.', 'warning')
        return redirect(url_for('superadmin.media'))

    try:
        before = os.path.getsize(path)
        img = PilImage.open(path).convert('RGB')
        max_side = 1200
        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), PilImage.LANCZOS)
        img.save(path, 'JPEG', quality=80, optimize=True)
        after = os.path.getsize(path)
        saved = before - after
        pct   = (saved / before * 100) if before else 0
        flash(
            f'Compressed "{filename}": {_sa_format_filesize(before)} → '
            f'{_sa_format_filesize(after)} (saved {_sa_format_filesize(saved)}, {pct:.0f}%).',
            'success',
        )
    except Exception as exc:
        flash(f'Compression failed: {exc}', 'danger')

    return redirect(url_for('superadmin.media',
                            asset_type=request.form.get('asset_type', 'all'),
                            tenant=request.form.get('tenant_filter', 'all')))


@superadmin.route('/media/delete-orphans', methods=['POST'])
@superadmin_required
def media_delete_orphans():
    """Delete files in upload folders that have no matching DB record."""
    import os
    from app.models.portfolio import Testimonial

    upload_root = os.path.join(current_app.static_folder, 'uploads')
    known = {'profiles': set(), 'projects': set(), 'billing': set()}

    for p in Profile.query.all():
        if p.profile_image:
            known['profiles'].add(p.profile_image)
    for t in Testimonial.query.all():
        if t.author_avatar:
            known['profiles'].add(t.author_avatar)
    for proj in Project.query.all():
        if proj.image:
            known['projects'].add(proj.image)
    for pm in PaymentMethod.query.all():
        if pm.qr_image:
            known['billing'].add(pm.qr_image)

    deleted = errors = 0
    for folder, known_files in known.items():
        folder_path = os.path.join(upload_root, folder)
        if not os.path.isdir(folder_path):
            continue
        for fname in os.listdir(folder_path):
            if fname not in known_files:
                try:
                    os.remove(os.path.join(folder_path, fname))
                    deleted += 1
                except OSError:
                    errors += 1

    if errors:
        flash(f'Deleted {deleted} orphan file(s). {errors} could not be removed.', 'warning')
    else:
        flash(f'Deleted {deleted} orphan file(s) with no database records.', 'success')

    return redirect(url_for('superadmin.media'))


# ── Settings ──────────────────────────────────────────────────────────────────

@superadmin.route('/settings', methods=['GET', 'POST'])
@superadmin_required
def settings():
    account_form  = SuperadminAccountForm(prefix='account')
    password_form = ChangePasswordForm(prefix='password')

    if request.method == 'GET':
        account_form.username.data = current_user.username
        account_form.email.data    = current_user.email

    # Check which form was actually submitted by looking for its prefixed field names
    account_form_submitted = request.method == 'POST' and 'account-username' in request.form
    password_form_submitted = request.method == 'POST' and 'password-current_password' in request.form

    if account_form_submitted and account_form.validate_on_submit():
        if current_user.verify_password(account_form.current_password.data):
            current_user.username = account_form.username.data.strip()
            current_user.email    = account_form.email.data.strip().lower()
            db.session.commit()
            log_activity('update', 'user', current_user.username, 'Superadmin account updated')
            flash('Account details updated successfully!', 'success')
            return redirect(url_for('superadmin.settings'))
        flash('Current password is incorrect.', 'danger')
    elif password_form_submitted and password_form.validate_on_submit():
        if current_user.verify_password(password_form.current_password.data):
            current_user.password = password_form.new_password.data
            db.session.commit()
            log_activity('update', 'user', current_user.username, 'Superadmin password changed')
            flash('Password changed successfully!', 'success')
            return redirect(url_for('superadmin.settings'))
        flash('Current password is incorrect.', 'danger')

    return render_template(
        'superadmin/settings.html',
        account_form=account_form,
        password_form=password_form,
        page_title='Superadmin Settings',
    )


# ── Superadmin Email & Web3Forms Settings (v3.8) ─────────────────────────────

@superadmin.route('/settings/email', methods=['GET', 'POST'])
@superadmin_required
def email_settings():
    """
    Superadmin → Settings → Email & Forms (v5.2)

    Manages GlobalEmailConfig:
      - MailerSend API key (primary transactional email provider)
      - Sender name / From email address
      - OTP expiry / recovery switch

    The MailerSend API key is stored Fernet-encrypted in DB and is NEVER
    returned to the template or any JSON response. Only the boolean
    `has_mailersend` flag is exposed to the UI.

    AJAX actions (POST with action=...):
      validate_mailersend_key  — validate key against MailerSend API
      send_test_email          — send a test email to superadmin address
    """
    from app.models.portfolio import GlobalEmailConfig
    from app.services.mailersend_service import validate_mailersend_key, send_email
    from flask import jsonify
    import re as _re

    cfg = GlobalEmailConfig.get()

    if request.method == 'POST':
        action = request.form.get('action', 'save')

        # ── AJAX: validate MailerSend key ──────────────────────────────────
        if action == 'validate_mailersend_key':
            key = request.form.get('mailersend_api_key', '').strip()
            if not key:
                return jsonify({'ok': False, 'message': 'No key supplied.'})
            # Validate against live MailerSend API (server-side only)
            valid, msg = validate_mailersend_key(key)
            # Never echo back the key in the response
            return jsonify({'ok': valid, 'message': msg})

        # ── AJAX: send test email ──────────────────────────────────────────
        if action == 'send_test_email':
            if not cfg.has_mailersend:
                return jsonify({'ok': False, 'message': 'MailerSend API key not configured. Save your key first.'})
            test_to = request.form.get('test_email', '').strip().lower()
            if not test_to or not _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', test_to):
                return jsonify({'ok': False, 'message': 'Invalid test recipient email address.'})
            ok, result = send_email(
                to=test_to,
                subject='[Portfolio CMS] MailerSend Test Email',
                text='This is a test email from Portfolio CMS to confirm MailerSend is correctly configured.',
                html='<p>This is a <strong>test email</strong> from Portfolio CMS confirming MailerSend is correctly configured.</p>',
            )
            if ok:
                current_app.logger.info('Test email sent to %s by superadmin %s', test_to, current_user.username)
                return jsonify({'ok': True, 'message': f'Test email delivered to {test_to}.'})
            return jsonify({'ok': False, 'message': f'Send failed: {result}'})

        # ── Save settings ─────────────────────────────────────────────────
        new_mailersend_key = request.form.get('mailersend_api_key', '').strip()
        sender_name        = request.form.get('sender_name', '').strip()
        sender_email       = request.form.get('sender_email', '').strip().lower()
        otp_ttl_raw        = request.form.get('otp_expiry_minutes', '10').strip()
        recovery_on        = request.form.get('recovery_enabled') == 'on'

        # Per-portal fields (v5.6)
        admin_key          = request.form.get('admin_mailersend_api_key', '').strip()
        admin_sender_email = request.form.get('admin_sender_email', '').strip().lower()
        admin_sender_name  = request.form.get('admin_sender_name', '').strip()
        sa_key             = request.form.get('superadmin_mailersend_api_key', '').strip()
        sa_sender_email    = request.form.get('superadmin_sender_email', '').strip().lower()
        sa_sender_name     = request.form.get('superadmin_sender_name', '').strip()

        try:
            otp_ttl = max(1, min(60, int(otp_ttl_raw)))
        except (ValueError, TypeError):
            otp_ttl = 10

        if sender_email and not _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', sender_email):
            flash('Invalid sender email format.', 'danger')
            return redirect(url_for('superadmin.email_settings'))

        # Store MailerSend key only if a new value was submitted
        if new_mailersend_key:
            cfg.mailersend_api_key = new_mailersend_key
            current_app.logger.info(
                'MailerSend API key updated by superadmin %s (key_length=%d)',
                current_user.username, len(new_mailersend_key),
            )

        # Per-portal keys (only update when a new value is submitted)
        if admin_key:
            cfg.admin_mailersend_api_key = admin_key
        if sa_key:
            cfg.superadmin_mailersend_api_key = sa_key

        cfg.sender_name        = sender_name  or cfg.sender_name
        cfg.sender_email       = sender_email or cfg.sender_email
        cfg.admin_sender_email = admin_sender_email or cfg.admin_sender_email
        cfg.admin_sender_name  = admin_sender_name  or cfg.admin_sender_name
        cfg.superadmin_sender_email = sa_sender_email or cfg.superadmin_sender_email
        cfg.superadmin_sender_name  = sa_sender_name  or cfg.superadmin_sender_name
        cfg.otp_expiry_minutes = otp_ttl
        cfg.recovery_enabled   = recovery_on
        cfg.updated_by         = current_user.username

        db.session.commit()
        flash('Email & Forms settings saved.', 'success')
        return redirect(url_for('superadmin.email_settings'))

    # GET — pass has_mailersend flag; never pass the raw key
    return render_template(
        'superadmin/email_settings.html',
        cfg=cfg,
        has_mailersend=cfg.has_mailersend,
        has_resend=False,  # backward-compat — Resend removed in v5.0
        default_form_provider='internal',
    )


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
    return render_template('superadmin/forgot_password_verify.html')
 
 
# ── CHANGE 2: Full replacement of forgot_password_reset with rate limiter ──
 
 
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


@superadmin.route('/subscriptions', methods=['GET', 'POST'])
@superadmin_required
def subscription_settings():
    """Superadmin subscription settings: manage subscription plans and tenant subscriptions."""
    from app.models.portfolio import Profile

    if request.method == 'POST':
        if request.form.get('action') == 'update_plans':
            # Update subscription plans
            for plan_key, plan_data in BILLING_PLANS.items():
                label = request.form.get(f'plan_label_{plan_key}', plan_data['label']).strip() or plan_data['label']
                price = request.form.get(f'plan_price_{plan_key}', plan_data['price'])
                duration = request.form.get(f'plan_duration_{plan_key}', plan_data.get('duration_days', 30))
                currency_symbol = request.form.get(f'plan_currency_{plan_key}', plan_data.get('currency_symbol', '₱')).strip() or '₱'
                description = request.form.get(f'plan_description_{plan_key}', plan_data.get('description', '')).strip() or ''
                features = request.form.get(f'plan_features_{plan_key}', plan_data.get('features', ''))
                payment_link = request.form.get(f'plan_payment_link_{plan_key}', plan_data.get('payment_link', '')).strip() or ''
                if payment_link:
                    try:
                        parsed = urlparse(payment_link)
                        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                            flash(f'Invalid payment link for {plan_key}. Must be a valid https URL.', 'danger')
                            return redirect(url_for('superadmin.subscription_settings'))
                    except Exception:
                        flash(f'Invalid payment link for {plan_key}. Changes not saved.', 'danger')
                        return redirect(url_for('superadmin.subscription_settings'))

                try:
                    price = float(price)
                except (TypeError, ValueError):
                    price = plan_data['price']
                try:
                    duration = int(duration)
                except (TypeError, ValueError):
                    duration = plan_data.get('duration_days', 30)

                plan_data['label'] = label
                plan_data['price'] = price            # legacy compat
                plan_data['price_monthly'] = price    # BUG#2: update monthly price
                plan_data['price_yearly'] = round(price * 12 * YEARLY_DISCOUNT, 2)  # BUG#2: recalc yearly
                plan_data['currency_symbol'] = currency_symbol
                plan_data['duration_days'] = duration
                plan_data['description'] = description
                # features comes from a textarea as a newline-delimited string;
                # normalise back to list so Jinja `{% for feature in details.features %}`
                # iterates items, not characters.
                if isinstance(features, str):
                    plan_data['features'] = [
                        f.strip() for f in features.splitlines() if f.strip()
                    ]
                else:
                    plan_data['features'] = features if isinstance(features, list) else []
                plan_data['payment_link'] = payment_link
                plan_data['price_label'] = f"{currency_symbol}{price:,.2f}/mo"

            log_activity('update', 'config', 'subscription_plans', 'Updated billing plans')
            flash('Subscription plans updated successfully!', 'success')
            return redirect(url_for('superadmin.subscription_settings'))

        elif request.form.get('action') == 'force_activate':
            profile = Profile.query.get(request.form.get('tenant_id'))
            if profile:
                plan = request.form.get('plan') or profile.plan or 'Basic'
                force_activate_subscription(profile, plan, actor=current_user.username)
                flash(f'Subscription activated for {profile.tenant_slug}.', 'success')
            else:
                flash('Tenant not found.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        elif request.form.get('action') == 'sync_paymongo':
            profile = Profile.query.get(request.form.get('tenant_id'))
            if profile:
                ok, message = sync_subscription_from_paymongo(profile)
                flash(message, 'success' if ok else 'warning')
            else:
                flash('Tenant not found.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        elif request.form.get('action') == 'reset_subscription':
            profile = Profile.query.get(request.form.get('tenant_id'))
            if profile:
                subscription = profile.current_subscription()
                if subscription:
                    subscription.status = 'pending'
                    subscription.started_at = None
                    subscription.expires_at = None
                    subscription.cancelled_at = None
                    db.session.commit()
                    log_activity('update', 'subscription', profile.tenant_slug, 'Reset tenant subscription')
                    flash(f'Subscription reset for {profile.tenant_slug}.', 'success')
                else:
                    flash(f'No subscription found for {profile.tenant_slug}.', 'warning')
            else:
                flash('Tenant not found.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

    # Gather subscription data for all tenants
    tenants_data = []
    for profile in Profile.query.order_by(Profile.tenant_slug).all():
        subscription = profile.current_subscription()
        plan_name = subscription.plan if subscription else profile.plan or 'Basic'
        plan_price_label = BILLING_PLANS.get(plan_name, {}).get('price_label', '')
        tenants_data.append({
            'id': profile.id,
            'tenant_slug': profile.tenant_slug,
            'name': profile.name,
            'plan': plan_name,
            'plan_price_label': plan_price_label,
            'status': subscription.status if subscription else 'no_subscription',
            'started_at': subscription.started_at if subscription else None,
            'expires_at': subscription.expires_at if subscription else None,
            'payment_method': subscription.payment_method if subscription else None,
            'amount_paid': subscription.amount_paid if subscription else 0.0,
        })

    return render_template(
        'superadmin/subscription_settings.html',
        tenants=tenants_data,
        billing_plans=BILLING_PLANS,
        page_title='Subscription Settings',
    )


@superadmin.route('/licenses', methods=['GET', 'POST'])
@superadmin_required
def licenses():
    """Deprecated — redirect to automated billing dashboard."""
    flash('License key management has been replaced by PayMongo automated billing.', 'info')
    return redirect(url_for('superadmin.billing_overview'))


# ── 2FA Routes ────────────────────────────────────────────────────────────────

@superadmin.route('/profile/2fa/setup', methods=['GET'])
@superadmin_required
def setup_2fa():
    import io, base64, qrcode
    from flask import current_app
    from app.forms import TOTPSetupForm

    form = TOTPSetupForm()
    if '_pending_totp_secret' not in session:
        secret = current_user.generate_totp_secret()
        session['_pending_totp_secret'] = secret

    secret = session['_pending_totp_secret']
    current_user.totp_secret = secret
    uri = current_user.get_totp_uri(
        issuer=current_app.config.get('TOTP_ISSUER', 'Portfolio CMS')
    )
    db.session.expire(current_user)

    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=8, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color='#6366f1', back_color='transparent')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    qr_b64 = base64.b64encode(buffer.getvalue()).decode()

    if '_pending_backup_codes' not in session:
        current_user.totp_secret = secret
        codes = current_user.generate_backup_codes()
        session['_pending_backup_codes'] = codes
        db.session.expire(current_user)

    backup_codes = session['_pending_backup_codes']
    return render_template('superadmin/2fa_setup.html', form=form, qr_b64=qr_b64,
                           secret=secret, backup_codes=backup_codes)


@superadmin.route('/profile/2fa/enable', methods=['POST'])
@superadmin_required
def enable_2fa():
    from app.forms import TOTPSetupForm
    form   = TOTPSetupForm()
    secret = session.get('_pending_totp_secret')
    backup = session.get('_pending_backup_codes', [])

    if not secret:
        flash('Setup session expired. Please start again.', 'warning')
        return redirect(url_for('superadmin.setup_2fa'))

    if form.validate_on_submit():
        import pyotp, json
        from werkzeug.security import generate_password_hash

        totp = pyotp.TOTP(secret)
        if totp.verify(form.code.data.strip(), valid_window=1):
            current_user.totp_secret       = secret
            current_user.totp_enabled      = True
            current_user.totp_backup_codes = json.dumps(
                [generate_password_hash(c) for c in backup]
            )
            db.session.commit()
            session.pop('_pending_totp_secret', None)
            session.pop('_pending_backup_codes', None)
            session['totp_verified'] = True
            log_activity('security', 'user', current_user.username, '2FA enabled via TOTP setup')
            flash('Two-factor authentication enabled successfully!', 'success')
            return redirect(url_for('superadmin.settings'))
        flash('Code incorrect — please try again.', 'danger')

    return redirect(url_for('superadmin.setup_2fa'))


@superadmin.route('/profile/2fa/disable', methods=['POST'])
@superadmin_required
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
    return redirect(url_for('superadmin.settings'))


@superadmin.route('/profile/2fa/regenerate-backup', methods=['POST'])
@superadmin_required
def regenerate_backup_codes():
    if not current_user.totp_enabled:
        flash('2FA is not enabled.', 'warning')
        return redirect(url_for('superadmin.settings'))
    codes = current_user.generate_backup_codes()
    db.session.commit()
    log_activity('security', 'user', current_user.username, 'Backup codes regenerated')
    session['_new_backup_codes'] = codes
    flash('Backup codes regenerated. Save them somewhere safe!', 'success')
    return redirect(url_for('superadmin.show_new_backup_codes'))


@superadmin.route('/profile/2fa/backup-codes')
@superadmin_required
def show_new_backup_codes():
    codes = session.pop('_new_backup_codes', None)
    if not codes:
        flash('No new backup codes to display.', 'warning')
        return redirect(url_for('superadmin.settings'))
    return render_template('superadmin/2fa_backup_codes.html', backup_codes=codes)


# ─────────────────────────────────────────────────────────────────────────────
# Superadmin Impersonation Feature
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

    query = ActivityLog.query.order_by(ActivityLog.created_at.desc())

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


# ─────────────────────────────────────────────────────────────────────────────
# IMPERSONATION — allows superadmin to log in as a tenant admin

@superadmin.route('/tenants/<int:tenant_id>/impersonate', methods=['POST'])
@superadmin_required
def impersonate_tenant(tenant_id):
    """
    Impersonate a tenant admin account.
    Stores the superadmin's identity in the session for restoration.
    """
    from flask_login import login_user
    from app.models import User
    from app.models.portfolio import Tenant
    from app.utils import log_activity

    tenant = Tenant.query.get_or_404(tenant_id)
    # Find the tenant's primary admin
    admin_user = User.query.filter_by(
        tenant_id=tenant.id,
        is_admin=True,
        is_superadmin=False,
    ).first()

    if not admin_user:
        flash(f'No admin user found for tenant {tenant.slug}.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    # Stash superadmin identity for restore
    session['_impersonating_as']        = admin_user.id
    session['_impersonation_superadmin'] = current_user.id
    session['_impersonation_started_at'] = datetime.now(timezone.utc).isoformat()

    # FIX IMP: stamp_session_tenant writes tenant_slug + HMAC _tsig into session.
    # Without this, TenantGuard sees a tenant_slug with no matching signature and
    # the impersonated session fails HMAC validation.
    stamp_session_tenant(admin_user.id, tenant.slug)

    login_user(admin_user)
    log_activity(
        'security', 'impersonation',
        f'superadmin→{admin_user.username}',
        f'Superadmin {session.get("_impersonation_superadmin")} started impersonation of {admin_user.username} (tenant: {tenant.slug})'
    )
    flash(
        f'Now acting as {admin_user.username} (tenant: {tenant.slug}). '
        f'<a href="{url_for("superadmin.stop_impersonation")}">Stop impersonation</a>',
        'warning'
    )
    return redirect(url_for('admin.dashboard'))


@superadmin.route('/stop-impersonation')
@login_required
def stop_impersonation():
    """Restore the original superadmin session."""
    from flask_login import login_user, logout_user
    from app.models import User
    from app.utils import log_activity

    superadmin_id = session.pop('_impersonation_superadmin', None)
    impersonated_id = session.pop('_impersonating_as', None)
    session.pop('_impersonation_started_at', None)

    if not superadmin_id:
        flash('No active impersonation session.', 'warning')
        return redirect(url_for('superadmin.dashboard'))

    superadmin_user = User.query.get(superadmin_id)
    if not superadmin_user or not superadmin_user.is_superadmin:
        logout_user()
        flash('Session expired. Please log in again.', 'warning')
        return redirect(url_for('superadmin.login'))

    logout_user()
    login_user(superadmin_user)
    session.pop('tenant_slug', None)

    log_activity(
        'security', 'impersonation',
        f'stopped→{superadmin_user.username}',
        f'Superadmin {superadmin_user.username} stopped impersonation'
    )
    flash('Impersonation ended. Welcome back.', 'success')
    return redirect(url_for('superadmin.dashboard'))


# ─────────────────────────────────────────────────────────────────────────────
# TENANT COMMUNICATION SETTINGS (v3.7)
# ─────────────────────────────────────────────────────────────────────────────

@superadmin.route('/tenants/<int:tenant_id>/communication', methods=['GET', 'POST'])
@superadmin_required
def tenant_communication(tenant_id):
    """View/edit per-tenant contact form (Basin/internal) settings. (v5.0)

    Note: SMTP fields in TenantCommunicationSettings are retained in the
    database for migration safety, but email dispatch now uses MailerSend
    exclusively. Flask-Mail removed in v5.0.
    """
    from app.models.portfolio import Tenant, Profile
    from app.models.tenant_form_settings import TenantFormSettings
    from app.services.basin_service import validate_basin_endpoint
    import re as _re

    tenant  = Tenant.query.get_or_404(tenant_id)
    profile = Profile.query.filter_by(tenant_id=tenant_id).first_or_404()
    comm    = TenantCommunicationSettings.get_or_create(tenant_id, profile.tenant_slug)
    form_settings = TenantFormSettings.get_or_create(tenant_id)

    if request.method == 'POST':
        # ── Contact form provider ─────────────────────────────────────────
        # v5.5 FIX: previously wrote only to legacy Tenant.form_provider /
        # Tenant.basin_endpoint, which app/tenant/__init__.py:contact() never
        # reads (it reads TenantFormSettings exclusively) — superadmin edits
        # here had no effect on actual delivery. Now writes TenantFormSettings
        # directly, same as the tenant-admin settings page fix.
        raw_provider     = request.form.get('form_provider', 'email').strip()
        basin_endpoint   = request.form.get('basin_endpoint', '').strip()
        recipient_email  = request.form.get('recipient_email', '').strip().lower()

        provider = 'basin' if raw_provider == 'basin' else 'email_only'

        if provider == 'basin' and basin_endpoint:
            valid, err = validate_basin_endpoint(basin_endpoint)
            if not valid:
                flash(f'Invalid Basin endpoint: {err}', 'danger')
                return redirect(url_for('superadmin.tenant_communication', tenant_id=tenant_id))
            form_settings.form_endpoint = basin_endpoint
            tenant.basin_endpoint = basin_endpoint
        elif provider == 'email_only':
            if recipient_email and not _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', recipient_email):
                flash('Invalid recipient email format.', 'danger')
                return redirect(url_for('superadmin.tenant_communication', tenant_id=tenant_id))
            if recipient_email:
                form_settings.receiver_email = recipient_email
                tenant.contact_email = recipient_email
            form_settings.form_endpoint = None

        form_settings.provider   = provider
        form_settings.is_enabled = True

        # Legacy columns — display-only elsewhere, not used for delivery.
        tenant.form_provider = provider if provider == 'basin' else 'internal'

        # ── SMTP ──────────────────────────────────────────────────────────
        comm.mail_username       = request.form.get('mail_username', '').strip()
        comm.mail_default_sender = request.form.get('mail_default_sender', '').strip()
        comm.admin_email         = request.form.get('admin_email', '').strip()
        comm.smtp_host           = request.form.get('smtp_host', '').strip()
        comm.smtp_tls            = request.form.get('smtp_tls') == '1'
        try:
            comm.smtp_port = int(request.form.get('smtp_port', 587))
        except (ValueError, TypeError):
            comm.smtp_port = 587

        pw = request.form.get('mail_password', '').strip()
        if pw and pw != '\u2022' * 8:
            comm.mail_password = pw

        # Reset to global defaults
        if request.form.get('reset_to_defaults'):
            tenant.form_provider  = 'internal'
            tenant.basin_endpoint = None
            form_settings.provider      = 'disabled'
            form_settings.is_enabled    = False
            form_settings.form_endpoint = None
            comm.mail_username       = ''
            comm.mail_password       = ''
            comm.mail_default_sender = ''
            comm.admin_email         = ''
            comm.smtp_host           = ''
            comm.smtp_port           = 587
            comm.smtp_tls            = True
            flash('Communication settings reset to global defaults.', 'success')
        else:
            flash('Communication settings saved.', 'success')

        db.session.commit()
        log_security_event(
            'comm_settings_updated', current_user,
            f'Superadmin updated comm settings for tenant {profile.tenant_slug!r}',
        )
        return redirect(url_for('superadmin.tenant_communication', tenant_id=tenant_id))

    return render_template(
        'superadmin/tenant_communication.html',
        profile=profile,
        tenant=tenant,
        comm=comm,
        form_settings=form_settings,
        has_smtp=comm.has_smtp,
        page_title=f'Communication — {profile.tenant_slug}',
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
        Subscription.query
        .filter(Subscription.status == 'active')
        .filter(Subscription.expires_at.between(now, horizon_7))
        .order_by(Subscription.expires_at.asc())
    )
    expiring_30_q = (
        Subscription.query
        .filter(Subscription.status == 'active')
        .filter(Subscription.expires_at.between(now, horizon_30))
        .order_by(Subscription.expires_at.asc())
    )
    expired_q = (
        Subscription.query
        .filter(Subscription.status == 'expired')
        .order_by(Subscription.expires_at.desc())
        .limit(50)
    )

    # Eager-load for display
    expiring_7  = _add_days_left(expiring_7_q.all())
    expiring_30 = _add_days_left(expiring_30_q.all())
    expired     = expired_q.all()

    # Metrics
    total_active   = Subscription.query.filter_by(status='active').count()
    total_expiring = expiring_7_q.count()
    total_expired  = Subscription.query.filter_by(status='expired').count()
    total_pending  = Subscription.query.filter_by(status='pending').count()
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
        SubscriptionNotification.query
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