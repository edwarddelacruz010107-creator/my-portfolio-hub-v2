"""
app/superadmin/routes/billing.py — Billing overview, payment methods, instructions, submissions (Phase 4b, batch 9)

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
from app.utils import (is_paymongo_enabled, set_paymongo_enabled,
                       is_dodo_payments_admin_enabled, set_dodo_payments_admin_enabled)
from app.models import User
from app.models.portfolio import (Profile, PaymentMethod, PaymentSubmission, Subscription, WebhookEvent,
                                   ActivityLog, Project, Inquiry, Tenant, PaymentInstruction, PAID_PLAN_NAMES,
                                   normalize_plan_name)


from app.utils import log_activity, BILLING_PLANS, YEARLY_DISCOUNT, get_public_billing_plans
from app.security import log_security_event
from app.tenant_security import RESERVED_SLUGS, validate_slug, stamp_session_tenant
from app.models.portfolio import TenantCommunicationSettings
from app.models.portfolio import _utcnow
from app.system_plan import ensure_default_tenant_administrator_plan, has_administrator_access, is_administrator_plan
from app.services.billing import (
    compute_billing_metrics,
    tenant_billing_summary,
    force_activate_subscription,
    sync_subscription_from_paymongo,
)


from app.superadmin.blueprint import superadmin, superadmin_required

logger = logging.getLogger(__name__)


@superadmin.route('/billing')
@superadmin_required
def billing_overview():
    """Subscription dashboard: MRR, active subs, webhook log, tenant billing table."""
    metrics = compute_billing_metrics()
    tenants = [tenant_billing_summary(p) for p in profile_repository.query.order_by(Profile.tenant_slug).all()]
    recent_webhooks = (
        webhook_event_repository.query
        .order_by(WebhookEvent.received_at.desc())
        .limit(25)
        .all()
    )
    return render_template(
        'superadmin/billing_overview.html',
        metrics=metrics,
        tenants=tenants,
        recent_webhooks=recent_webhooks,
        billing_plans=get_public_billing_plans(),
        page_title='Subscription Overview',
    )

@superadmin.route('/billing/sync/<int:profile_id>', methods=['POST'])
@superadmin_required
def billing_sync_tenant(profile_id):
    profile = profile_repository.get_or_404(profile_id)
    ok, message = sync_subscription_from_paymongo(profile)
    flash(message, 'success' if ok else 'warning')
    return redirect(url_for('superadmin.billing_overview'))

@superadmin.route('/billing/activate/<int:profile_id>', methods=['POST'])
@superadmin_required
def billing_force_activate(profile_id):
    profile = profile_repository.get_or_404(profile_id)
    plan = request.form.get('plan') or profile.plan or 'Basic'
    if has_administrator_access(profile):
        ensure_default_tenant_administrator_plan(commit=True)
        flash('The protected system portfolio already has Administrator full access and cannot be reassigned.', 'info')
    elif is_administrator_plan(plan):
        flash('Administrator is an internal-only system plan and cannot be assigned to normal tenants.', 'warning')
    else:
        ok, message = force_activate_subscription(profile, plan, actor=current_user.username)
        flash(message or f'Subscription force-activated for {profile.tenant_slug}.', 'success' if ok else 'warning')
    return redirect(url_for('superadmin.billing_overview'))

@superadmin.route('/billing/payment-methods')
@superadmin_required
def billing_payment_methods():
    methods = payment_method_repository.query.order_by(
        PaymentMethod.tenant_id.asc().nullsfirst(),
        PaymentMethod.is_default.desc(),
        PaymentMethod.display_order.asc(),
        PaymentMethod.name.asc(),
    ).all()
    dodo_product_keys = (
        'DODO_BASIC_MONTHLY_PRODUCT_ID',
        'DODO_BASIC_YEARLY_PRODUCT_ID',
        'DODO_PRO_MONTHLY_PRODUCT_ID',
        'DODO_PRO_YEARLY_PRODUCT_ID',
        'DODO_ENTERPRISE_MONTHLY_PRODUCT_ID',
        'DODO_ENTERPRISE_YEARLY_PRODUCT_ID',
    )
    dodo_products_configured = sum(
        1 for key in dodo_product_keys if current_app.config.get(key)
    )
    dodo_api_configured = bool(current_app.config.get('DODO_PAYMENTS_API_KEY'))
    dodo_webhook_configured = bool(current_app.config.get('DODO_PAYMENTS_WEBHOOK_SECRET'))
    dodo_env_enabled = bool(current_app.config.get('DODO_PAYMENTS_ENABLED'))
    dodo_admin_enabled = is_dodo_payments_admin_enabled(default=True)
    dodo_ready = bool(dodo_env_enabled and dodo_admin_enabled and dodo_api_configured)

    return render_template(
        'superadmin/billing_payment_methods.html',
        methods=methods,
        paymongo_enabled=is_paymongo_enabled(),
        paymongo_configured=bool(current_app.config.get('PAYMONGO_SECRET_KEY')),
        dodo_enabled=dodo_ready,
        dodo_admin_enabled=dodo_admin_enabled,
        dodo_env_enabled=dodo_env_enabled,
        dodo_api_configured=dodo_api_configured,
        dodo_webhook_configured=dodo_webhook_configured,
        dodo_mode=current_app.config.get('DODO_PAYMENTS_MODE', 'test'),
        dodo_products_configured=dodo_products_configured,
        dodo_products_total=len(dodo_product_keys),
        page_title='Payment Methods',
    )

@superadmin.route('/billing/dodo/toggle', methods=['POST'])
@superadmin_required
def billing_dodo_toggle():
    """Enable or disable tenant Dodo checkout without changing Render secrets."""
    env_enabled = bool(current_app.config.get('DODO_PAYMENTS_ENABLED'))
    api_configured = bool(current_app.config.get('DODO_PAYMENTS_API_KEY'))
    currently = is_dodo_payments_admin_enabled(default=True)

    if not currently and not (env_enabled and api_configured):
        flash('Dodo Payments cannot be activated until its Render configuration is complete.', 'warning')
        return redirect(url_for('superadmin.billing_payment_methods'))

    try:
        set_dodo_payments_admin_enabled(not currently)
    except Exception:
        flash('Dodo Payments status could not be changed. Please check the server logs.', 'danger')
        return redirect(url_for('superadmin.billing_payment_methods'))

    from app.utils import log_billing_event
    state = 'enabled' if not currently else 'disabled'
    log_billing_event('dodo_toggle', 'global', f'Dodo Payments checkout {state} by {current_user.username}')
    flash(f'Dodo Payments checkout {state}.', 'success')
    return redirect(url_for('superadmin.billing_payment_methods'))


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
    tenants = tenant_repository.query.order_by(Tenant.slug.asc()).all()
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
            tenant = tenant_repository.get_by_slug(form.tenant_slug.data)
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
            tenant = tenant_repository.get_by_slug(form.tenant_slug.data)
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
        payment_submission_repository.query
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
    """
    Process approve/reject for a manual payment submission.

    v6.3.1 hardening:
      • Explicit VALID_REVIEW_ACTIONS whitelist guard.
      • Detailed structured logging at entry point.
      • Service exceptions caught here as last-resort safety net (service also
        wraps internally, but double-wrapping prevents 500 from reaching user).
      • Action is read from form field 'action'; the template submits via
        <button name="action" value="approve|reject"> — the clicked button's
        value is the one that arrives in the POST body.
    """
    VALID_REVIEW_ACTIONS = {'approve', 'reject'}

    submission = db.session.get(PaymentSubmission, submission_id)
    if submission is None:
        logger.warning('[PAYMENT_REVIEW] submission_id=%s not found', submission_id)
        flash('Payment submission not found.', 'danger')
        return redirect(url_for('superadmin.billing_submissions'))

    # Read action — log raw value to catch empty/malformed submissions
    action = (request.form.get('action') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip()
    reviewer = current_user.username

    logger.info(
        '[PAYMENT_REVIEW] route hit: submission_id=%s action=%r reviewer=%s form_keys=%s',
        submission_id, action, reviewer, list(request.form.keys()),
    )

    if action not in VALID_REVIEW_ACTIONS:
        logger.warning(
            '[PAYMENT_REVIEW] Invalid action=%r for submission_id=%s — form keys: %s',
            action, submission_id, list(request.form.keys()),
        )
        flash(
            f'Invalid review action "{action or "(empty)"}". '
            'Use the Approve or Reject buttons on the submission row.',
            'danger',
        )
        return redirect(url_for('superadmin.billing_submissions'))

    try:
        if action == 'approve':
            ok, message = approve_payment_submission(
                submission, reviewer=reviewer, review_notes=review_notes,
            )
            flash(message, 'success' if ok else 'danger')
        else:  # action == 'reject'
            ok, message = reject_payment_submission(
                submission, reviewer=reviewer, review_notes=review_notes,
            )
            flash(message, 'success' if ok else 'warning')

    except Exception as exc:
        # Last-resort catch — service should have already rolled back
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception(
            '[PAYMENT_REVIEW] Unhandled exception for submission_id=%s action=%s: %s',
            submission_id, action, exc,
        )
        flash(
            f'An unexpected error occurred during payment review. '
            f'The submission has not been changed. ({type(exc).__name__})',
            'danger',                        # ← closing the flash() call
        )

    return redirect(url_for('superadmin.billing_submissions'))  # ← always redirect
