"""
app/superadmin/routes/subscriptions.py — Subscription settings + license management (Phase 4b, batch 6)

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


from app.utils import (
    log_activity, BILLING_PLANS, YEARLY_DISCOUNT, get_yearly_discount,
    get_public_billing_plans, get_system_billing_plans,
)
from app.system_plan import (
    ADMINISTRATOR_PLAN_NAME, ensure_default_tenant_administrator_plan,
    has_administrator_access, is_administrator_plan,
)
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
from app.services.billing.currency import (
    BASE_CURRENCY, SUPPORTED_CURRENCIES, apply_currency_pricing,
    currency_context, format_money, get_currency_settings,
    save_currency_settings, save_plan_config, save_plan_usd_prices,
)
from app.utils.datetime_utils import ensure_utc_aware, utc_now

from app.services.billing.trial_limits import (
    get_trial_limits, save_trial_limits, reset_trial_limits, trial_field_metadata,
    get_all_plan_limits, save_plan_limits, reset_plan_limits, plan_field_metadata,
    normalize_editable_plan, plan_slug,
)


from app.superadmin.blueprint import superadmin, superadmin_required, _normalize_timestamp

logger = logging.getLogger(__name__)


def _parse_utc_datetime_local(raw: str | None) -> datetime | None:
    value = (raw or '').strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Use a valid date and time.') from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _manageable_subscription_plans() -> dict[str, dict]:
    """Plans a superadmin may assign, including a zero-cost Trial."""
    plans = {
        'Trial': {
            'label': 'Trial',
            'currency_symbol': '$',
            'currency_code': 'USD',
            'base_price_usd': 0.0,
            'price_monthly': 0.0,
            'duration_days': int(get_trial_limits().get('trial_duration_days', 7) or 7),
            'price_label': 'Free trial',
        }
    }
    plans.update(get_public_billing_plans())
    return plans


def _sync_managed_subscription(profile: Profile, subscription: Subscription, desired_status: str) -> None:
    tenant = Tenant.query.get(profile.tenant_id)
    if tenant is None:
        raise ValueError('Tenant record not found.')

    if hasattr(profile, '_current_subscription_cache'):
        del profile._current_subscription_cache

    normalized_plan = normalize_plan_name(subscription.plan)
    tenant.plan = normalized_plan
    tenant.plan_name = normalized_plan
    tenant.subscription_started_at = subscription.started_at
    tenant.subscription_expires_at = subscription.expires_at

    if desired_status == 'active' and normalized_plan == 'Trial':
        expires = ensure_utc_aware(subscription.expires_at)
        remaining = max(0, ((expires - utc_now()).days + 1) if expires else 0)
        tenant.subscription_state = 'trial'
        tenant.status = 'active'
        tenant.trial_status = 'active'
        tenant.trial_ends_at = subscription.expires_at
        profile.plan = 'Trial'
        profile.free_trial_days = remaining
        profile.free_trial_ends = subscription.expires_at
    elif desired_status == 'active':
        tenant.subscription_state = 'active'
        tenant.status = 'active'
        tenant.trial_status = 'ended'
        tenant.trial_ends_at = None
        profile.plan = normalized_plan
        profile.free_trial_days = 0
        profile.free_trial_ends = None
    elif desired_status == 'scheduled':
        # Preserve an existing live trial until the scheduled paid plan begins.
        tenant.subscription_state = 'trial' if tenant.trial_ends_at and ensure_utc_aware(tenant.trial_ends_at) > utc_now() else 'pending'
        tenant.status = 'active'
    elif desired_status == 'pending':
        tenant.subscription_state = 'pending'
        tenant.status = 'active'
    elif desired_status in {'expired', 'cancelled'}:
        tenant.subscription_state = desired_status
        tenant.status = 'suspended'


@superadmin.route('/subscriptions', methods=['GET', 'POST'])
@superadmin_required
def subscription_settings():
    """Superadmin subscription settings: manage subscription plans and tenant subscriptions."""
    from app.models.portfolio import Profile

    if request.method == 'POST':
        action = request.form.get('action')

        if action in {'update_currency', 'refresh_fx'}:
            try:
                if action == 'update_currency':
                    settings = save_currency_settings(
                        display_currency=request.form.get('display_currency') or 'USD',
                        provider=request.form.get('fx_provider') or 'frankfurter',
                        refresh_minutes=int(request.form.get('fx_refresh_minutes') or 60),
                    )
                else:
                    settings = get_currency_settings()
                fx_ctx = currency_context(force=True)
                apply_currency_pricing(BILLING_PLANS)
                db.session.commit()
                if fx_ctx['fx'].get('available'):
                    flash(
                        f"Currency updated: prices use USD as the base and display in {settings['display_currency']} "
                        f"at 1 USD = {fx_ctx['fx']['rate']:.6g} {settings['display_currency']}.",
                        'success',
                    )
                else:
                    flash('Currency settings saved, but the rate provider is currently unavailable. USD pricing remains authoritative.', 'warning')
            except Exception as exc:
                db.session.rollback()
                logger.exception('Unable to update billing currency: %s', exc)
                flash('Unable to update currency settings. Check the provider configuration and try again.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        if action == 'manage_subscription':
            profile = profile_repository.get(request.form.get('tenant_id'))
            if not profile:
                flash('Tenant profile not found.', 'danger')
                return redirect(url_for('superadmin.subscription_settings'))
            if has_administrator_access(profile):
                ensure_default_tenant_administrator_plan(commit=True)
                flash('The protected Administrator portfolio cannot be rescheduled or downgraded.', 'info')
                return redirect(url_for('superadmin.subscription_settings'))
            try:
                plan = normalize_plan_name(request.form.get('plan') or 'Basic')
                manageable_plans = _manageable_subscription_plans()
                if plan not in manageable_plans:
                    raise ValueError('Select a valid tenant plan.')
                desired_status = (request.form.get('subscription_status') or 'active').strip().lower()
                if desired_status not in {'active', 'scheduled', 'pending', 'expired', 'cancelled'}:
                    raise ValueError('Select a valid subscription status.')

                start_at = _parse_utc_datetime_local(request.form.get('starts_at'))
                expires_at = _parse_utc_datetime_local(request.form.get('expires_at'))
                now = utc_now()
                if desired_status == 'active' and start_at is None:
                    start_at = now
                if desired_status == 'scheduled':
                    if start_at is None:
                        raise ValueError('A scheduled subscription requires a start date and time.')
                    if start_at <= now:
                        desired_status = 'active'
                if desired_status in {'active', 'scheduled'} and expires_at is None:
                    duration = int(manageable_plans[plan].get('duration_days', 30))
                    expires_at = (start_at or now) + timedelta(days=duration)
                if start_at and expires_at and expires_at <= start_at:
                    raise ValueError('Expiration must be later than the start date.')

                subscription = profile.current_subscription()
                if subscription is None or subscription.status == 'cancelled':
                    subscription = Subscription(tenant_id=profile.tenant_id)
                    db.session.add(subscription)
                subscription.plan = plan
                subscription.billing_cycle = request.form.get('billing_cycle') or 'monthly'
                subscription.status = desired_status
                subscription.started_at = start_at
                subscription.expires_at = expires_at
                subscription.cancelled_at = now if desired_status == 'cancelled' else None
                subscription.payment_method = 'superadmin-scheduled' if desired_status == 'scheduled' else 'superadmin-managed'
                subscription.reminder_sent_7d = False
                subscription.reminder_sent_30d = False

                _sync_managed_subscription(profile, subscription, desired_status)
                db.session.commit()
                log_activity(
                    'update', 'subscription', profile.tenant_slug,
                    f'Superadmin set {plan} subscription to {desired_status}; start={start_at}; expires={expires_at}',
                )
                verb = 'scheduled' if desired_status == 'scheduled' else 'updated'
                flash(f'{profile.tenant_slug} subscription {verb} successfully.', 'success')
            except Exception as exc:
                db.session.rollback()
                logger.exception('Unable to manage subscription for profile=%s: %s', getattr(profile, 'id', None), exc)
                flash(str(exc) if isinstance(exc, ValueError) else 'Unable to update the tenant subscription.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        if action in {'update_plan_limits', 'update_trial_limits'}:
            try:
                plan_name = normalize_editable_plan(request.form.get('plan_name') or 'Trial')
                if action == 'update_trial_limits':
                    plan_name = 'Trial'
                    save_trial_limits(request.form)
                else:
                    save_plan_limits(plan_name, request.form)
                db.session.commit()
                log_activity('update', 'config', f'{plan_slug(plan_name)}_plan_limits', f'Updated {plan_name} plan limits')
                flash(f'{plan_name} plan limits updated successfully. Admin dashboard feature gates now use these settings.', 'success')
            except Exception as exc:
                db.session.rollback()
                logger.exception('Failed to update plan limits: %s', exc)
                flash('Unable to save plan limits. Please check the values and try again.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        if action in {'reset_plan_limits', 'reset_trial_limits'}:
            try:
                plan_name = normalize_editable_plan(request.form.get('plan_name') or 'Trial')
                if action == 'reset_trial_limits':
                    plan_name = 'Trial'
                    reset_trial_limits()
                else:
                    reset_plan_limits(plan_name)
                db.session.commit()
                log_activity('update', 'config', f'{plan_slug(plan_name)}_plan_limits', f'Reset {plan_name} plan limits to defaults')
                flash(f'{plan_name} plan limits were reset to defaults.', 'success')
            except Exception as exc:
                db.session.rollback()
                logger.exception('Failed to reset plan limits: %s', exc)
                flash('Unable to reset plan limits.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        if action == 'update_plans':
            # Update persisted USD base prices and plan presentation settings.
            updated_prices = {}
            updated_config = {}
            for plan_key, plan_data in get_public_billing_plans().items():
                label = request.form.get(f'plan_label_{plan_key}', plan_data['label']).strip() or plan_data['label']
                price = request.form.get(f'plan_price_usd_{plan_key}', request.form.get(f'plan_price_{plan_key}', plan_data.get('base_price_usd', plan_data['price'])))
                duration = request.form.get(f'plan_duration_{plan_key}', plan_data.get('duration_days', 30))
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
                plan_data['base_price_usd'] = price
                plan_data['price'] = price
                plan_data['price_monthly'] = price
                # Recalc yearly using the live yearly-discount rate for this plan
                # (per-plan override if the superadmin has set one, else the
                # global rate) instead of the stale hardcoded constant — keeps
                # this in sync with the Discounts & Promotions page.
                plan_data['price_yearly'] = round(price * 12 * get_yearly_discount(plan_key), 2)
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
                plan_data['price_label'] = f"${price:,.2f} USD/mo"
                updated_prices[plan_key] = price
                updated_config[plan_key] = {
                    'label': label,
                    'duration_days': duration,
                    'description': description,
                    'features': plan_data['features'],
                    'payment_link': payment_link,
                }

            save_plan_usd_prices(updated_prices)
            save_plan_config(updated_config)
            apply_currency_pricing(BILLING_PLANS)
            db.session.commit()
            log_activity('update', 'config', 'subscription_plans', 'Updated USD base billing plans')
            flash('Subscription plans updated successfully. USD remains the base price and display totals were recalculated.', 'success')
            return redirect(url_for('superadmin.subscription_settings'))

        elif request.form.get('action') == 'force_activate':
            profile = profile_repository.get(request.form.get('tenant_id'))
            if profile:
                plan = request.form.get('plan') or profile.plan or 'Basic'
                if has_administrator_access(profile):
                    ensure_default_tenant_administrator_plan(commit=True)
                    flash('The protected system portfolio already has full Administrator access and cannot be reassigned.', 'info')
                elif is_administrator_plan(plan):
                    flash('Administrator is an internal-only system plan and cannot be assigned to normal tenants.', 'warning')
                else:
                    ok, message = force_activate_subscription(profile, plan, actor=current_user.username)
                    flash(message or f'Subscription activated for {profile.tenant_slug}.', 'success' if ok else 'warning')
            else:
                flash('Tenant not found.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        elif request.form.get('action') == 'sync_paymongo':
            profile = profile_repository.get(request.form.get('tenant_id'))
            if profile:
                ok, message = sync_subscription_from_paymongo(profile)
                flash(message, 'success' if ok else 'warning')
            else:
                flash('Tenant not found.', 'danger')
            return redirect(url_for('superadmin.subscription_settings'))

        elif request.form.get('action') == 'reset_subscription':
            profile = profile_repository.get(request.form.get('tenant_id'))
            if profile:
                if has_administrator_access(profile):
                    ensure_default_tenant_administrator_plan(commit=True)
                    flash('The protected system portfolio cannot be reset, downgraded, or expired.', 'info')
                else:
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

    # Gather subscription data for all tenants.  The protected system portfolio
    # is displayed as an internal Administrator plan, independent of old Basic
    # subscription rows that may still exist from previous seeds.
    tenants_data = []
    public_plans = get_public_billing_plans()
    for profile in profile_repository.query.order_by(Profile.tenant_slug).all():
        if has_administrator_access(profile):
            tenants_data.append({
                'id': profile.id,
                'tenant_slug': profile.tenant_slug,
                'name': profile.name,
                'plan': ADMINISTRATOR_PLAN_NAME,
                'plan_price_label': 'Internal only',
                'status': 'active',
                'started_at': None,
                'expires_at': None,
                'payment_method': 'system-protected',
                'amount_paid': 0.0,
                'amount_paid_label': '$0.00',
                'billing_cycle': None,
                'is_system_plan': True,
            })
            continue

        subscription = profile.current_subscription()
        plan_name = subscription.plan if subscription else profile.plan or 'Basic'
        plan_price_label = 'Free trial' if normalize_plan_name(plan_name) == 'Trial' else public_plans.get(plan_name, {}).get('price_label', '')
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
            'amount_paid_label': format_money(subscription.amount_paid if subscription else 0.0),
            'billing_cycle': subscription.billing_cycle if subscription else 'monthly',
            'is_system_plan': False,
        })

    return render_template(
        'superadmin/subscription_settings.html',
        tenants=tenants_data,
        billing_plans=public_plans,
        system_billing_plans=get_system_billing_plans(),
        trial_limits=get_trial_limits(),
        trial_meta=trial_field_metadata(),
        plan_limits=get_all_plan_limits(),
        plan_limit_meta=plan_field_metadata(),
        page_title='Subscription Settings',
        currency=currency_context(),
        supported_currencies=SUPPORTED_CURRENCIES,
        base_currency=BASE_CURRENCY,
        focused_tenant=request.args.get('tenant', type=int),
    )

@superadmin.route('/subscriptions/tenant/<int:profile_id>')
@superadmin_required
def subscription_manage(profile_id):
    profile = profile_repository.get(profile_id)
    if not profile:
        flash('Tenant profile not found.', 'danger')
        return redirect(url_for('superadmin.subscription_settings'))
    if has_administrator_access(profile):
        flash('The protected Administrator portfolio has permanent full access.', 'info')
        return redirect(url_for('superadmin.subscription_settings'))
    subscription = profile.current_subscription()
    return render_template(
        'superadmin/subscription_manage.html',
        profile=profile,
        subscription=subscription,
        billing_plans=_manageable_subscription_plans(),
        currency=currency_context(),
        page_title=f'Manage Subscription — {profile.tenant_slug}',
    )


@superadmin.route('/licenses', methods=['GET', 'POST'])
@superadmin_required
def licenses():
    """Deprecated — redirect to automated billing dashboard."""
    flash('License key management has been replaced by PayMongo automated billing.', 'info')
    return redirect(url_for('superadmin.billing_overview'))

def _generate_license_key(plan: str, slug: str) -> str:
    """Delegate to shared utils.generate_license_key."""
    return _utils_generate_license_key(plan, slug)

def _license_plan_details(plan: str) -> dict:
    if is_administrator_plan(plan):
        return get_system_billing_plans()[ADMINISTRATOR_PLAN_NAME]
    public_plans = get_public_billing_plans()
    return public_plans.get(normalize_plan_name(plan), public_plans['Basic'])

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
