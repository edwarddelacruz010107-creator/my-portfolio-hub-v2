"""
app/superadmin/routes/messaging.py — Superadmin <-> tenant messaging (Phase 4b, batch 4)

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


_READY_MADE_MESSAGE_TEMPLATES = {
    'general': {
        'message_type': 'general',
        'subject': 'Platform update from MyPortfolioHub',
        'message': 'Hello,\n\nWe wanted to share a quick platform update with you. Please review the details below and let us know if you need any help.\n\nWhat changed:\n- [Add the main update here]\n- [Add any action needed here]\n\nThank you,\nMyPortfolioHub Team',
    },
    'alert': {
        'message_type': 'alert',
        'subject': 'Important alert for your portfolio account',
        'message': 'Hello,\n\nWe are sending this alert because an important item may require your attention.\n\nSummary:\n- Issue: [Describe the alert]\n- Impact: [Explain who or what is affected]\n- Action needed: [Explain what the tenant admin should do]\n\nPlease review this as soon as possible.\n\nThank you,\nMyPortfolioHub Team',
    },
    'billing': {
        'message_type': 'billing',
        'subject': 'Billing update for your portfolio subscription',
        'message': 'Hello,\n\nThis is a billing update regarding your MyPortfolioHub account.\n\nDetails:\n- Plan or invoice: [Add plan/invoice detail]\n- Amount or status: [Add amount/status if applicable]\n- Next step: [Explain payment, renewal, or confirmation action]\n\nPlease check your billing page or contact support if you have questions.\n\nThank you,\nMyPortfolioHub Team',
    },
    'maintenance': {
        'message_type': 'maintenance',
        'subject': 'Scheduled maintenance notice',
        'message': 'Hello,\n\nWe will be performing scheduled maintenance on MyPortfolioHub.\n\nSchedule:\n- Date/time: [Add maintenance schedule]\n- Expected duration: [Add estimated duration]\n- Expected impact: [Add downtime or feature impact]\n\nWe recommend saving any work before the maintenance window begins.\n\nThank you for your patience,\nMyPortfolioHub Team',
    },
    'account': {
        'message_type': 'account',
        'subject': 'Reminder: review your portfolio account settings',
        'message': 'Hello,\n\nThis is a friendly reminder to review your MyPortfolioHub account settings.\n\nRecommended actions:\n- Confirm your portfolio details are updated\n- Review your contact and email settings\n- Check your theme, projects, and billing information\n\nKeeping your account updated helps your portfolio stay accurate and professional.\n\nThank you,\nMyPortfolioHub Team',
    },
}

@superadmin.route('/messages/send', methods=['GET', 'POST'])
@superadmin_required
def send_message():
    form = SuperadminMessageForm()
    tenants = profile_repository.query.order_by(Profile.name.asc()).all()
    form.tenant_slug.choices = [
        ('all', 'All Tenants'),
        *[(t.tenant_slug, f"{t.name or t.tenant_slug} ({t.tenant_slug})") for t in tenants]
    ]

    if request.method == 'GET':
        requested_tenant = request.args.get('tenant_slug')
        if requested_tenant:
            form.tenant_slug.data = requested_tenant

        requested_template = (request.args.get('template') or '').strip().lower()
        template = _READY_MADE_MESSAGE_TEMPLATES.get(requested_template)
        if template:
            form.message_type.data = template['message_type']
            form.subject.data = template['subject']
            form.message.data = template['message']

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
            tenant = profile_repository.get_by_tenant_slug(selected)
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

@superadmin.route('/messages')
@superadmin_required
def messages_inbox():
    """
    Superadmin inbox: shows all Inquiry threads across all tenants.
    
    INCLUDES:
      • Landing page contact form submissions (tenant_slug='default', sender='visitor')
      • Tenant admin inquiries (sender='tenant')
      • Superadmin sent messages (sender='superadmin')
    
    Tabs: All | From Tenants | Sent by Me | Unread
      • 'all' (default): Shows all inquiries
      • 'from_tenants': Shows inquiries from visitors and tenants (includes contact form submissions)
      • 'sent': Shows superadmin-sent messages only
      • 'unread': Shows threads with unread tenant replies
    """
    from app.models.portfolio import InquiryReply
    tab    = request.args.get('tab', 'all')
    search = request.args.get('q', '').strip()
    page   = request.args.get('page', 1, type=int)

    query = inquiry_repository.query

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
    unread_total = inquiry_repository.query.filter(Inquiry.thread_unread_super > 0).count()

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
@limiter.limit('30 per minute')
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
