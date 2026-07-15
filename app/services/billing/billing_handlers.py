"""
app/services/billing/billing_handlers.py — Portfolio CMS
========================================================
PATCHED: adds discount / coupon support to the tenant checkout flow.

Additive changes only. Existing behaviour is preserved when no coupon is
entered — the code path is identical to the pre-patch version.

Changes vs. the previous version:
  1) billing_plans_context() computes a DiscountQuote (auto-apply aware)
     and exposes `discount_quote` to the template.
  2) handle_billing_plans_post() reads the `coupon_code` form field,
     stashes it in the session via discount_checkout.stash_coupon(), so
     the same code is picked up when the subscription actually activates.
  3) handle_billing_payment_post() calls discount_checkout.apply_on_activation()
     after submit_manual_payment(), so manual-approval flows also
     redeem the coupon.

PayMongo webhook redemption is intentionally NOT wired in this file — do
it in one line inside your existing webhook handler (see
DISCOUNT_CHECKOUT_INTEGRATION.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app, flash, redirect, request, url_for

from app import db
from app.forms import PaymentUploadForm, PlanSelectionForm
from app.models.portfolio import normalize_plan_name
from app.models import Subscription
from app.services.billing import get_or_create_pending_subscription, initiate_checkout, subscription_access_status
from app.services.billing.dodo_service import is_dodo_enabled, create_checkout_session
from app.services.billing import discount_checkout, discount_service
from app.services.billing.currency import (
    currency_context, currency_symbol, format_money, get_currency_settings,
    get_exchange_rate, get_plan_usd_amount,
)
from app.services.billing.countries import (
    country_details, country_options, normalize_country_code,
)
from app.services.billing.discount_service import DiscountQuote
from app.services.billing.trial_history import ensure_profile_trial_history
from app.utils import get_plan_price, get_public_billing_plans
from app.services.manual_billing import (
    get_active_payment_methods_for_tenant,
    get_manual_payment_methods,
    get_payment_method_for_tenant,
    save_billing_upload,
    submit_manual_payment,
)
from app.utils import BILLING_PLANS
from app.system_plan import ADMINISTRATOR_PLAN, has_administrator_access, is_administrator_plan

logger = logging.getLogger(__name__)


def _money_decimal(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_country_payment_quote(profile, *, billing_cycle: str = "monthly", country_code: str | None = None) -> dict:
    """Build an immutable USD quote plus a tenant-country converted quote."""
    subscription = profile.current_subscription()
    plan = normalize_plan_name(subscription.plan if subscription else profile.plan or "Basic")
    tenant = getattr(profile, "tenant", None)
    saved_country = getattr(tenant, "country_code", None) if tenant is not None else None
    country = country_details(country_code or saved_country or "PH")
    currency_code = country["currency"]

    usd_base = get_plan_usd_amount(plan, billing_cycle)
    stashed = discount_checkout.peek_coupon(profile.tenant_id)
    quote_usd = discount_checkout.quote_for_context(
        tenant_id=profile.tenant_id,
        plan=plan,
        billing_cycle=billing_cycle,
        code=stashed,
        base_amount_override=usd_base,
    )
    fx = get_exchange_rate(target=currency_code)
    rate = Decimal(str(fx.get("rate") or 1))

    def converted(value: Decimal) -> Decimal:
        return (value * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    quote_local = DiscountQuote(
        campaign=quote_usd.campaign,
        plan=quote_usd.plan,
        billing_cycle=quote_usd.billing_cycle,
        amount_before=converted(quote_usd.amount_before),
        amount_discounted=converted(quote_usd.amount_discounted),
        amount_after=converted(quote_usd.amount_after),
    )
    currency = currency_context(target=currency_code)
    return {
        "plan": plan,
        "country": country,
        "currency": currency,
        "fx": fx,
        "quote_usd": quote_usd,
        "quote_local": quote_local,
        "amount_usd": float(quote_usd.amount_after),
        "amount_local": float(quote_local.amount_after),
        "fx_available": bool(fx.get("available", True)),
        "coupon_code": stashed or "",
    }


def country_payment_quote_payload(profile, *, billing_cycle: str, country_code: str | None) -> dict:
    data = build_country_payment_quote(profile, billing_cycle=billing_cycle, country_code=country_code)
    quote = data["quote_local"]
    quote_usd = data["quote_usd"]
    currency = data["currency"]
    fx = data["fx"]
    return {
        "ok": bool(data["fx_available"]),
        "country": data["country"],
        "currency": currency["display_currency"],
        "symbol": currency["symbol"],
        "amount": float(quote.amount_after),
        "amount_before": float(quote.amount_before),
        "discount": float(quote.amount_discounted),
        "amount_usd": float(quote_usd.amount_after),
        "formatted": format_money(quote.amount_after, currency["display_currency"], include_code=True),
        "rate": float(fx.get("rate") or 1),
        "provider": fx.get("provider") or "fixed",
        "stale": bool(fx.get("stale")),
        "source_date": fx.get("source_date") or "",
        "error": fx.get("error") or "",
    }


def billing_plans_context(profile, *, tenant_slug: str | None, billing_routes: dict, paymongo_enabled: bool):
    """Build a consistent tenant billing-plan context.

    Trial entitlement is stored on ``Tenant`` rather than in ``Profile.plan``.
    Older code treated the profile's paid fallback (usually ``Basic``) as the
    active plan, which made trial accounts look subscribed.  This resolver
    keeps current, pending, and trial states separate.
    """
    is_admin_plan = has_administrator_access(profile)

    # Backfill the zero-cost trial timeline row for existing accounts.  This is
    # best-effort and does not alter entitlements or the user's paid plan.
    if not is_admin_plan:
        ensure_profile_trial_history(profile, commit=True)
        if hasattr(profile, '_current_subscription_cache'):
            del profile._current_subscription_cache

    tenant = getattr(profile, 'tenant', None)
    access_state = 'active' if is_admin_plan else subscription_access_status(profile)
    is_trial = (not is_admin_plan and access_state == 'trial')

    active_subscription = None
    pending_subscription = None
    if not is_admin_plan:
        active_subscription = (
            Subscription.query
            .filter_by(tenant_id=profile.tenant_id, status='active')
            .order_by(Subscription.created_at.desc())
            .first()
        )
        if active_subscription is not None:
            active_subscription.refresh_status(commit=False)
            if not active_subscription.is_active():
                active_subscription = None

        pending_subscription = (
            Subscription.query
            .filter(
                Subscription.tenant_id == profile.tenant_id,
                Subscription.status.in_(['pending', 'scheduled']),
            )
            .order_by(Subscription.created_at.desc())
            .first()
        )

    if is_admin_plan:
        current_plan = 'Administrator'
    elif is_trial:
        current_plan = 'Trial'
    elif active_subscription is not None:
        current_plan = normalize_plan_name(active_subscription.plan)
    else:
        current_plan = normalize_plan_name(profile.plan or 'Basic')

    # Paid cards should not be preselected merely because Profile.plan keeps a
    # fallback value during a trial.  A pending checkout may be prefilled, but
    # it is clearly labelled as pending rather than current.
    selected_candidate = (
        request.values.get('plan')
        or (pending_subscription.plan if pending_subscription is not None else None)
        or (current_plan if current_plan in get_public_billing_plans() else 'Basic')
    )
    selected_candidate = normalize_plan_name(selected_candidate)
    form = PlanSelectionForm(plan=selected_candidate)

    manual_methods = [] if is_admin_plan else get_manual_payment_methods(profile.tenant_id)

    if not manual_methods and not paymongo_enabled:
        logger.warning(
            'BILLING plans_context: tenant_id=%s has no manual payment methods '
            'and PayMongo is disabled — billing page will show the "no methods" warning.',
            profile.tenant_id,
        )

    billing_cycle = request.values.get('billing_cycle', 'monthly')
    coupon_code = (request.values.get('coupon_code') or '').strip().upper() or None
    if coupon_code is None:
        coupon_code = discount_checkout.peek_coupon(profile.tenant_id)

    quote_plan = selected_candidate if selected_candidate in get_public_billing_plans() else 'Basic'
    discount_quote = None if is_admin_plan else discount_checkout.quote_for_context(
        tenant_id=profile.tenant_id,
        plan=quote_plan,
        billing_cycle=billing_cycle,
        code=coupon_code,
    )

    promo_campaign = discount_service.get_promo_banner_campaign()
    promo_eligible_plans: list[str] = []
    promo_scope_label = None
    if promo_campaign:
        if promo_campaign.plan_slug:
            promo_eligible_plans = [normalize_plan_name(promo_campaign.plan_slug)]
            promo_scope_label = f"{promo_eligible_plans[0]} Plan only"
        else:
            promo_eligible_plans = list(get_public_billing_plans().keys())
            promo_scope_label = "All Plans"

    trial_ends_at = None
    if tenant is not None:
        trial_ends_at = getattr(tenant, 'trial_ends_at', None)
    if trial_ends_at is None:
        trial_ends_at = getattr(profile, 'free_trial_ends', None)

    return dict(
        profile=profile,
        subscription=active_subscription,
        active_subscription=active_subscription,
        pending_subscription=pending_subscription,
        current_plan=current_plan,
        subscription_state=access_state,
        is_trial=is_trial,
        trial_days_left=profile.trial_days_remaining() if is_trial else 0,
        trial_ends_at=trial_ends_at,
        form=form,
        plans=get_public_billing_plans(),
        is_administrator_plan=is_admin_plan,
        administrator_plan=ADMINISTRATOR_PLAN,
        tenant_slug=tenant_slug,
        billing_routes=billing_routes,
        paymongo_enabled=paymongo_enabled,
        automated_gateway_name=('Dodo Payments' if is_dodo_enabled() else 'PayMongo'),
        dodo_enabled=is_dodo_enabled(),
        payment_methods=manual_methods,
        manual_payment_enabled=bool(manual_methods),
        show_billing_tabs=False,
        activation_eta='Usually within 24 hours',
        discount_quote=discount_quote,
        coupon_code=coupon_code or '',
        promo_campaign=promo_campaign,
        promo_eligible_plans=promo_eligible_plans,
        promo_scope_label=promo_scope_label,
        currency=currency_context(),
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
    Process plan selection POST. Same dispatch table as before —
    'checkout' / 'manual' / 'method_<int>' — plus:
      * If the form includes a non-empty `coupon_code`, we validate it
        (via quote_discount) and stash it in the session. An invalid
        coupon flashes a warning but does NOT block the redirect — the
        user can still complete checkout at full price.
    """
    if has_administrator_access(profile):
        flash('This protected system portfolio already has Administrator full access. No checkout is required.', 'info')
        return redirect(url_for(return_endpoint or billing_routes.get('overview', 'admin.billing_index'))), None

    selected_plan = normalize_plan_name(request.form.get('plan') or profile.plan or 'Basic')
    if is_administrator_plan(selected_plan):
        flash('Administrator is an internal system plan and cannot be selected or purchased.', 'danger')
        return None, None
    billing_cycle = request.form.get('billing_cycle', 'monthly')
    action = request.form.get('action', 'checkout')

    # -- Coupon intake ------------------------------------------------------
    raw_code = (request.form.get('coupon_code') or '').strip().upper()
    if raw_code:
        try:
            # quote_discount raises DiscountError subclasses on bad codes.
            from app.services.billing.discount_service import DiscountError, quote_discount
            quote_discount(
                tenant_id=profile.tenant_id,
                plan=selected_plan,
                billing_cycle=billing_cycle,
                code=raw_code,
            )
        except Exception as exc:  # DiscountError, but be defensive
            flash(f'Coupon "{raw_code}" could not be applied: {exc}', 'warning')
            discount_checkout.stash_coupon(profile.tenant_id, '')  # clear stash
        else:
            discount_checkout.stash_coupon(profile.tenant_id, raw_code)
    else:
        # Empty submission clears any previously stashed coupon.
        discount_checkout.stash_coupon(profile.tenant_id, '')

    # -- Pending subscription ------------------------------------------------
    try:
        sub = get_or_create_pending_subscription(
            db.session, profile.tenant_id, selected_plan, billing_cycle=billing_cycle
        )
        # Persist the coupon on the subscription row itself — this is the
        # durable reference read later by activation paths that run outside
        # the tenant's browser session (PayMongo webhook, superadmin manual
        # approval/resync). get_or_create_pending_subscription() already
        # reset coupon_code=None on this row above; only set it if the
        # coupon actually validated (raw_code was stashed, not cleared).
        sub.coupon_code = discount_checkout.peek_coupon(profile.tenant_id)
        db.session.commit()
        if hasattr(profile, '_current_subscription_cache'):
            del profile._current_subscription_cache
    except Exception as exc:
        db.session.rollback()
        flash('Failed to save plan selection. Please try again.', 'danger')
        return None, exc

    if action == 'checkout' and (is_dodo_enabled() or paymongo_enabled):
        base_url = current_app.config.get('APP_BASE_URL', '').rstrip('/')
        slug = tenant_slug or 'default'

        # Studio checkout uses a hidden return endpoint that immediately sends
        # the tenant back to the dashboard with a clear payment-state banner.
        # The redirect is UX only; verified webhooks remain authoritative.
        if (return_endpoint or '').startswith('admin.'):
            try:
                success_url = url_for('admin.dodo_checkout_return', outcome='success', _external=True)
                cancel_url = url_for('admin.dodo_checkout_return', outcome='cancelled', _external=True)
            except Exception:
                root = base_url or ''
                success_url = f'{root}/studio/billing/dodo/return?outcome=success'
                cancel_url = f'{root}/studio/billing/dodo/return?outcome=cancelled'
        elif base_url:
            success_url = f'{base_url}/{slug}/billing/plans?status=success'
            cancel_url  = f'{base_url}/{slug}/billing/plans?status=cancelled'
        else:
            plans_endpoint = billing_routes.get('plans', 'tenant.billing_plans')
            try:
                success_url = url_for(plans_endpoint, tenant_slug=slug, status='success', _external=True)
                cancel_url  = url_for(plans_endpoint, tenant_slug=slug, status='cancelled', _external=True)
            except Exception:
                success_url = f'/{slug}/billing/plans?status=success'
                cancel_url  = f'/{slug}/billing/plans?status=cancelled'

        if is_dodo_enabled():
            result = create_checkout_session(
                profile=profile, subscription=sub, plan=selected_plan,
                billing_cycle=billing_cycle, return_url=success_url, cancel_url=cancel_url,
            )
            if result.ok and result.checkout_url:
                sub.payment_provider = 'dodo'
                sub.dodo_checkout_session_id = result.session_id
                sub.payment_method = 'dodo'
                db.session.commit()
                return redirect(result.checkout_url), None
            flash(result.error or 'Could not start Dodo Payments checkout.', 'warning')
            return None, None

        checkout_url, error = initiate_checkout(
            db.session, profile, selected_plan, billing_cycle=billing_cycle,
            success_url=success_url, cancel_url=cancel_url,
        )
        if checkout_url:
            return redirect(checkout_url), None
        flash(error or 'Could not start automated checkout. Try a manual payment method instead.', 'warning')
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
    if has_administrator_access(profile):
        raise ValueError('Administrator plan does not require payment.')

    requested_country = request.values.get('country_code') or request.args.get('country')
    payment_quote = build_country_payment_quote(
        profile, billing_cycle=billing_cycle, country_code=requested_country,
    )
    subscription = profile.current_subscription()
    form = PaymentUploadForm()
    form.payment_method_id.data = str(method.id)
    form.amount_paid.data = f"{payment_quote['amount_local']:.2f}"

    return dict(
        profile=profile,
        subscription=subscription,
        payment_method=method,
        form=form,
        plans=get_public_billing_plans(),
        tenant_slug=tenant_slug,
        billing_routes=billing_routes,
        billing_cycle=billing_cycle,
        suggested_amount=payment_quote['amount_local'],
        suggested_amount_usd=payment_quote['amount_usd'],
        activation_eta='Usually within 24 hours',
        show_billing_tabs=False,
        discount_quote=payment_quote['quote_local'],
        discount_quote_usd=payment_quote['quote_usd'],
        coupon_code=payment_quote['coupon_code'],
        currency=payment_quote['currency'],
        fx=payment_quote['fx'],
        fx_available=payment_quote['fx_available'],
        countries=country_options(),
        selected_country=payment_quote['country'],
        suggested_amount_label=format_money(
            payment_quote['amount_local'],
            payment_quote['currency']['display_currency'],
            include_code=True,
        ),
    )


