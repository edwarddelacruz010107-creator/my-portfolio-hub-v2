"""
Shared billing view helpers for admin, tenant, and main blueprints.

v3.9 patches applied:
  • BUG-001 / BUG-007 FIX: handle_billing_plans_post() now passes db.session as
    the first argument to initiate_checkout(), and constructs proper success_url /
    cancel_url using url_for() instead of the non-existent return_endpoint kwarg.
"""

from __future__ import annotations

import logging

from flask import flash, redirect, request, url_for

from app import db
from app.forms import PaymentUploadForm, PlanSelectionForm
from app.models.portfolio import normalize_plan_name
from app.services.billing import get_or_create_pending_subscription, initiate_checkout
from app.utils import get_plan_price
from app.services.manual_billing import (
    get_active_payment_methods_for_tenant,
    get_manual_payment_methods,
    get_payment_method_for_tenant,
    save_billing_upload,
    submit_manual_payment,
)
from app.utils import BILLING_PLANS

logger = logging.getLogger(__name__)


def billing_plans_context(profile, *, tenant_slug: str | None, billing_routes: dict, paymongo_enabled: bool):
    """
    Build context dict for billing/plans templates.
    """
    subscription = profile.current_subscription()
    form = PlanSelectionForm(
        plan=normalize_plan_name(subscription.plan if subscription else profile.plan or 'Basic'),
    )

    manual_methods = get_manual_payment_methods(profile.tenant_id)

    if not manual_methods and not paymongo_enabled:
        logger.warning(
            'BILLING plans_context: tenant_id=%s has no manual payment methods '
            'and PayMongo is disabled — billing page will show the "no methods" warning.',
            profile.tenant_id,
        )

    return dict(
        profile=profile,
        subscription=subscription,
        form=form,
        plans=BILLING_PLANS,
        tenant_slug=tenant_slug,
        billing_routes=billing_routes,
        paymongo_enabled=paymongo_enabled,
        payment_methods=manual_methods,
        manual_payment_enabled=bool(manual_methods),
        show_billing_tabs=False,
        activation_eta='Usually within 24 hours',
    )


def handle_billing_plans_post(
    profile,
    *,
    tenant_slug: str | None,
    billing_routes: dict,
    paymongo_enabled: bool,
    return_endpoint: str | None = None,
    payment_route_builder,
):
    """
    Process plan selection POST.

    BUG-001 / BUG-007 FIX:
      • db.session is now passed as the first argument to initiate_checkout()
      • success_url / cancel_url are constructed from url_for(), not return_endpoint
      • return_endpoint is retained as a kwarg for backward-compat but is now used
        only as the cancel destination, not passed into initiate_checkout as a kwarg

    payment_route_builder(method_id, billing_cycle=...) -> redirect URL string.
    """
    selected_plan = normalize_plan_name(request.form.get('plan') or profile.plan or 'Basic')
    billing_cycle = request.form.get('billing_cycle', 'monthly')
    action = request.form.get('action', 'checkout')

    try:
        get_or_create_pending_subscription(db.session, profile.tenant_id, selected_plan, billing_cycle=billing_cycle)
        db.session.commit()
        if hasattr(profile, '_current_subscription_cache'):
            del profile._current_subscription_cache
    except Exception as exc:
        db.session.rollback()
        flash('Failed to save plan selection. Please try again.', 'danger')
        return None, exc

    if action == 'checkout' and paymongo_enabled:
        # BUG-001 FIX: build proper success_url/cancel_url; pass db.session first
        plans_endpoint = return_endpoint or billing_routes.get('plans', 'tenant.billing_plans')
        try:
            if tenant_slug:
                success_url = url_for(plans_endpoint, tenant_slug=tenant_slug, status='success', _external=True)
                cancel_url  = url_for(plans_endpoint, tenant_slug=tenant_slug, status='cancelled', _external=True)
            else:
                success_url = url_for(plans_endpoint, status='success', _external=True)
                cancel_url  = url_for(plans_endpoint, status='cancelled', _external=True)
        except Exception:
            from flask import current_app
            base = current_app.config.get('APP_BASE_URL', '').rstrip('/')
            success_url = f'{base}/billing/plans?status=success'
            cancel_url  = f'{base}/billing/plans?status=cancelled'

        checkout_url, error = initiate_checkout(
            db.session,        # BUG-007 FIX: pass db_session as first argument
            profile,
            selected_plan,
            billing_cycle=billing_cycle,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        if checkout_url:
            return redirect(checkout_url), None
        flash(error or 'Could not start PayMongo checkout. Try a manual payment method instead.', 'warning')
        return None, None

    if action == 'manual' or action.startswith('method_'):
        method_id = request.form.get('method_id') or action.replace('method_', '')
        try:
            method_id_int = int(method_id)
        except (TypeError, ValueError):
            flash('Please select a valid payment method.', 'danger')
            return None, None

        method = get_payment_method_for_tenant(method_id_int, profile.tenant_id)
        if not method:
            flash('Selected payment method is not available.', 'danger')
            return None, None
        return redirect(payment_route_builder(method_id_int, billing_cycle=billing_cycle)), None

    flash(f'Plan saved: {selected_plan}. Choose a payment method below.', 'info')
    return None, None


def billing_payment_context(
    profile,
    method,
    *,
    tenant_slug: str | None,
    billing_routes: dict,
    billing_cycle: str = 'monthly',
):
    subscription = profile.current_subscription()
    plan = normalize_plan_name(subscription.plan if subscription else profile.plan or 'Basic')
    form = PaymentUploadForm()
    form.payment_method_id.data = str(method.id)
    suggested_amount = get_plan_price(plan, billing_cycle)
    form.amount_paid.data = f'{suggested_amount:.2f}'
    return dict(
        profile=profile,
        subscription=subscription,
        payment_method=method,
        form=form,
        plans=BILLING_PLANS,
        tenant_slug=tenant_slug,
        billing_routes=billing_routes,
        billing_cycle=billing_cycle,
        suggested_amount=suggested_amount,
        activation_eta='Usually within 24 hours',
        show_billing_tabs=False,
    )


def handle_billing_payment_post(profile, method, *, billing_cycle: str = 'monthly', success_redirect):
    form = PaymentUploadForm()
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            for err in errors:
                flash(f'{field}: {err}', 'danger')
        return None

    amount_raw = (form.amount_paid.data or '').replace(',', '').strip()
    try:
        amount_paid = float(amount_raw)
    except ValueError:
        flash('Please enter a valid amount paid.', 'danger')
        return None

    proof_filename = None
    if form.payment_proof.data:
        proof_filename, err = save_billing_upload(form.payment_proof.data, image_only=False)
        if err:
            flash(err, 'danger')
            return None

    plan = normalize_plan_name(
        profile.current_subscription().plan
        if profile.current_subscription()
        else profile.plan or 'Basic'
    )
    submit_manual_payment(
        profile,
        method=method,
        plan=plan,
        amount_paid=amount_paid,
        payment_reference=form.payment_reference.data or '',
        note=form.payment_note.data or '',
        proof_filename=proof_filename,
        billing_cycle=billing_cycle,
    )
    flash(
        'Thank you! Your payment has been received. Please wait up to 24 hours for manual review. '
        'Your portfolio subscription will be activated shortly after approval.',
        'success',
    )
    return redirect(success_redirect)
