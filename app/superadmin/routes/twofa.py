"""
app/superadmin/routes/twofa.py — Superadmin TOTP 2FA setup / enable / disable / backup codes (Phase 4b, batch 7)

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


@superadmin.route('/profile/2fa/setup', methods=['GET'])
@superadmin_required
def setup_2fa():
    """Start Superadmin TOTP setup using the same safe flow as tenant admin.

    The old implementation temporarily wrote the pending TOTP secret onto the
    current user during GET and generated the QR code inline. On production this
    could 500 if qrcode was unavailable and it also risked stale session/DB
    state. The shared helper keeps pending secrets in session until a valid code
    is submitted.
    """
    from app.forms import TOTPSetupForm
    from app.auth.totp import generate_setup_context

    form = TOTPSetupForm()
    ctx = generate_setup_context(user=current_user)
    return render_template('superadmin/2fa_setup.html', form=form, **ctx)

@superadmin.route('/profile/2fa/enable', methods=['POST'])
@superadmin_required
def enable_2fa():
    """Confirm and persist Superadmin TOTP setup.

    Mirrors the tenant/admin implementation: validate CSRF, rate-limit attempts,
    verify against the pending session secret, then commit atomically.
    """
    from app.forms import TOTPSetupForm
    from app.auth.totp import (
        commit_2fa_enable, rate_limit_totp_verify,
        record_totp_failure, clear_totp_attempts, TotpRateLimitError,
    )

    form = TOTPSetupForm()
    from app.request_security import get_client_ip
    ip = get_client_ip()

    try:
        rate_limit_totp_verify(ip)
    except TotpRateLimitError as exc:
        flash(str(exc), 'danger')
        session.pop('_pending_totp_secret', None)
        session.pop('_pending_backup_codes', None)
        return redirect(url_for('superadmin.settings'))

    if not form.validate_on_submit():
        flash('Invalid form submission. Please try again.', 'danger')
        return redirect(url_for('superadmin.setup_2fa'))

    success, error = commit_2fa_enable(current_user, form.code.data or '')
    if success:
        clear_totp_attempts(ip)
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to persist superadmin 2FA settings: %s', exc)
            flash('Database error saving 2FA settings. Please try again.', 'danger')
            return redirect(url_for('superadmin.setup_2fa'))
        session['totp_verified'] = True
        log_activity('security', 'user', current_user.username, 'Superadmin 2FA enabled via TOTP setup')
        flash('Two-factor authentication enabled successfully!', 'success')
        return redirect(url_for('superadmin.settings'))

    record_totp_failure(ip)
    flash(error or 'Code incorrect — please try again.', 'danger')
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