def handle_billing_payment_post(profile, method, *, billing_cycle: str = 'monthly', success_redirect):
    form = PaymentUploadForm()
    if not form.validate_on_submit():
        for field, errors in form.errors.items():
            for err in errors:
                flash(f'{field}: {err}', 'danger')
        return None

    # Never trust browser-submitted country, currency, rate, or amount.
    # Country is normalized against a server-side allow-list, currency is
    # derived from country, and the exact local total is converted from USD.
    country_code = normalize_country_code(request.form.get('country_code'))
    payment_quote = build_country_payment_quote(
        profile, billing_cycle=billing_cycle, country_code=country_code,
    )
    plan = payment_quote['plan']
    if not payment_quote['fx_available'] and payment_quote['currency']['display_currency'] != 'USD':
        flash('The exchange rate is temporarily unavailable. Please retry or choose United States (USD).', 'danger')
        return None
    amount_paid = payment_quote['amount_local']

    # Proof is mandatory in both WTForms and server-side business logic.
    if not form.payment_proof.data:
        flash('Please upload proof of payment before submitting.', 'danger')
        return None
    proof_filename, err = save_billing_upload(form.payment_proof.data, image_only=False)
    if err:
        flash(err, 'danger')
        return None
    tenant = getattr(profile, 'tenant', None)
    if tenant is not None:
        tenant.country_code = payment_quote['country']['code']
        tenant.preferred_currency = payment_quote['currency']['display_currency']
        tenant.country_source = 'billing_selection'
        tenant.country_updated_at = datetime.now(timezone.utc)
        db.session.add(tenant)

    try:
        submission = submit_manual_payment(
            profile,
            method=method,
            plan=plan,
            amount_paid=amount_paid,
            payment_reference=form.payment_reference.data or '',
            note=form.payment_note.data or '',
            proof_filename=proof_filename,
            billing_cycle=billing_cycle,
            expected_amount=payment_quote['amount_local'],
            amount_usd=payment_quote['amount_usd'],
            currency_code=payment_quote['currency']['display_currency'],
            exchange_rate=float(payment_quote['fx'].get('rate') or 1),
            country_code=payment_quote['country']['code'],
        )
    except Exception:
        db.session.rollback()
        logger.exception(
            'Manual payment submission failed after private proof storage: tenant_id=%s',
            profile.tenant_id,
        )
        # Delete the newly stored object only when the database proves no row
        # references it. If the database check itself fails, preserve the file
        # for reconciliation rather than risk deleting committed evidence.
        try:
            from app.models.portfolio import PaymentSubmission
            persisted = PaymentSubmission.query.filter_by(
                payment_proof=proof_filename,
            ).with_entities(PaymentSubmission.id).first()
            if persisted is None:
                from app.services.billing.private_proof_storage import (
                    delete_private_billing_proof,
                )
                delete_private_billing_proof(proof_filename)
        except Exception:
            logger.exception(
                'Could not reconcile private proof after failed manual submission'
            )
        flash(
            'Your payment submission could not be recorded. No subscription was changed; please try again.',
            'danger',
        )
        return None

    # NOTE: For manual payments, actual activation happens later when a
    # superadmin approves the submission (manual_billing.approve_payment_submission),
    # which now calls discount_checkout.apply_on_activation() using
    # sub.coupon_code — the durable field set above in
    # handle_billing_plans_post, not the tenant's (unavailable, out-of-session)
    # Flask session stash. No action needed here.

    flash(
        'Thank you! Your payment has been received. Please wait up to 24 hours for manual review. '
        'Your portfolio subscription will be activated shortly after approval.',
        'success',
    )
    return redirect(success_redirect)
