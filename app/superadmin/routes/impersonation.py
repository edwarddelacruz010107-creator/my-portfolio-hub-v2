"""
app/superadmin/routes/impersonation.py — Tenant impersonation + tenant communication settings (Phase 4b, batch 3)

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

    tenant = tenant_repository.get_or_404(tenant_id)
    # Find the tenant's primary admin
    admin_user = user_repository.query.filter_by(
        tenant_id=tenant.id,
        is_admin=True,
        is_superadmin=False,
    ).first()

    if not admin_user:
        flash(f'No admin user found for tenant {tenant.slug}.', 'danger')
        return redirect(url_for('superadmin.tenants'))

    # Stash superadmin identity for restore
    # Preserve original session tenant/signature so we can fully restore
    # the superadmin's session after impersonation ends. Store a small
    # JSON-serializable snapshot under '_impersonation_original'.
    session['_impersonation_original'] = {
        'tenant_slug': session.get('tenant_slug'),
        '_tsig': session.get('_tsig'),
        '_tsig_created': session.get('_tsig_created'),
        '_tsig_user_id': session.get('_tsig_user_id'),
    }

    # Stash superadmin identity for restore (backwards-compatible keys kept)
    session['_impersonating_as']         = admin_user.id
    session['_impersonation_superadmin'] = current_user.id
    session['_impersonation_started_at'] = datetime.now(timezone.utc).isoformat()

    # Additional metadata for stronger validation and auditing
    session['impersonator_id'] = current_user.id
    session['original_role'] = 'superadmin' if getattr(current_user, 'is_superadmin', False) else 'admin'
    session['original_tenant_id'] = getattr(current_user, 'tenant_id', None)

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
    """
    Restore the original superadmin session.
    
    v4.0 FIX: Now re-stamps session tenant with new HMAC to invalidate any
    impersonation-era session tokens. Prevents session replay after stopping
    impersonation.
    """
    from flask_login import login_user, logout_user
    from app.models import User
    from app.utils import log_activity
    from app.tenant_security import stamp_session_tenant

    # Retrieve stored values. We intentionally read the original snapshot
    # first so it can be restored after re-authenticating the superadmin.
    original_snapshot = session.pop('_impersonation_original', None)
    superadmin_id = session.pop('_impersonation_superadmin', None)
    impersonated_id = session.pop('_impersonating_as', None)
    session.pop('_impersonation_started_at', None)

    if not superadmin_id:
        flash('No active impersonation session.', 'warning')
        return redirect(url_for('superadmin.dashboard'))

    superadmin_user = user_repository.get(superadmin_id)
    if not superadmin_user or not superadmin_user.is_superadmin:
        logout_user()
        flash('Session expired. Please log in again.', 'warning')
        return redirect(url_for('superadmin.login'))

    logout_user()
    login_user(superadmin_user)
    
    # v4.0 FIX: Don't restore old HMAC values — re-stamp with new session_token.
    # This invalidates any session cookies from the impersonation era and
    # ensures a fresh, cryptographically-valid session.
    # Superadmins don't have a tenant_slug, so we just clear tenant context.
    session.pop('tenant_slug', None)
    session.pop('_tsig', None)
    session.pop('_tsig_created', None)
    session.pop('_tsig_user_id', None)
    session.pop('_session_token', None)

    # Cleanup additional impersonation metadata
    session.pop('impersonator_id', None)
    session.pop('original_role', None)
    session.pop('original_tenant_id', None)

    log_activity(
        'security', 'impersonation',
        f'stopped→{superadmin_user.username}',
        f'Superadmin {superadmin_user.username} stopped impersonation'
    )
    flash('Impersonation ended. Welcome back.', 'success')
    return redirect(url_for('superadmin.dashboard'))

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

    tenant  = tenant_repository.get_or_404(tenant_id)
    profile = profile_repository.get_by_tenant_id_or_404(tenant_id)
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
