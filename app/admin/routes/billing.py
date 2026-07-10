"""
app/admin/routes/billing.py — Billing index, plans, payment, history (Phase 4b, batch 2)

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
from app.utils import BILLING_PLANS, get_public_billing_plans, is_paymongo_enabled, log_activity
from app.models.portfolio import Subscription
from app.system_plan import ADMINISTRATOR_PLAN, has_administrator_access, is_administrator_plan
from app.services.billing import subscription_access_status
from app.services.billing.currency import get_currency_settings
from app.services.billing.trial_history import ensure_profile_trial_history
from app.services.billing_handlers import (
    billing_payment_context,
    country_payment_quote_payload,
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


from app.admin.blueprint import (admin, admin_required, _active_tenant_slug,
                                 _require_tenant_object, _load_tenant_profile,
                                 _tenant_slug_filter, _get_client_ip, _DEFAULT_TENANT_SLUG)

logger = logging.getLogger(__name__)


def _billing_access_check(tenant: str) -> Optional[Response]:
    """
    Return a redirect Response if the current user cannot access billing for
    `tenant`, or None if access is permitted.

    Permits:
      • Superadmin: always
      • Admin whose tenant_slug matches: always
      • Admin for 'default' whose tenant_slug is 'default': always
    """
    if current_user.is_superadmin:
        return None
    user_tenant = getattr(current_user, 'tenant_slug', None) or _DEFAULT_TENANT_SLUG
    if user_tenant == tenant:
        return None
    flash('You do not have access to billing for this tenant.', 'danger')
    logger.warning(
        'TENANT: user id=%s (tenant=%r) attempted to access billing '
        'for tenant=%r — blocked.',
        current_user.id, user_tenant, tenant,
    )
    return redirect(url_for('admin.dashboard'))

@admin.route('/billing')
@admin_required
def billing_index():
    tenant  = _active_tenant_slug()
    profile = _load_tenant_profile()
    if profile is None:
        flash('Tenant profile not found.', 'danger')
        return redirect(url_for('admin.dashboard'))
    denied = _billing_access_check(tenant)
    if denied:
        return denied

    is_admin_plan = has_administrator_access(profile)
    subscription = None if is_admin_plan else profile.current_subscription()
    return render_template(
        'admin/billing_overview.html',
        profile=profile,
        subscription=subscription,
        subscription_status=subscription_access_status(profile),
        license_status=profile.license_status(),
        trial_days_left=profile.trial_days_remaining(),
        plans=get_public_billing_plans(),
        is_administrator_plan=is_admin_plan,
        administrator_plan=ADMINISTRATOR_PLAN,
        tenant_slug=tenant,
        paymongo_enabled=(is_paymongo_enabled() and get_currency_settings().get('display_currency') == 'PHP'),
        billing_routes={
            'overview': 'admin.billing_index',
            'plans':    'admin.billing_plans',
            'history':  'admin.billing_history',
        },
    )

@admin.route('/billing/plans', methods=['GET', 'POST'])
@admin_required
def billing_plans():
    tenant  = _active_tenant_slug()
    profile = _load_tenant_profile()
    if profile is None:
        flash('Tenant profile not found.', 'danger')
        return redirect(url_for('admin.dashboard'))
    denied = _billing_access_check(tenant)
    if denied:
        return denied

    is_admin_plan = has_administrator_access(profile)
    subscription = None if is_admin_plan else profile.current_subscription()
    form = PlanSelectionForm(
        plan='Basic' if is_admin_plan else normalize_plan_name(subscription.plan if subscription else profile.plan or 'Basic')
    )

    status = request.args.get('status')
    if status == 'success':
        flash('Payment received! Your subscription will activate shortly.', 'success')
    elif status == 'failed':
        flash('Payment failed. Please try again.', 'danger')
    elif status == 'cancelled':
        flash('Payment cancelled. You can try again anytime.', 'warning')

    billing_routes = {
        'overview': 'admin.billing_index',
        'plans':    'admin.billing_plans',
        'history':  'admin.billing_history',
        'payment':  'admin.billing_payment',
    }
    paymongo_enabled = is_paymongo_enabled() and get_currency_settings().get('display_currency') == 'PHP'

    if form.validate_on_submit() or request.method == 'POST':
        response, exc = handle_billing_plans_post(
            profile,
            tenant_slug=tenant,
            billing_routes=billing_routes,
            paymongo_enabled=paymongo_enabled,
            return_endpoint='admin.billing_plans',
            payment_route_builder=lambda mid, **kw: url_for(
                'admin.billing_payment',
                method_id=mid,
                billing_cycle=kw.get('billing_cycle', 'monthly'),
            ),
        )
        if response is not None:
            return response
        if exc is not None:
            logger.exception('billing_plans update failed: %s', exc)

    ctx = billing_plans_context(
        profile,
        tenant_slug=tenant,
        billing_routes=billing_routes,
        paymongo_enabled=paymongo_enabled,
    )
    return render_template('admin/billing_plans.html', **ctx)

@admin.route('/billing/payment/<int:method_id>', methods=['GET', 'POST'])
@admin_required
def billing_payment(method_id):
    tenant  = _active_tenant_slug()
    profile = _load_tenant_profile()
    if profile is None:
        flash('Tenant profile not found.', 'danger')
        return redirect(url_for('admin.dashboard'))
    denied = _billing_access_check(tenant)
    if denied:
        return denied

    if has_administrator_access(profile):
        flash('Administrator plan has full access and does not require payment.', 'info')
        return redirect(url_for('admin.billing_index'))

    method = get_payment_method_for_tenant(method_id, profile.tenant_id)
    if not method:
        flash('Payment method not found or inactive.', 'danger')
        return redirect(url_for('admin.billing_plans'))

    billing_cycle = request.args.get('billing_cycle', 'monthly')
    # Accept plan from query string (set by the JS link on billing_plans page)
    selected_plan = normalize_plan_name(
        request.args.get('plan')
        or (profile.current_subscription().plan if profile.current_subscription() else None)
        or profile.plan
        or 'Basic'
    )

    # If a plan was passed in the URL, persist it to a pending subscription
    if request.args.get('plan'):
        try:
            from app.services.manual_billing import get_or_create_pending_subscription
            get_or_create_pending_subscription(db.session, profile.tenant_id, selected_plan, billing_cycle=billing_cycle)
            db.session.commit()
            if hasattr(profile, '_current_subscription_cache'):
                del profile._current_subscription_cache
        except Exception as exc:
            db.session.rollback()
            logger.warning('billing_payment: failed to persist plan selection: %s', exc)

    billing_routes = {
        'overview': 'admin.billing_index',
        'plans':    'admin.billing_plans',
        'history':  'admin.billing_history',
        'payment':  'admin.billing_payment',
    }

    if request.method == 'POST':
        response = handle_billing_payment_post(
            profile, method,
            billing_cycle=billing_cycle,
            success_redirect=url_for('admin.billing_index'),
        )
        if response is not None:
            return response

    ctx = billing_payment_context(
        profile, method,
        tenant_slug=tenant,
        billing_routes=billing_routes,
        billing_cycle=billing_cycle,
    )
    ctx['quote_url'] = url_for('admin.billing_payment_quote', method_id=method_id)
    return render_template('admin/billing_payment.html', **ctx)


@admin.route('/billing/payment/<int:method_id>/quote')
@admin_required
def billing_payment_quote(method_id):
    tenant = _active_tenant_slug()
    profile = _load_tenant_profile()
    if profile is None:
        return jsonify({'ok': False, 'error': 'Tenant profile not found.'}), 404
    denied = _billing_access_check(tenant)
    if denied:
        return jsonify({'ok': False, 'error': 'Access denied.'}), 403
    method = get_payment_method_for_tenant(method_id, profile.tenant_id)
    if not method:
        return jsonify({'ok': False, 'error': 'Payment method unavailable.'}), 404
    payload = country_payment_quote_payload(
        profile,
        billing_cycle=request.args.get('billing_cycle', 'monthly'),
        country_code=request.args.get('country'),
    )
    return jsonify(payload), (200 if payload.get('ok') else 503)


@admin.route('/billing/history')
@admin_required
def billing_history():
    tenant  = _active_tenant_slug()
    profile = _load_tenant_profile()
    if profile is None:
        flash('Tenant profile not found.', 'danger')
        return redirect(url_for('admin.dashboard'))
    denied = _billing_access_check(tenant)
    if denied:
        return denied

    ensure_profile_trial_history(profile, commit=True)

    subscriptions = (
        subscription_repository.query
        .filter_by(tenant_id=profile.tenant_id)
        .order_by(Subscription.created_at.desc())
        .all()
    )
    return render_template(
        'admin/billing_history.html',
        profile=profile,
        subscriptions=subscriptions,
        tenant_slug=tenant,
        billing_routes={
            'overview': 'admin.billing_index',
            'plans':    'admin.billing_plans',
            'history':  'admin.billing_history',
        },
    )
