"""
app/admin/routes/notifications_email.py — Notifications + contact-form provider + email services config (Phase 4b, batch 10)

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


from app.admin.blueprint import admin, admin_required, _active_tenant_slug

logger = logging.getLogger(__name__)


@admin.route('/notifications')
@login_required
def notifications():
    """Full notifications page for the tenant admin."""
    from app.services.notification_service import get_notifications, mark_all_read, get_unread_count
    tenant_id = current_user.tenant_id
    notifs = get_notifications(tenant_id, limit=50)
    unread = get_unread_count(tenant_id)
    return render_template(
        'admin/notifications.html',
        notifications=notifs,
        unread_count=unread,
    )

@admin.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def notifications_mark_all_read():
    from app.services.notification_service import mark_all_read
    mark_all_read(current_user.tenant_id)
    return redirect(url_for('admin.notifications'))

@admin.route('/notifications/<int:notif_id>/read', methods=['POST'])
@login_required
def notification_mark_read(notif_id):
    from app.services.notification_service import mark_notification_read
    mark_notification_read(notif_id, current_user.tenant_id)
    # HIGH-07: validate referrer to prevent open redirect
    from app.auth import _is_safe_url
    referrer = request.referrer
    safe_target = referrer if (referrer and _is_safe_url(referrer)) else url_for('admin.notifications')
    return redirect(safe_target)

@admin.route('/settings/contact-form', methods=['POST'])
@login_required
def update_contact_form_provider():
    """
    Update tenant contact form provider (email_only | basin). v5.5 FIX

    BUG (found in audit): this handler previously wrote only to the legacy
    Tenant.form_provider / Tenant.basin_endpoint columns. The actual contact
    form delivery engine (app/tenant/__init__.py: contact()) reads exclusively
    from TenantFormSettings — a completely separate, properly tenant-isolated
    table. Because nothing ever wrote to TenantFormSettings from this page,
    every tenant's row stayed at its disabled default, so submissions always
    fell back to "internal inbox only" regardless of what was selected here.

    Additionally, recipient_email was never read from the POST body at all —
    there was no column on Tenant to store it, so it was silently discarded.

    This handler now writes to TenantFormSettings (the table delivery actually
    reads) and also mirrors the recipient email onto Tenant.contact_email,
    since contact() falls back to that field if TenantFormSettings.receiver_email
    is empty. Legacy Tenant.form_provider/basin_endpoint are kept in sync only
    for backward-compatible display elsewhere — they are not used for delivery.
    """
    from app.models.portfolio import Tenant
    from app.models.tenant_form_settings import TenantFormSettings
    from app.services.basin_service import validate_basin_endpoint
    import re as _re

    tenant_slug = _active_tenant_slug()
    tenant = tenant_repository.get_by_slug(tenant_slug)
    if not tenant:
        flash('Tenant not found.', 'danger')
        return redirect(url_for('admin.settings'))

    # Template radios send 'email' | 'basin'; TenantFormSettings stores
    # 'email_only' | 'basin' (see VALID_PROVIDERS in tenant_form_settings.py).
    raw_provider     = request.form.get('form_provider', 'email').strip()
    basin_endpoint   = request.form.get('basin_endpoint', '').strip()
    recipient_email  = request.form.get('recipient_email', '').strip().lower()

    provider = 'basin' if raw_provider == 'basin' else 'email_only'
    settings = TenantFormSettings.get_or_create(tenant.id)

    if provider == 'basin':
        if not basin_endpoint:
            flash('A Basin endpoint URL is required when selecting Basin.', 'danger')
            return redirect(url_for('admin.settings'))
        valid, err = validate_basin_endpoint(basin_endpoint)
        if not valid:
            flash(f'Invalid Basin endpoint: {err}', 'danger')
            return redirect(url_for('admin.settings'))
        settings.form_endpoint = basin_endpoint
    else:
        if not recipient_email or not _re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', recipient_email):
            flash('A valid recipient email is required for Email Only.', 'danger')
            return redirect(url_for('admin.settings'))
        settings.receiver_email = recipient_email
        settings.form_endpoint  = None
        tenant.contact_email    = recipient_email  # kept in sync for the contact() fallback path

    settings.provider   = provider
    settings.is_enabled = True

    # Legacy columns — retained for any remaining display-only template reads,
    # NOT used by the actual delivery engine.
    tenant.form_provider  = 'basin' if provider == 'basin' else 'internal'
    if provider == 'basin':
        tenant.basin_endpoint = basin_endpoint

    db.session.commit()
    flash('Contact form provider saved.', 'success')
    return redirect(url_for('admin.settings'))

@admin.route('/api/notifications/unread-count')
@login_required
def api_notifications_unread_count():
    """JSON endpoint for bell badge polling."""
    from flask import jsonify
    from app.services.notification_service import get_unread_count, get_notifications
    tenant_id = current_user.tenant_id
    unread = get_unread_count(tenant_id)
    recent = get_notifications(tenant_id, limit=5)
    return jsonify({
        'unread_count': unread,
        'notifications': [
            {
                'id': n.id,
                'type': n.notification_type,
                'title': n.title,
                'message': n.message,
                'is_read': n.is_read,
                'created_at': n.created_at.isoformat() if n.created_at else None,
            }
            for n in recent
        ],
    })

@admin.route('/email-services')
@admin_required
def email_services():
    """
    Email Services dashboard — shows all provider statuses and config forms.
    Reads provider, SMTP, Resend, and MailerSend settings for the current tenant.
    Credentials are NEVER passed to the template (only masked indicators).
    """
    from app.models.core import (
        TenantEmailProvider,
        TenantSmtpSettings,
        TenantResendSettings,
        TenantMailerSendSettings,
    )
    from app.services.tenant_email_service import get_provider_status, bootstrap_tenant_providers

    tenant_id = current_user.tenant_id

    # Ensure provider records exist
    bootstrap_tenant_providers(tenant_id)

    # Load settings (for has_* indicators only — no credentials in template)
    smtp_settings = TenantSmtpSettings.get_or_create(tenant_id)
    resend_settings = TenantResendSettings.get_or_create(tenant_id)
    ms_settings = TenantMailerSendSettings.get_or_create(tenant_id)

    # Load ordered provider list
    providers = (TenantEmailProvider.query
                 .filter_by(tenant_id=tenant_id)
                 .order_by(TenantEmailProvider.priority.asc())
                 .all())

    # Build safe display dict (no secrets)
    provider_status = get_provider_status(tenant_id)

    smtp_display = {
        'host':            smtp_settings.smtp_host,
        'port':            smtp_settings.smtp_port,
        'username':        smtp_settings.smtp_username,
        'sender_email':    smtp_settings.sender_email,
        'sender_name':     smtp_settings.sender_name,
        'encryption_type': smtp_settings.encryption_type or 'tls',
        'has_password':    bool(smtp_settings._smtp_password),
        'is_configured':   smtp_settings.is_configured,
    }
    resend_display = {
        'domain':        resend_settings.domain,
        'sender_email':  resend_settings.sender_email,
        'sender_name':   resend_settings.sender_name,
        'has_api_key':   bool(resend_settings._api_key),
        'is_configured': resend_settings.is_configured,
    }
    ms_display = {
        'domain':        ms_settings.domain,
        'sender_email':  ms_settings.sender_email,
        'sender_name':   ms_settings.sender_name,
        'has_api_token': bool(ms_settings._api_token),
        'is_configured': ms_settings.is_configured,
    }

    # Pass form_settings so the template can warn when contact delivery is disabled
    from app.models.tenant_form_settings import TenantFormSettings
    form_settings = TenantFormSettings.get_or_create(tenant_id)
    # Determine if any provider is configured+active so we can show a warning
    any_active_configured = any(
        (p.active and p.status == 'connected') for p in providers
    )
    contact_delivery_enabled = (
        form_settings.is_enabled and
        form_settings.provider not in ('disabled', '', None)
    )

    return render_template(
        'admin/email_services.html',
        providers=providers,
        provider_status=provider_status,
        smtp_display=smtp_display,
        resend_display=resend_display,
        ms_display=ms_display,
        form_settings=form_settings,
        any_active_configured=any_active_configured,
        contact_delivery_enabled=contact_delivery_enabled,
    )

@admin.route('/email-services/save/<provider_name>', methods=['POST'])
@admin_required
def email_services_save(provider_name: str):
    """
    Save provider credentials for the current tenant.
    Credentials are encrypted server-side via Fernet before DB storage.
    CSRF protected by Flask-WTF token in all form submissions.
    """
    from app.models.core import (
        TenantEmailProvider,
        TenantSmtpSettings,
        TenantResendSettings,
        TenantMailerSendSettings,
    )
    from app import db

    VALID_PROVIDERS = ('smtp', 'resend', 'mailersend')
    if provider_name not in VALID_PROVIDERS:
        flash('Invalid provider.', 'danger')
        return redirect(url_for('admin.email_services'))

    tenant_id = current_user.tenant_id

    try:
        if provider_name == 'smtp':
            s = TenantSmtpSettings.get_or_create(tenant_id)
            s.smtp_host       = request.form.get('smtp_host', '').strip()[:300]
            s.smtp_port       = int(request.form.get('smtp_port', 587) or 587)
            s.smtp_username   = request.form.get('smtp_username', '').strip()[:300]
            s.sender_email    = request.form.get('sender_email', '').strip()[:300]
            s.sender_name     = request.form.get('sender_name', '').strip()[:200]
            s.encryption_type = request.form.get('encryption_type', 'tls').strip()[:20]

            # Only update password if a new one was submitted (non-empty)
            new_password = request.form.get('smtp_password', '').strip()
            if new_password:
                s.smtp_password = new_password   # triggers Fernet encryption

            db.session.add(s)

        elif provider_name == 'resend':
            s = TenantResendSettings.get_or_create(tenant_id)
            s.domain       = request.form.get('domain', '').strip()[:300]
            s.sender_email = request.form.get('sender_email', '').strip()[:300]
            s.sender_name  = request.form.get('sender_name', '').strip()[:200]

            new_key = request.form.get('api_key', '').strip()
            if new_key:
                s.api_key = new_key   # triggers Fernet encryption

            db.session.add(s)

        elif provider_name == 'mailersend':
            s = TenantMailerSendSettings.get_or_create(tenant_id)
            s.domain       = request.form.get('domain', '').strip()[:300]
            s.sender_email = request.form.get('sender_email', '').strip()[:300]
            s.sender_name  = request.form.get('sender_name', '').strip()[:200]

            new_token = request.form.get('api_token', '').strip()
            if new_token:
                s.api_token = new_token   # triggers Fernet encryption

            db.session.add(s)

        db.session.commit()

        # ── Update provider record status immediately after save ──────────────
        # Don't wait for a test-email click — if credentials were just saved,
        # mark the provider as configured and active right away so the badge
        # shows "Configured" without requiring an extra step.
        try:
            provider_rec = TenantEmailProvider.get_or_create(tenant_id, provider_name)
            if s.is_configured:
                provider_rec.status = 'connected'
                provider_rec.active = True
                db.session.commit()
        except Exception as _pe:
            logger.warning('[EmailServices] provider_rec status update failed (non-fatal): %s', _pe)

        # ── Auto-enable contact form delivery when provider is first configured ──
        # If TenantFormSettings is still 'disabled', activating a provider here
        # means the contact form would still silently fall to inbox-only because
        # the two systems are independent. Auto-bridge them: set provider=email_only
        # and populate receiver_email so contact submissions are actually delivered.
        try:
            from app.models.tenant_form_settings import TenantFormSettings
            from app.models.core import TenantEmailProvider
            form_settings = TenantFormSettings.get_or_create(tenant_id)
            if form_settings.provider in ('disabled', '') or not form_settings.is_enabled:
                # Only auto-enable if the saved provider is now configured
                provider_rec = TenantEmailProvider.get_or_create(tenant_id, provider_name)
                if s.is_configured:
                    # Resolve a receiver_email from tenant admin account
                    if not form_settings.receiver_email:
                        from app.models.core import User
                        admin_user = (user_repository.query
                                      .filter_by(tenant_id=tenant_id, is_admin=True)
                                      .order_by(User.id.asc())
                                      .first())
                        if admin_user and admin_user.email:
                            form_settings.receiver_email = admin_user.email
                            # Mirror onto tenant.contact_email for the fallback chain
                            from app.models.portfolio import Tenant
                            _tenant = tenant_repository.get(tenant_id)
                            if _tenant and not _tenant.contact_email:
                                _tenant.contact_email = admin_user.email
                    form_settings.provider   = 'email_only'
                    form_settings.is_enabled = True
                    db.session.commit()
                    logger.info(
                        '[EmailServices] Auto-enabled contact form delivery via email_only '
                        'for tenant_id=%d on %s save', tenant_id, provider_name
                    )
        except Exception as _fe:
            logger.warning('[EmailServices] form_settings auto-enable failed (non-fatal): %s', _fe)

        log_activity('update', 'email_provider', provider_name, f'Email provider {provider_name} configuration saved')
        flash(f'{provider_name.title()} settings saved successfully.', 'success')

    except Exception as e:
        db.session.rollback()
        logger.error('[EmailServices] Save failed provider=%s tenant=%d: %s', provider_name, tenant_id, str(e))
        flash(f'Error saving {provider_name} settings. Please try again.', 'danger')

    return redirect(url_for('admin.email_services'))

@admin.route('/email-services/toggle/<provider_name>', methods=['POST'])
@admin_required
def email_services_toggle(provider_name: str):
    """
    Activate or deactivate a provider for the current tenant.
    JSON endpoint — returns {success, active, status}.
    """
    from flask import jsonify
    from app.models.core import TenantEmailProvider
    from app import db

    VALID_PROVIDERS = ('smtp', 'resend', 'mailersend')
    if provider_name not in VALID_PROVIDERS:
        return jsonify({'success': False, 'error': 'Invalid provider'}), 400

    tenant_id = current_user.tenant_id
    action    = request.form.get('action', 'toggle')   # activate | deactivate | toggle

    try:
        rec = TenantEmailProvider.get_or_create(tenant_id, provider_name)

        if action == 'activate':
            rec.active = True
        elif action == 'deactivate':
            rec.active = False
        else:
            rec.active = not rec.active

        db.session.commit()

        # ── Auto-enable contact form delivery when provider activated ──────────
        # Activating a provider here doesn't automatically make contact form
        # submissions reach the tenant's inbox unless TenantFormSettings.provider
        # is also set to 'email_only'. Sync them now.
        form_settings_updated = False
        if rec.active:
            try:
                from app.models.tenant_form_settings import TenantFormSettings
                form_settings = TenantFormSettings.get_or_create(tenant_id)
                if form_settings.provider in ('disabled', '') or not form_settings.is_enabled:
                    if not form_settings.receiver_email:
                        from app.models.core import User
                        admin_user = (user_repository.query
                                      .filter_by(tenant_id=tenant_id, is_admin=True)
                                      .order_by(User.id.asc())
                                      .first())
                        if admin_user and admin_user.email:
                            form_settings.receiver_email = admin_user.email
                            from app.models.portfolio import Tenant
                            _tenant = tenant_repository.get(tenant_id)
                            if _tenant and not _tenant.contact_email:
                                _tenant.contact_email = admin_user.email
                    form_settings.provider   = 'email_only'
                    form_settings.is_enabled = True
                    db.session.commit()
                    form_settings_updated = True
                    logger.info(
                        '[EmailServices] Toggle auto-enabled contact form delivery '
                        'for tenant_id=%d via %s', tenant_id, provider_name
                    )
            except Exception as _fe:
                logger.warning('[EmailServices] Toggle form_settings sync failed (non-fatal): %s', _fe)

        log_activity(
            'update', 'email_provider', provider_name,
            f'Provider {provider_name} {"activated" if rec.active else "deactivated"}'
        )
        return jsonify({
            'success':               True,
            'active':                rec.active,
            'provider':              provider_name,
            'status':                rec.status,
            'form_delivery_enabled': form_settings_updated,
        })

    except Exception as e:
        db.session.rollback()
        logger.error('[EmailServices] Toggle failed provider=%s: %s', provider_name, str(e))
        return jsonify({'success': False, 'error': 'Toggle failed'}), 500

@admin.route('/email-services/priority', methods=['POST'])
@admin_required
def email_services_priority():
    """
    Update provider priority ordering.
    Expects JSON body: {providers: [{name: 'smtp', priority: 1}, ...]}
    """
    from flask import jsonify
    from app.models.core import TenantEmailProvider
    from app import db

    tenant_id = current_user.tenant_id

    try:
        data      = request.get_json(silent=True) or {}
        providers = data.get('providers', [])

        VALID = ('smtp', 'resend', 'mailersend')
        for item in providers:
            name     = item.get('name', '')
            priority = item.get('priority')
            if name not in VALID or not isinstance(priority, int):
                continue
            rec = TenantEmailProvider.get_or_create(tenant_id, name)
            rec.priority = max(1, min(99, priority))

        db.session.commit()
        log_activity('update', 'email_provider', 'priority', 'Email provider priority updated')
        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        logger.error('[EmailServices] Priority update failed: %s', str(e))
        return jsonify({'success': False, 'error': 'Priority update failed'}), 500

@admin.route('/email-services/test', methods=['POST'])
@admin_required
def email_services_test():
    """
    Send a test email through a specified provider.
    JSON endpoint — returns {success, message, latency}.
    """
    from flask import jsonify
    from app.services.tenant_email_service import test_provider

    tenant_id     = current_user.tenant_id
    provider_name = request.form.get('provider', '').strip()
    to_email      = request.form.get('to_email', '').strip() or current_user.email

    VALID_PROVIDERS = ('smtp', 'resend', 'mailersend')
    if provider_name not in VALID_PROVIDERS:
        return jsonify({'success': False, 'message': 'Invalid provider'}), 400

    if not to_email or '@' not in to_email:
        to_email = current_user.email

    ok, msg, latency = test_provider(tenant_id, provider_name, to_email)
    log_activity(
        'update', 'email_provider', provider_name,
        f'Test email {"succeeded" if ok else "failed"} via {provider_name}'
    )
    return jsonify({
        'success': ok,
        'message': msg,
        'latency': round(latency, 2),
        'provider': provider_name,
        'to_email': to_email,
    })

@admin.route('/email-services/status')
@admin_required
def email_services_status():
    """JSON health check for all providers — used by dashboard polling."""
    from flask import jsonify
    from app.services.tenant_email_service import get_provider_status

    tenant_id = current_user.tenant_id
    status    = get_provider_status(tenant_id)

    # Serialize datetimes to ISO strings
    for provider_data in status.values():
        for key in ('last_tested_at', 'last_sent_at'):
            val = provider_data.get(key)
            provider_data[key] = val.isoformat() if val else None

    return jsonify({'success': True, 'providers': status})
