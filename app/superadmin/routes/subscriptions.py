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


from app.superadmin.blueprint import superadmin, superadmin_required, _normalize_timestamp

logger = logging.getLogger(__name__)


@superadmin.route('/subscriptions', methods=['GET', 'POST'])
@superadmin_required
def subscription_settings():
    """Superadmin subscription settings: manage subscription plans and tenant subscriptions."""
    from app.models.portfolio import Profile

    if request.method == 'POST':
        if request.form.get('action') == 'update_plans':
            # Update subscription plans
            for plan_key, plan_data in get_public_billing_plans().items():
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
                plan_data['price_monthly'] = price    # update monthly price
                # Recalc yearly using the live yearly-discount rate for this plan
                # (per-plan override if the superadmin has set one, else the
                # global rate) instead of the stale hardcoded constant — keeps
                # this in sync with the Discounts & Promotions page.
                plan_data['price_yearly'] = round(price * 12 * get_yearly_discount(plan_key), 2)
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
                'is_system_plan': True,
            })
            continue

        subscription = profile.current_subscription()
        plan_name = subscription.plan if subscription else profile.plan or 'Basic'
        plan_price_label = public_plans.get(plan_name, {}).get('price_label', '')
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
            'is_system_plan': False,
        })

    return render_template(
        'superadmin/subscription_settings.html',
        tenants=tenants_data,
        billing_plans=public_plans,
        system_billing_plans=get_system_billing_plans(),
        page_title='Subscription Settings',
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
