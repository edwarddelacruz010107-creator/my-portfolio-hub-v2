"""
app/admin/routes/messaging.py — Admin <-> superadmin messaging (Phase 4b, batch 3)

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
                        get_profile_completion, is_upload_file)
from app.tenant_security import (
    resolve_active_tenant, stamp_session_tenant,
    RESERVED_SLUGS, session_tenant_valid,
)
from app import limiter  # Flask-Limiter instance
from app.forms import ForgotPasswordForm  # Flask-WTF form for CSRF protection

logger = logging.getLogger(__name__)
admin  = Blueprint('admin', __name__)


from app.admin.blueprint import (admin,
                                 admin_required,
                                 _active_tenant_slug,
                                 _require_tenant_object,        
                                 _tenant_slug_filter,
                                 )

logger = logging.getLogger(__name__)


def _get_client_ip() -> str:
    from app.request_security import get_client_ip
    return get_client_ip()


def _ensure_inquiry_phone_column() -> None:
    """
    Ensure the legacy 'phone' column exists on the inquiries table for
    older local SQLite DBs that predate this column. This is a lightweight
    compatibility step to avoid OperationalError when admin pages query
    the full Inquiry model.
    """
    try:
        inspector = db.inspect(db.engine)
        cols = [c['name'] for c in inspector.get_columns('inquiries')]
        if 'phone' not in cols:
            # Add nullable text column — safe default for SQLite and others.
            with db.engine.begin() as conn:
                conn.execute("ALTER TABLE inquiries ADD COLUMN phone VARCHAR(50)")
                conn.execute("ALTER TABLE inquiries ADD COLUMN company VARCHAR(200)")
    except Exception:
        # Any failure here should be non-fatal; the later query will either
        # succeed or raise a clearer error. Log for diagnostics.
        logger.exception('Failed to ensure inquiries phone/company columns')

@admin.route('/messages')
@admin_required
def messages():
    """
    Tenant admin inbox (v3.8):
    - Visitor contact-form submissions
    - Messages sent by superadmin
    - All threaded with reply support
    Tabs: all | from_superadmin | from_visitors | unread
    """
    from app.models.portfolio import InquiryReply
    tab    = request.args.get('tab', 'all')
    search = request.args.get('q', '').strip()
    page   = request.args.get('page', 1, type=int)

    # Ensure expected inquiry columns exist on older dev DBs
    _ensure_inquiry_phone_column()

    query = _tenant_slug_filter(inquiry_repository.query)

    if tab == 'from_superadmin':
        query = query.filter_by(sender='superadmin')
    elif tab == 'from_visitors':
        query = query.filter(Inquiry.sender.in_(['visitor', 'tenant']))
    elif tab == 'unread':
        query = query.filter(
            db.or_(Inquiry.is_read == False, Inquiry.thread_unread_tenant > 0)
        )

    if search:
        like = f'%{search}%'
        query = query.filter(
            db.or_(
                Inquiry.name.ilike(like),
                Inquiry.subject.ilike(like),
                Inquiry.message.ilike(like),
            )
        )

    msgs = (
        query
        .order_by(
            db.case((Inquiry.thread_unread_tenant > 0, 0), else_=1),
            Inquiry.updated_at.desc().nulls_last(),
            Inquiry.created_at.desc(),
        )
        .paginate(page=page, per_page=20, error_out=False)
    )

    unread_total = _tenant_slug_filter(inquiry_repository.query).filter(
        db.or_(Inquiry.is_read == False, Inquiry.thread_unread_tenant > 0)
    ).count()

    return render_template('admin/messages.html',
                           messages=msgs, tab=tab, search=search,
                           unread_total=unread_total)

@admin.route('/messages/<int:message_id>', methods=['GET', 'POST'])
@admin_required
def view_message(message_id: int):
    """
    Full thread view for tenant admin.
    GET  → render thread with reply form.
    POST → post a reply to superadmin (direction='tenant').
    """
    from app.models.portfolio import InquiryReply
    from app.forms import ReplyForm

    message = _require_tenant_object(db.session.get(Inquiry, message_id))
    if message is None:
        flash('Message not found.', 'warning')
        return redirect(url_for('admin.messages'))

    form = ReplyForm()

    if form.validate_on_submit():
        # Only superadmin-originated threads can be replied to
        # Visitor submissions can also be replied to (reply goes to superadmin as record)
        reply = InquiryReply(
            inquiry_id  = message.id,
            tenant_slug = message.tenant_slug,
            direction   = 'tenant',
            sender_name = current_user.username,
            message     = form.message.data.strip(),
            is_read     = False,
        )
        db.session.add(reply)

        # Bump unread counter for superadmin
        message.thread_unread_super = (message.thread_unread_super or 0) + 1
        # Clear own unread
        message.thread_unread_tenant = 0
        message.is_read = True
        message.updated_at = datetime.now(timezone.utc)

        db.session.commit()
        try:
            from app.services.notification_service import Recipient, publish_notification
            publish_notification(
                recipient=Recipient.role('superadmin'),
                event_type='message.reply_to_platform',
                template_key='message.reply_to_platform',
                parameters={'tenant_name': message.tenant_slug, 'subject': message.subject},
                dedupe_key=f'message.reply_to_platform:{reply.id}',
                entity_type='inquiry_reply',
                entity_id=reply.id,
                actor_type='user',
                actor_id=current_user.id,
                action_route='superadmin.message_thread',
                action_parameters={'msg_id': message.id},
                commit=True,
            )
        except Exception:
            logger.exception('Tenant reply notification failed: reply_id=%s', reply.id)
        log_activity('reply', 'inquiry', message.name,
                     f'Tenant admin replied to thread #{message.id}')
        flash('Reply sent.', 'success')
        return redirect(url_for('admin.view_message', message_id=message.id))

    # Mark as read on open
    if not message.is_read or message.thread_unread_tenant:
        message.is_read = True
        message.thread_unread_tenant = 0
        db.session.commit()

    replies = message.replies.all()

    return render_template('admin/message_detail.html',
                           message=message, replies=replies, form=form)

@admin.route('/messages/new', methods=['GET', 'POST'])
@admin_required
def new_message_to_superadmin():
    """
    Tenant admin → Superadmin: compose a new message thread.
    Creates an Inquiry with sender='tenant' visible in the superadmin inbox.
    """
    from app.forms import ReplyForm

    tenant_slug = _active_tenant_slug()
    form = ReplyForm()
    # Reuse ReplyForm — just need subject + message
    subject = ''

    if request.method == 'POST':
        subject = request.form.get('subject', '').strip()
        msg_text = request.form.get('message', '').strip()

        if not subject:
            flash('Subject is required.', 'danger')
        elif not msg_text or len(msg_text) < 5:
            flash('Message must be at least 5 characters.', 'danger')
        else:
            inquiry = Inquiry(
                tenant_slug = tenant_slug,
                name        = current_user.username,
                email       = current_user.email or f'{tenant_slug}@tenant',
                subject     = subject,
                message     = msg_text,
                sender      = 'tenant',
                is_read     = False,
                thread_unread_super = 1,
            )
            db.session.add(inquiry)
            db.session.commit()
            try:
                from app.services.notification_service import Recipient, publish_notification
                publish_notification(
                    recipient=Recipient.role('superadmin'),
                    event_type='message.tenant_to_platform',
                    template_key='message.tenant_to_platform',
                    parameters={'tenant_name': tenant_slug, 'subject': subject},
                    dedupe_key=f'message.tenant_to_platform:{inquiry.id}',
                    entity_type='inquiry',
                    entity_id=inquiry.id,
                    actor_type='user',
                    actor_id=current_user.id,
                    action_route='superadmin.message_thread',
                    action_parameters={'msg_id': inquiry.id},
                    priority='high',
                    commit=True,
                )
            except Exception:
                logger.exception('New tenant message notification failed: inquiry_id=%s', inquiry.id)
            log_activity('create', 'inquiry', tenant_slug,
                         f'Tenant admin sent message to superadmin')
            flash('Message sent to platform support.', 'success')
            return redirect(url_for('admin.view_message', message_id=inquiry.id))

    return render_template('admin/new_message.html',
                           subject=subject, form=form)

@admin.route('/messages/<int:message_id>/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_message(message_id: int):
    message = _require_tenant_object(db.session.get(Inquiry, message_id))
    if message is None:
        flash('Message not found.', 'warning')
        return redirect(url_for('admin.messages'))
    db.session.delete(message)
    db.session.commit()
    log_activity('delete', 'inquiry', message.name)
    flash('Message deleted.', 'success')
    return redirect(url_for('admin.messages'))
