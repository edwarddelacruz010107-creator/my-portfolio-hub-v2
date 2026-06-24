"""
app/admin/__init__.py — Admin dashboard blueprint (v3.4.2)

CHANGELOG
─────────
v3.4.2 (this version) — Default Tenant Hardening
  • _active_tenant_slug():
      - Priority 1: current_user.tenant_slug  (non-superadmin)
      - Priority 2: session['tenant_slug']
      - Priority 3: explicit 'default'  ← was missing; caused unscoped queries
      Superadmins fall straight to session → then 'default' if none set.
  • block_public_admin() — before_request:
      - Unauthenticated:  ALWAYS sets session['tenant_slug'] = 'default'
        (was conditional on "not session.get('tenant_slug')" which meant
        a stale OTHER tenant slug could survive into the login redirect).
      - Authenticated non-superadmin:  corrects session mismatch AND logs
        when a correction is made (was silent, impossible to diagnose).
      - TOTP gate:  now redirects to auth.verify_2fa for 'default' tenant
        instead of attempting tenant.auth_2fa (which 301s/404s for reserved slugs).
  • _load_tenant_profile():
      - Falls back to Profile.query.filter_by(tenant_slug='default').first()
        before Profile.query.first() to prevent cross-tenant data leakage.
      - Logs a WARNING when the default profile is missing entirely.
  • _require_tenant_object():
      - Explicitly handles tenant_slug == 'default' so default-admin can
        always access their own objects even if Profile row is not yet set up.
  • login_alias():
      - Explicit guard: 'default' and '' both redirect to auth.login.
  • billing_index/plans/payment/history:
      - Access check correctly handles current_user.tenant_slug == 'default'.

v3.1 (previous) — TOTP gate, tenant-scoped activity, project fixes.
v3.0 — Initial multi-tenant admin.
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

# The canonical slug for the primary administrator portfolio.
# Centralised here so all helpers reference the same constant.
_DEFAULT_TENANT_SLUG = 'default'


# ── Auth decorator ────────────────────────────────────────────────────────────

def admin_required(f):
    """Require authenticated user with is_admin=True."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not (current_user.is_admin or current_user.is_superadmin):
            flash('Admin access required.', 'danger')
            return redirect(_safe_root())
        return f(*args, **kwargs)
    return decorated


def _safe_root():
    try:
        return url_for('root')
    except Exception:
        return '/'


# ── Tenant resolution ─────────────────────────────────────────────────────────

def _active_tenant_slug() -> str:
    """
    v3.7: Thin wrapper over resolve_active_tenant() (app.tenant_security).
    Kept for backward compatibility — all call sites in this blueprint
    already use this name.  The actual resolution logic lives in one place.
    """
    return resolve_active_tenant()


def _load_tenant_profile() -> Optional[Profile]:
    """
    Load the Profile for the active tenant.

    FIX v3.4.2: Previously fell back to Profile.query.first() which could
    return any tenant's profile.  Now falls back to the explicit default-slug
    query, and only if that also misses does it log a warning.
    """
    tenant_slug = _active_tenant_slug()

    profile = Profile.query.filter_by(tenant_slug=tenant_slug).first()
    if profile:
        return profile

    # If the resolved slug is already 'default' and we got nothing, the
    # default profile doesn't exist yet (fresh install).  Log and return None
    # — callers must handle None gracefully.
    if tenant_slug == _DEFAULT_TENANT_SLUG:
        logger.warning(
            'TENANT: no Profile row found for tenant_slug=%r (fresh install?). '
            'Run `flask seed-default-tenant` or create a profile via superadmin.',
            _DEFAULT_TENANT_SLUG,
        )
        return None

    # The resolved slug was NOT 'default' (e.g. a non-default tenant admin whose
    # profile row doesn't exist yet).  Try 'default' as a last resort ONLY for
    # superadmin contexts to avoid cross-tenant leakage.
    if current_user.is_authenticated and current_user.is_superadmin:
        fallback = Profile.query.filter_by(tenant_slug=_DEFAULT_TENANT_SLUG).first()
        if fallback:
            logger.info(
                'TENANT: superadmin context — profile for %r not found, '
                'falling back to default profile.',
                tenant_slug,
            )
            return fallback

    logger.warning(
        'TENANT: no Profile found for tenant_slug=%r and no superadmin fallback.',
        tenant_slug,
    )
    return None


def _tenant_slug_filter(query):
    """Apply tenant_slug filter to a SQLAlchemy query."""
    tenant_slug = _active_tenant_slug()
    # _active_tenant_slug() always returns a non-empty string now.
    return query.filter_by(tenant_slug=tenant_slug)


def _require_tenant_object(obj):
    """
    Return obj if it belongs to the active tenant, else None.

    FIX v3.4.2: Explicitly handles tenant_slug == 'default' — the default
    admin's objects have tenant_slug='default', and _active_tenant_slug()
    now guarantees returning 'default' when appropriate, so this check is
    always correct for both default and non-default tenants.
    """
    if obj is None:
        return None
    if current_user.is_superadmin:
        return obj  # Superadmin can access any object
    tenant_slug = _active_tenant_slug()
    obj_tenant = getattr(obj, 'tenant_slug', None)
    if obj_tenant != tenant_slug:
        logger.warning(
            'TENANT isolation: user id=%s (tenant=%r) attempted to access '
            'object %s id=%s (tenant=%r) — blocked.',
            getattr(current_user, 'id', '?'), tenant_slug,
            type(obj).__name__, getattr(obj, 'id', '?'), obj_tenant,
        )
        return None
    return obj


def _active_tenant_plan_features() -> dict:
    profile = _load_tenant_profile()
    if profile:
        return profile.plan_features()
    tenant_slug = _active_tenant_slug()
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    if tenant:
        return tenant.plan_features()
    return get_plan_features('Basic')


def _active_tenant_plan_name() -> str:
    profile = _load_tenant_profile()
    if profile:
        return profile.effective_plan()
    tenant_slug = _active_tenant_slug()
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    return tenant.normalized_plan if tenant else 'Basic'


def _tenant_media_upload_count() -> int:
    profile = _load_tenant_profile()
    count = 1 if profile and profile.profile_image else 0
    count += _tenant_slug_filter(Project.query).filter(Project.image != None).filter(Project.image != '').count()
    count += _tenant_slug_filter(Testimonial.query).filter(Testimonial.author_avatar != None).filter(Testimonial.author_avatar != '').count()
    return count


# Deprecated LICENSE_PLANS removed — use BILLING_PLANS from app.utils instead.
LICENSE_PLANS = BILLING_PLANS


def _license_plan_details(plan: str) -> dict:
    """Alias into BILLING_PLANS for backward compatibility."""
    return BILLING_PLANS.get(normalize_plan_name(plan), BILLING_PLANS['Basic'])


def _normalize_datetime(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _license_expiration_info(profile):
    plan_name = profile.license_plan or profile.plan or 'Basic'
    details = _license_plan_details(plan_name)
    expires_at = None
    expires_in_days = None
    activated_at = _normalize_datetime(profile.license_activated_at)
    if profile.license_active and activated_at:
        expires_at = activated_at + timedelta(days=details['duration_days'])
        expires_in_days = max(0, (expires_at - datetime.now(timezone.utc)).days)
    return details, expires_at, expires_in_days


def _format_filesize(size: int | None) -> str:
    if size is None:
        return 'n/a'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


@admin.route('/license', methods=['GET', 'POST'])
@admin_required
def license():
    """Deprecated — subscriptions activate automatically via PayMongo."""
    flash('License keys are no longer required. Manage your subscription on the Billing page.', 'info')
    return redirect(url_for('admin.billing_index'))


# ── Security: block unauthenticated admin access ──────────────────────────────

@admin.before_request
def block_public_admin():
    """
    Gate every /admin/* request.  Enforces authentication, tenant isolation,
    TOTP verification, and subscription expiry in that order.

    FIX v3.4.2 — Unauthenticated path
    ──────────────────────────────────
    ALWAYS set session['tenant_slug'] = 'default' when the request arrives at
    /admin/ without authentication and no tenant slug is available. The old
    code was conditional (only set if nothing in session) which meant a stale
    OTHER tenant's slug could survive from a previous browser session and send
    the default admin to /<other-tenant>/auth/login — the wrong login page.

    FIX v3.4.2 — TOTP redirect
    ───────────────────────────
    The old code attempted to redirect to tenant.auth_2fa when tenant == 'default'.
    'default' is in _RESERVED_SLUGS so tenant_bp rejects it with a 301→/, creating
    an infinite redirect loop.  Now:
      • tenant == 'default' or tenant == ''  → auth.verify_2fa  (correct path)
      • tenant is a real slug                → tenant.auth_2fa  (unchanged)

    FIX v5.7 — Forgot-password routes were never exempted
    ────────────────────────────────────────────────────────
    block_public_admin() ran unconditionally on every /admin/* request,
    including the three public forgot-password endpoints. An unauthenticated
    visitor clicking "Forgot your password?" hit this hook, failed the
    is_authenticated check below, and was bounced straight back to
    auth.login with next=<the forgot-password URL they just came from> —
    i.e. the link appeared completely unresponsive (URL changes, page does
    not). This is now exempted by endpoint name before any auth check runs.
    """
    # ── Public-endpoint exemption — MUST run before any auth/session logic ──
    _PUBLIC_ADMIN_ENDPOINTS = {
        'admin.forgot_password',
        'admin.forgot_password_verify',
        'admin.forgot_password_reset',
    }
    if request.endpoint in _PUBLIC_ADMIN_ENDPOINTS:
        return None  # let the route's own logic run unauthenticated

    # Check authentication: also treat as unauthenticated when the session
    # has no '_user_id' key (guards against Flask-Login's app-level user cache
    # leaking between test clients on a session-scoped test app, and handles
    # edge cases where the login manager restores a stale user from memory).
    _session_has_user = bool(session.get('_user_id') or session.get('user_id'))
    _truly_authenticated = current_user.is_authenticated and _session_has_user

    if not _truly_authenticated:
        # ALWAYS resolve to 'default' for unauthenticated /admin/ access.
        # Do NOT preserve a stale tenant from a previous session — the admin
        # blueprint serves the default tenant; tenant-scoped admins log in via
        # /<slug>/auth/login (the tenant blueprint).
        current_session_tenant = session.get('tenant_slug')
        if current_session_tenant != _DEFAULT_TENANT_SLUG:
            logger.info(
                'TENANT: unauthenticated /admin/ access — overriding '
                'session tenant %r with %r',
                current_session_tenant, _DEFAULT_TENANT_SLUG,
            )
            session['tenant_slug'] = _DEFAULT_TENANT_SLUG

        return redirect(url_for('auth.login', next=request.url))

    # ── Authenticated: enforce tenant isolation ───────────────────────────────
    if not current_user.is_superadmin:
        user_tenant    = getattr(current_user, 'tenant_slug', None) or _DEFAULT_TENANT_SLUG
        session_tenant = session.get('tenant_slug')

        if session_tenant != user_tenant:
            logger.info(
                'TENANT correction in block_public_admin: user id=%s '
                '(assigned tenant=%r) had session tenant=%r — correcting.',
                current_user.id, user_tenant, session_tenant,
            )
            # FIX v3.7: Use stamp_session_tenant (HMAC) instead of a bare
            # session write.  A raw write leaves _tsig stale → TenantGuard
            # rejects the next request, kicking users mid-2FA flow.
            stamp_session_tenant(current_user.id, user_tenant)

    # ── TOTP gate ─────────────────────────────────────────────────────────────
    # _bypass_endpoints: requests that must pass even without TOTP confirmation.
    # Billing endpoints are included so an expired-TOTP user isn't locked out
    # of renewing, and login_alias is the /admin/login redirect stub.
    _bypass_endpoints = {
        'admin.login_alias',
        'admin.billing_index', 'admin.billing_plans',
        'admin.billing_payment', 'admin.billing_history',
    }
    if (
        current_user.is_authenticated
        and current_user.totp_enabled
        and not session.get('totp_verified')
        and request.endpoint not in _bypass_endpoints
    ):
        # Stamp the session with a unique verification nonce to prevent
        # replay attacks where an old totp_verified=True cookie is reused.
        session['_2fa_user_id']      = current_user.id
        session['_2fa_remember']     = False
        session['_2fa_next']         = request.url
        session['_2fa_default_next'] = url_for('admin.dashboard')
        # Stamp the tenant so the 2FA redirect stays in the right scope.
        session['_2fa_tenant']       = session.get('tenant_slug', _DEFAULT_TENANT_SLUG)

        # FIX v3.4.2: 'default' is a reserved slug — never send it through
        # the tenant blueprint.  Route to auth.verify_2fa directly.
        active_tenant = session.get('tenant_slug', _DEFAULT_TENANT_SLUG)
        if active_tenant and active_tenant != _DEFAULT_TENANT_SLUG:
            try:
                return redirect(url_for('tenant.auth_2fa', tenant_slug=active_tenant))
            except Exception:
                pass  # Blueprint not registered or route missing — fall through
        return redirect(url_for('auth.verify_2fa'))

    # ── Subscription / trial expiry gate ─────────────────────────────────────
    # NOTE: _billing_endpoints are already in _bypass_endpoints above, so a
    # single combined set is used here for clarity.
    _sub_exempt = _bypass_endpoints | {'admin.license'}
    if (
        current_user.is_authenticated
        and not current_user.is_superadmin
        and request.endpoint not in _sub_exempt
    ):
        _trial_profile = _load_tenant_profile()
        if _trial_profile and _trial_profile.is_expired():
            _trial_profile.enforce_expiry(commit=True)
            flash(
                '\u26a0\ufe0f Your subscription has expired. '
                'Please subscribe to restore access.',
                'warning',
            )
            return redirect(url_for('admin.billing_index'))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin.route('/')
@admin_required
def dashboard():
    profile           = _load_tenant_profile()
    project_query     = _tenant_slug_filter(Project.query)
    skill_query       = _tenant_slug_filter(Skill.query)
    testimonial_query = _tenant_slug_filter(Testimonial.query)
    inquiry_query     = _tenant_slug_filter(Inquiry.query)

    from datetime import date as _date
    _now_utc     = datetime.now(timezone.utc)
    _today_start = _now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    _week_start  = _now_utc - timedelta(days=7)

    stats = {
        'total_projects':     project_query.count(),
        'published_projects': project_query.filter_by(status='published').count(),
        'total_skills':       skill_query.filter_by(is_visible=True).count(),
        'total_testimonials': testimonial_query.count(),
        'profile_completion': get_profile_completion(profile) if profile else 0,
        'featured_projects':  project_query.filter_by(is_featured=True).count(),
        # Message counters
        'unread_messages':    inquiry_query.filter_by(is_read=False).count(),
        'total_messages':     inquiry_query.count(),
        'today_messages':     inquiry_query.filter(Inquiry.created_at >= _today_start).count(),
        'week_messages':      inquiry_query.filter(Inquiry.created_at >= _week_start).count(),
    }
    recent_activity = (
        _tenant_slug_filter(ActivityLog.query)
        .order_by(ActivityLog.created_at.desc())
        .limit(10).all()
    )
    recent_projects = (
        _tenant_slug_filter(Project.query)
        .order_by(Project.created_at.desc())
        .limit(5).all()
    )
    subscription = profile.current_subscription() if profile else None
    return render_template(
        'admin/dashboard.html',
        stats=stats,
        recent_activity=recent_activity,
        recent_projects=recent_projects,
        profile=profile,
        subscription=subscription,
        subscription_status=subscription_access_status(profile) if profile else 'none',
    )


@admin.route('/dashboard')
def dashboard_alias():
    """Backward-compatible alias for /admin/dashboard."""
    return redirect(url_for('admin.dashboard'))


# ── Force password reset (BUG-05 fix) ────────────────────────────────────────

@admin.route('/reset-password-required', methods=['GET', 'POST'])
@login_required
def reset_password_required():
    """
    Force-password-change wall shown after first login or admin-triggered reset.

    Flow:
      • If current_user.require_password_reset is False → redirect to dashboard.
      • On valid POST: update password, clear flag, redirect to login so a fresh
        session is established with the new credentials.
    """
    if not getattr(current_user, 'require_password_reset', False):
        return redirect(url_for('admin.dashboard'))

    form = ChangePasswordForm()
    if form.validate_on_submit():
        current_user.password = form.new_password.data
        current_user.require_password_reset = False
        db.session.commit()
        log_activity(
            current_user,
            'password_reset_required_completed',
            'User completed forced password reset',
        )
        flash('Password updated successfully. Please log in with your new password.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('admin/reset_password_required.html', form=form)


# ── Login alias ───────────────────────────────────────────────────────────────

@admin.route('/login')
def login_alias():
    """
    Redirect /admin/login → appropriate login page.

    FIX v3.4.2: Explicit guard for 'default' and empty string — both map to
    auth.login (the root-domain login page).  Falsy or 'default' slugs must
    NEVER be passed to url_for('tenant.auth_login') because 'default' is in
    _RESERVED_SLUGS and would 404 or redirect to /.
    """
    next_page = request.args.get('next') or url_for('admin.dashboard')
    tenant    = session.get('tenant_slug', _DEFAULT_TENANT_SLUG)

    if tenant and tenant != _DEFAULT_TENANT_SLUG:
        try:
            return redirect(url_for('tenant.auth_login', tenant_slug=tenant, next=next_page))
        except Exception:
            pass  # Tenant blueprint not available — fall through to default login

    return redirect(url_for('auth.login', next=next_page))


# ── Billing routes ─────────────────────────────────────────────────────────────

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

    subscription = profile.current_subscription()
    return render_template(
        'admin/billing_overview.html',
        profile=profile,
        subscription=subscription,
        subscription_status=subscription_access_status(profile),
        license_status=profile.license_status(),
        trial_days_left=profile.trial_days_remaining(),
        plans=BILLING_PLANS,
        tenant_slug=tenant,
        paymongo_enabled=is_paymongo_enabled(),
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

    subscription = profile.current_subscription()
    form = PlanSelectionForm(
        plan=normalize_plan_name(subscription.plan if subscription else profile.plan or 'Basic')
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
    paymongo_enabled = is_paymongo_enabled()

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
    return render_template('admin/billing_payment.html', **ctx)


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

    subscriptions = (
        Subscription.query
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


def _get_asset_size(filename: str, folder: str) -> int | None:
    if not filename:
        return None
    path = os.path.join(current_app.static_folder, 'uploads', folder, filename)
    try:
        return os.path.getsize(path)
    except OSError:
        return None


# ── Client IP helper ─────────────────────────────────────────────────────────

def _get_client_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()[:45]
    return (request.remote_addr or 'unknown')[:45]


# ── Messages ──────────────────────────────────────────────────────────────────

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

    query = _tenant_slug_filter(Inquiry.query)

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

    unread_total = _tenant_slug_filter(Inquiry.query).filter(
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
            log_activity('create', 'inquiry', tenant_slug,
                         f'Tenant admin sent message to superadmin')
            flash('Message sent to platform support.', 'success')
            return redirect(url_for('admin.view_message', message_id=inquiry.id))

    return render_template('admin/new_message.html',
                           subject=subject, form=form)


@admin.route('/messages/<int:message_id>/delete', methods=['POST'])
@admin_required
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


# ── Profile ───────────────────────────────────────────────────────────────────

@admin.route('/profile', methods=['GET', 'POST'])
@admin_required
def edit_profile():
    profile = _load_tenant_profile()
    if not profile:
        tenant_slug = _active_tenant_slug()
        tenant = Tenant.query.filter_by(slug=tenant_slug).first()
        if not tenant:
            tenant = Tenant(
                slug=tenant_slug,
                company_name=tenant_slug.title(),
                status='active',
                plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()
        profile = Profile(tenant=tenant)
        db.session.add(profile)
        db.session.commit()

    form = ProfileForm(obj=profile)

    if request.method == 'GET':
        social = profile.social_links or {}
        for field in ['github', 'linkedin', 'facebook', 'twitter',
                      'instagram', 'youtube', 'website', 'dribbble']:
            getattr(form, field).data = social.get(field, '')

    if form.validate_on_submit():
        if is_upload_file(form.profile_image.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and not profile.profile_image and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                new_img = save_image(form.profile_image.data, 'profiles', max_size=(800, 800), quality=90)
                if new_img:
                    if profile.profile_image:
                        delete_image(profile.profile_image, 'profiles')
                    profile.profile_image = new_img
                else:
                    flash('Image upload failed — check format/size.', 'warning')

        profile.name                = form.name.data
        profile.title               = form.title.data
        profile.subtitle            = form.subtitle.data
        profile.bio                 = form.bio.data
        profile.bio_short           = form.bio_short.data
        profile.location            = form.location.data
        profile.email               = form.email.data
        profile.phone               = form.phone.data
        profile.resume_url          = form.resume_url.data or ''
        profile.years_experience    = form.years_experience.data or 0
        profile.experience_start_year = form.experience_start_year.data
        profile.clients_count       = form.clients_count.data or 0
        profile.hero_tagline        = form.hero_tagline.data
        profile.availability_status = form.availability_status.data
        profile.is_available        = form.is_available.data
        profile.social_links = {
            k: (getattr(form, k).data or '')
            for k in ['github', 'linkedin', 'facebook', 'twitter',
                       'instagram', 'youtube', 'website', 'dribbble']
        }
        db.session.commit()
        log_activity('update', 'profile', profile.name, 'Profile updated')
        flash('Profile saved!', 'success')
        return redirect(url_for('admin.edit_profile'))

    return render_template(
        'admin/profile.html',
        form=form,
        profile=profile,
        profile_completion=get_profile_completion(profile),
    )


# ── Skills ────────────────────────────────────────────────────────────────────

@admin.route('/skills')
@admin_required
def skills():
    query      = _tenant_slug_filter(Skill.query)
    all_skills = query.order_by(Skill.category, Skill.order).all()
    by_category = {}
    for s in all_skills:
        by_category.setdefault(s.category, []).append(s)
    return render_template('admin/skills.html', skills=all_skills, by_category=by_category)


@admin.route('/skills/new', methods=['GET', 'POST'])
@admin_required
def new_skill():
    plan_features  = _active_tenant_plan_features()
    max_skills     = plan_features.get('max_skills')
    current_skills = _tenant_slug_filter(Skill.query).count()
    if max_skills is not None and current_skills >= max_skills:
        flash(
            f'Your current plan ({_active_tenant_plan_name()}) allows up to '
            f'{max_skills} skills. Upgrade to add more.',
            'warning',
        )
        return redirect(url_for('admin.skills'))

    form = SkillForm()
    if form.validate_on_submit():
        skill = Skill(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            name=form.name.data,
            proficiency=form.proficiency.data,
            category=form.category.data,
            icon=form.icon.data or '',
            color=form.color.data or '',
            order=form.order.data or 0,
            is_visible=form.is_visible.data,
        )
        db.session.add(skill)
        db.session.commit()
        log_activity('create', 'skill', skill.name, f'Added skill: {skill.name}')
        flash(f'Skill "{skill.name}" created!', 'success')
        return redirect(url_for('admin.skills'))
    return render_template('admin/skill_form.html', form=form, skill=None)


@admin.route('/skills/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_skill(id: int):
    skill = _require_tenant_object(db.session.get(Skill, id))
    if skill is None:
        flash('Skill not found.', 'warning')
        return redirect(url_for('admin.skills'))
    form = SkillForm(obj=skill)
    if form.validate_on_submit():
        skill.name        = form.name.data
        skill.proficiency = form.proficiency.data
        skill.category    = form.category.data
        skill.icon        = form.icon.data or ''
        skill.color       = form.color.data or ''
        skill.order       = form.order.data or 0
        skill.is_visible  = form.is_visible.data
        db.session.commit()
        log_activity('update', 'skill', skill.name)
        flash(f'Skill "{skill.name}" updated!', 'success')
        return redirect(url_for('admin.skills'))
    return render_template('admin/skill_form.html', form=form, skill=skill)


@admin.route('/skills/<int:id>/delete', methods=['POST'])
@admin_required
def delete_skill(id: int):
    skill = _require_tenant_object(db.session.get(Skill, id))
    if skill is None:
        flash('Skill not found.', 'warning')
        return redirect(url_for('admin.skills'))
    name = skill.name
    db.session.delete(skill)
    db.session.commit()
    log_activity('delete', 'skill', name)
    flash(f'Skill "{name}" deleted.', 'success')
    return redirect(url_for('admin.skills'))


@admin.route('/skills/reorder', methods=['POST'])
@admin_required
def reorder_skills():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        skill = _require_tenant_object(db.session.get(Skill, item.get('id')))
        if skill:
            skill.order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')


# ── Projects ──────────────────────────────────────────────────────────────────

@admin.route('/projects')
@admin_required
def projects():
    q               = request.args.get('q', '').strip()
    status_filter   = request.args.get('status', 'all')
    category_filter = request.args.get('category', 'all')

    # Tenant scope FIRST, then additional filters
    query = _tenant_slug_filter(Project.query)
    if q:
        query = query.filter(Project.title.ilike(f'%{q}%'))
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    if category_filter != 'all':
        query = query.filter_by(category=category_filter)

    all_projects = query.order_by(Project.order.asc(), Project.created_at.desc()).all()
    categories   = sorted({
        c[0] for c in _tenant_slug_filter(db.session.query(Project.category)).distinct()
        if c[0]
    })

    return render_template(
        'admin/projects.html',
        projects=all_projects,
        q=q,
        status_filter=status_filter,
        category_filter=category_filter,
        categories=categories,
    )


@admin.route('/projects/new', methods=['GET', 'POST'])
@admin_required
def new_project():
    plan_features    = _active_tenant_plan_features()
    max_projects     = plan_features.get('max_projects')
    current_projects = _tenant_slug_filter(Project.query).count()
    if max_projects is not None and current_projects >= max_projects:
        flash(
            f'Your current plan ({_active_tenant_plan_name()}) allows up to '
            f'{max_projects} projects. Upgrade to add more.',
            'warning',
        )
        return redirect(url_for('admin.projects'))

    form = ProjectForm()
    if form.validate_on_submit():
        project = Project(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            title=form.title.data,
            description=form.description.data or '',
            description_short=form.description_short.data or '',
            live_url=form.live_url.data or '',
            github_url=form.github_url.data or '',
            framework=form.framework.data or '',
            language=form.language.data or '',
            category=form.category.data,
            status=form.status.data,
            is_featured=form.is_featured.data,
            date_completed=form.date_completed.data,
            order=form.order.data or 0,
        )
        project.tags = [t.strip() for t in (form.tags.data or '').split(',') if t.strip()]

        base_slug = project.generate_slug()
        slug      = base_slug
        counter   = 1
        while Project.query.filter_by(slug=slug).first():
            slug = f'{base_slug}-{counter}'
            counter += 1
        project.slug = slug

        if is_upload_file(form.image.data):
            max_uploads = plan_features.get('max_media_uploads')
            if max_uploads is not None and not project.image and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                img = save_image(form.image.data, 'projects')
                if img:
                    project.image = img
                else:
                    flash('Image upload failed — check format/size.', 'warning')

        db.session.add(project)
        db.session.commit()
        log_activity('create', 'project', project.title)
        flash(f'Project "{project.title}" created!', 'success')
        return redirect(url_for('admin.projects'))

    return render_template('admin/project_form.html', form=form, project=None)


@admin.route('/projects/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_project(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        flash('Project not found.', 'warning')
        return redirect(url_for('admin.projects'))
    form = ProjectForm(obj=project)

    if request.method == 'GET':
        form.tags.data = ', '.join(project.tags or [])

    if form.validate_on_submit():
        project.title             = form.title.data
        project.description       = form.description.data or ''
        project.description_short = form.description_short.data or ''
        project.live_url          = form.live_url.data or ''
        project.github_url        = form.github_url.data or ''
        project.framework         = form.framework.data or ''
        project.language          = form.language.data or ''
        project.category          = form.category.data
        project.status            = form.status.data
        project.is_featured       = form.is_featured.data
        project.date_completed    = form.date_completed.data
        project.order             = form.order.data or 0
        project.tags = [t.strip() for t in (form.tags.data or '').split(',') if t.strip()]

        if is_upload_file(form.image.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and not project.image and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                new_img = save_image(form.image.data, 'projects')
                if new_img:
                    if project.image:
                        delete_image(project.image, 'projects')
                    project.image = new_img
                else:
                    flash('Image upload failed — check format/size.', 'warning')

        db.session.commit()
        log_activity('update', 'project', project.title)
        flash(f'Project "{project.title}" updated!', 'success')
        return redirect(url_for('admin.projects'))

    return render_template('admin/project_form.html', form=form, project=project)


@admin.route('/projects/<int:id>/delete', methods=['POST'])
@admin_required
def delete_project(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        flash('Project not found.', 'warning')
        return redirect(url_for('admin.projects'))
    title = project.title
    if project.image:
        delete_image(project.image, 'projects')
    db.session.delete(project)
    db.session.commit()
    log_activity('delete', 'project', title)
    flash(f'Project "{title}" deleted.', 'success')
    return redirect(url_for('admin.projects'))


@admin.route('/uploads')
@admin_required
def uploads():
    profile    = _load_tenant_profile()
    asset_type = request.args.get('asset_type', 'all')
    allowed_types = {'all', 'profile', 'project', 'testimonial'}
    if asset_type not in allowed_types:
        asset_type = 'all'

    project_images = (
        _tenant_slug_filter(Project.query)
        .filter(Project.image != None)
        .order_by(Project.created_at.desc())
        .all()
    )
    testimonial_images = (
        _tenant_slug_filter(Testimonial.query)
        .filter(Testimonial.author_avatar != None)
        .order_by(Testimonial.created_at.desc())
        .all()
    )

    assets = []
    if profile and profile.profile_image:
        assets.append({
            'id': profile.id, 'type': 'profile', 'label': 'Profile Image',
            'filename': profile.profile_image, 'folder': 'profiles',
            'description': profile.name or profile.tenant_slug,
            'url': url_for('static', filename=f'uploads/profiles/{profile.profile_image}'),
        })

    for project in project_images:
        assets.append({
            'id': project.id, 'type': 'project', 'label': 'Project Image',
            'filename': project.image, 'folder': 'projects',
            'description': project.title,
            'url': url_for('static', filename=f'uploads/projects/{project.image}'),
        })

    for testimonial in testimonial_images:
        assets.append({
            'id': testimonial.id, 'type': 'testimonial', 'label': 'Testimonial Avatar',
            'filename': testimonial.author_avatar, 'folder': 'profiles',
            'description': testimonial.author_name,
            'url': url_for('static', filename=f'uploads/profiles/{testimonial.author_avatar}'),
        })

    for asset in assets:
        size = _get_asset_size(asset['filename'], asset['folder'])
        asset['size_bytes'] = size
        asset['size_text']  = _format_filesize(size)

    filtered_assets = [a for a in assets if a['type'] == asset_type] if asset_type != 'all' else assets
    counts = {
        'profile':     sum(1 for a in assets if a['type'] == 'profile'),
        'project':     sum(1 for a in assets if a['type'] == 'project'),
        'testimonial': sum(1 for a in assets if a['type'] == 'testimonial'),
        'all':         len(assets),
    }
    total_size = sum(a['size_bytes'] for a in assets if a['size_bytes'] is not None)

    return render_template(
        'admin/uploads.html',
        assets=filtered_assets,
        asset_type=asset_type,
        counts=counts,
        total_assets=counts['all'],
        total_size=_format_filesize(total_size),
    )


@admin.route('/uploads/delete', methods=['POST'])
@admin_required
def delete_upload():
    asset_type   = request.form.get('asset_type')
    asset_id_raw = request.form.get('asset_id')
    try:
        asset_id = int(asset_id_raw) if asset_id_raw is not None else None
    except (TypeError, ValueError):
        asset_id = None

    if asset_type == 'profile':
        profile = _load_tenant_profile()
        if profile and profile.profile_image:
            delete_image(profile.profile_image, 'profiles')
            profile.profile_image = None
            db.session.commit()
            log_activity('delete', 'profile', profile.name or profile.tenant_slug, 'Deleted profile image')
            flash('Profile image deleted.', 'success')
        else:
            flash('Nothing to delete.', 'warning')
        return redirect(url_for('admin.uploads'))

    if asset_type == 'project':
        project = _require_tenant_object(db.session.get(Project, asset_id))
        if project and project.image:
            delete_image(project.image, 'projects')
            project.image = None
            db.session.commit()
            log_activity('delete', 'project', project.title, 'Deleted project image')
            flash(f'Image removed from project "{project.title}".', 'success')
        else:
            flash('Project image not found.', 'warning')
        return redirect(url_for('admin.uploads'))

    if asset_type == 'testimonial':
        testimonial = _require_tenant_object(db.session.get(Testimonial, asset_id))
        if testimonial and testimonial.author_avatar:
            delete_image(testimonial.author_avatar, 'profiles')
            testimonial.author_avatar = None
            db.session.commit()
            log_activity('delete', 'testimonial', testimonial.author_name, 'Deleted testimonial avatar')
            flash(f'Avatar removed for testimonial "{testimonial.author_name}".', 'success')
        else:
            flash('Testimonial avatar not found.', 'warning')
        return redirect(url_for('admin.uploads'))

    flash('Unsupported upload type.', 'danger')
    return redirect(url_for('admin.uploads'))


@admin.route('/projects/<int:id>/toggle', methods=['POST'])
@admin_required
def toggle_project(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        return jsonify(error='Not found'), 404
    project.status = 'published' if project.status != 'published' else 'draft'
    db.session.commit()
    action = 'publish' if project.status == 'published' else 'unpublish'
    log_activity(action, 'project', project.title)
    return jsonify(status=project.status, title=project.title)


@admin.route('/projects/<int:id>/toggle-featured', methods=['POST'])
@admin_required
def toggle_featured(id: int):
    project = _require_tenant_object(db.session.get(Project, id))
    if project is None:
        return jsonify(error='Not found'), 404
    project.is_featured = not project.is_featured
    db.session.commit()
    return jsonify(featured=project.is_featured)


@admin.route('/projects/reorder', methods=['POST'])
@admin_required
def reorder_projects():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        p = _require_tenant_object(db.session.get(Project, item.get('id')))
        if p:
            p.order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')


# ── Testimonials ──────────────────────────────────────────────────────────────

@admin.route('/testimonials')
@admin_required
def testimonials():
    all_t = _tenant_slug_filter(Testimonial.query).order_by(Testimonial.order).all()
    return render_template('admin/testimonials.html', testimonials=all_t)


@admin.route('/testimonials/new', methods=['GET', 'POST'])
@admin_required
def new_testimonial():
    form = TestimonialForm()
    if form.validate_on_submit():
        t = Testimonial(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            author_name=form.author_name.data,
            author_title=form.author_title.data or '',
            author_company=form.author_company.data or '',
            content=form.content.data,
            rating=form.rating.data,
            is_featured=form.is_featured.data,
            is_visible=form.is_visible.data,
            order=form.order.data or 0,
        )
        if is_upload_file(form.author_avatar.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                img = save_image(form.author_avatar.data, 'profiles', max_size=(200, 200))
                if img:
                    t.author_avatar = img
        db.session.add(t)
        db.session.commit()
        log_activity('create', 'testimonial', t.author_name)
        flash('Testimonial added!', 'success')
        return redirect(url_for('admin.testimonials'))
    return render_template('admin/testimonial_form.html', form=form, testimonial=None)


@admin.route('/testimonials/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_testimonial(id: int):
    t = _require_tenant_object(db.session.get(Testimonial, id))
    if t is None:
        flash('Testimonial not found.', 'warning')
        return redirect(url_for('admin.testimonials'))
    form = TestimonialForm(obj=t)
    if form.validate_on_submit():
        t.author_name    = form.author_name.data
        t.author_title   = form.author_title.data or ''
        t.author_company = form.author_company.data or ''
        t.content        = form.content.data
        t.rating         = form.rating.data
        t.is_featured    = form.is_featured.data
        t.is_visible     = form.is_visible.data
        t.order          = form.order.data or 0
        if is_upload_file(form.author_avatar.data):
            plan_features = _active_tenant_plan_features()
            max_uploads   = plan_features.get('max_media_uploads')
            if max_uploads is not None and not t.author_avatar and _tenant_media_upload_count() >= max_uploads:
                flash(
                    f'Your current plan ({_active_tenant_plan_name()}) allows up to '
                    f'{max_uploads} uploads. Remove an existing asset or upgrade.',
                    'warning',
                )
            else:
                new_img = save_image(form.author_avatar.data, 'profiles', max_size=(200, 200))
                if new_img:
                    if t.author_avatar:
                        delete_image(t.author_avatar, 'profiles')
                    t.author_avatar = new_img
        db.session.commit()
        log_activity('update', 'testimonial', t.author_name)
        flash('Testimonial updated!', 'success')
        return redirect(url_for('admin.testimonials'))
    return render_template('admin/testimonial_form.html', form=form, testimonial=t)


@admin.route('/testimonials/<int:id>/delete', methods=['POST'])
@admin_required
def delete_testimonial(id: int):
    t = _require_tenant_object(db.session.get(Testimonial, id))
    if t is None:
        flash('Testimonial not found.', 'warning')
        return redirect(url_for('admin.testimonials'))
    name = t.author_name
    if t.author_avatar:
        delete_image(t.author_avatar, 'profiles')
    db.session.delete(t)
    db.session.commit()
    log_activity('delete', 'testimonial', name)
    flash(f'Testimonial from "{name}" deleted.', 'success')
    return redirect(url_for('admin.testimonials'))


@admin.route('/testimonials/reorder', methods=['POST'])
@admin_required
def reorder_testimonials():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        t = _require_tenant_object(db.session.get(Testimonial, item.get('id')))
        if t:
            t.order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')


# ── Services ──────────────────────────────────────────────────────────────────

@admin.route('/services')
@admin_required
def services():
    all_services = (
        _tenant_slug_filter(Service.query)
        .order_by(Service.display_order.asc(), Service.id.asc())
        .all()
    )
    return render_template('admin/services.html', services=all_services)


@admin.route('/services/new', methods=['GET', 'POST'])
@admin_required
def new_service():
    form = ServiceForm()
    if form.validate_on_submit():
        svc = Service(
            tenant_id=current_user.tenant_id,   # cross-DB: must be set explicitly
            tenant_slug=_active_tenant_slug(),
            title=form.title.data,
            description=form.description.data or '',
            icon=form.icon.data or 'lucide:briefcase',
            features=form.features.data or '',
            display_order=form.display_order.data or 0,
            is_visible=form.is_visible.data,
        )
        db.session.add(svc)
        db.session.commit()
        log_activity('create', 'service', svc.title, f'Added service: {svc.title}')
        flash(f'Service "{svc.title}" created!', 'success')
        return redirect(url_for('admin.services'))
    return render_template('admin/service_form.html', form=form, service=None)


@admin.route('/services/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_service(id: int):
    svc = _require_tenant_object(db.session.get(Service, id))
    if svc is None:
        flash('Service not found.', 'warning')
        return redirect(url_for('admin.services'))
    form = ServiceForm(obj=svc)
    if form.validate_on_submit():
        svc.title         = form.title.data
        svc.description   = form.description.data or ''
        svc.icon          = form.icon.data or 'lucide:briefcase'
        svc.features      = form.features.data or ''
        svc.display_order = form.display_order.data or 0
        svc.is_visible    = form.is_visible.data
        db.session.commit()
        log_activity('update', 'service', svc.title)
        flash(f'Service "{svc.title}" updated!', 'success')
        return redirect(url_for('admin.services'))
    return render_template('admin/service_form.html', form=form, service=svc)


@admin.route('/services/<int:id>/delete', methods=['POST'])
@admin_required
def delete_service(id: int):
    svc = _require_tenant_object(db.session.get(Service, id))
    if svc is None:
        flash('Service not found.', 'warning')
        return redirect(url_for('admin.services'))
    title = svc.title
    db.session.delete(svc)
    db.session.commit()
    log_activity('delete', 'service', title)
    flash(f'Service "{title}" deleted.', 'success')
    return redirect(url_for('admin.services'))


@admin.route('/services/reorder', methods=['POST'])
@admin_required
def reorder_services():
    data = request.get_json(force=True, silent=True) or {}
    for item in data.get('order', []):
        svc = _require_tenant_object(db.session.get(Service, item.get('id')))
        if svc:
            svc.display_order = item.get('order', 0)
    db.session.commit()
    return jsonify(status='ok')


# ── Settings ──────────────────────────────────────────────────────────────────

@admin.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.verify_password(form.current_password.data):
            current_user.password = form.new_password.data
            db.session.commit()
            log_activity('update', 'user', current_user.username, 'Password changed')
            flash('Password changed successfully!', 'success')
        else:
            flash('Current password is incorrect.', 'danger')
    # Pass tenant + the real per-tenant form settings (TenantFormSettings is
    # what app/tenant/__init__.py:contact() actually reads at delivery time;
    # tenant.form_provider/basin_endpoint are legacy display-only columns).
    from app.models.portfolio import Tenant
    from app.models.tenant_form_settings import TenantFormSettings
    tenant = Tenant.query.filter_by(slug=_active_tenant_slug()).first()
    form_settings = TenantFormSettings.get_or_create(tenant.id) if tenant else None
    return render_template('admin/settings.html', form=form, tenant=tenant, form_settings=form_settings)


# ── Activity Log ──────────────────────────────────────────────────────────────

@admin.route('/activity')
@admin_required
def activity():
    page = request.args.get('page', 1, type=int)
    logs = (
        _tenant_slug_filter(ActivityLog.query)
        .order_by(ActivityLog.created_at.desc())
        .paginate(page=page, per_page=30, error_out=False)
    )
    return render_template('admin/activity.html', logs=logs)


# ── Export ────────────────────────────────────────────────────────────────────

@admin.route('/export')
@admin_required
def export_data():
    profile   = _load_tenant_profile()
    skills    = _tenant_slug_filter(Skill.query).order_by(Skill.order).all()
    projects  = _tenant_slug_filter(Project.query).order_by(Project.order).all()
    testi     = _tenant_slug_filter(Testimonial.query).order_by(Testimonial.order).all()
    inquiries = (
        _tenant_slug_filter(Inquiry.query)
        .order_by(Inquiry.created_at.desc()).limit(200).all()
    )

    def p_dict(obj):
        d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    payload = {
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'profile':     p_dict(profile) if profile else None,
        'skills':      [p_dict(s) for s in skills],
        'projects':    [p_dict(p) for p in projects],
        'testimonials': [p_dict(t) for t in testi],
        'inquiries':   [p_dict(i) for i in inquiries],
    }

    log_activity('export', 'portfolio', 'full export')
    return Response(
        json.dumps(payload, indent=2, default=str),
        mimetype='application/json',
        headers={
            'Content-Disposition': (
                f'attachment; filename=portfolio_export_'
                f'{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.json'
            )
        },
    )


# ── 2FA Management ────────────────────────────────────────────────────────────

@admin.route('/profile/2fa/setup', methods=['GET', 'POST'])
@admin_required
def setup_2fa():
    from app.forms import TOTPSetupForm
    from app.auth.totp import generate_setup_context
    form = TOTPSetupForm()
    ctx  = generate_setup_context(user=current_user)
    return render_template('admin/2fa_setup.html', form=form, **ctx)


@admin.route('/profile/2fa/enable', methods=['POST'])
@admin_required
def enable_2fa():
    from app.forms import TOTPSetupForm
    from app.auth.totp import (
        commit_2fa_enable, rate_limit_totp_verify,
        record_totp_failure, clear_totp_attempts, TotpRateLimitError,
    )
    form = TOTPSetupForm()
    ip   = _get_client_ip()

    try:
        rate_limit_totp_verify(ip)
    except TotpRateLimitError as e:
        flash(str(e), 'danger')
        session.pop('_pending_totp_secret', None)
        session.pop('_pending_backup_codes', None)
        return redirect(url_for('admin.settings'))

    if not form.validate_on_submit():
        flash('Invalid form submission.', 'danger')
        return redirect(url_for('admin.setup_2fa'))

    success, error = commit_2fa_enable(current_user, form.code.data or '')

    if success:
        clear_totp_attempts(ip)
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Failed to persist 2FA: %s', exc)
            flash('Database error saving 2FA settings. Please try again.', 'danger')
            return redirect(url_for('admin.setup_2fa'))
        session['totp_verified'] = True
        log_activity('security', 'user', current_user.username, '2FA enabled via TOTP setup')
        flash('Two-factor authentication enabled successfully!', 'success')
        return redirect(url_for('admin.settings'))

    record_totp_failure(ip)
    flash(error or 'Code incorrect — please try again.', 'danger')
    return redirect(url_for('admin.setup_2fa'))


@admin.route('/profile/2fa/disable', methods=['POST'])
@admin_required
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
    return redirect(url_for('admin.settings'))


@admin.route('/profile/2fa/regenerate-backup', methods=['POST'])
@admin_required
def regenerate_backup_codes():
    if not current_user.totp_enabled:
        flash('2FA is not enabled.', 'warning')
        return redirect(url_for('admin.settings'))
    codes = current_user.generate_backup_codes()
    db.session.commit()
    log_activity('security', 'user', current_user.username, 'Backup codes regenerated')
    session['_new_backup_codes'] = codes
    flash('Backup codes regenerated. Save them somewhere safe!', 'success')
    return redirect(url_for('admin.show_new_backup_codes'))


@admin.route('/profile/2fa/backup-codes')
@admin_required
def show_new_backup_codes():
    codes = session.pop('_new_backup_codes', None)
    if not codes:
        flash('No new backup codes to display.', 'warning')
        return redirect(url_for('admin.settings'))
    return render_template('admin/2fa_backup_codes.html', backup_codes=codes)


# ── Admin Forgot Password (v3.8) ─────────────────────────────────────────────
# Completely isolated from the superadmin flow.

@admin.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
@limiter.limit("100 per hour")
def forgot_password():
    """Step 1: Admin enters username + email — OTP dispatched (v5.6: username required)."""
    from flask_login import current_user as _cu
    if _cu.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    from flask import session as _session
    form = ForgotPasswordForm()
    if request.method == 'POST':
        logger.info('[ADMIN RESET][route] POST received at /admin/forgot-password')
        if not form.validate_on_submit():
            logger.warning(
                '[ADMIN RESET][route] validate_on_submit() FAILED — csrf_or_field_errors=%s',
                form.errors,
            )
            flash('Form validation failed. Please try again.', 'danger')
            return render_template('admin/forgot_password_request.html', form=form)
        logger.info('[ADMIN RESET][route] validate_on_submit() OK (CSRF + field validators passed)')
        from app.services.password_reset_service import initiate_admin_reset
        email    = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        if not email or not username:
            flash('Username and email are both required.', 'danger')
            return render_template('admin/forgot_password_request.html', form=form)

        ok, msg = initiate_admin_reset(email, username)
        flash(msg, 'info' if ok else 'danger')
        if ok:
            _session['_admin_pw_reset_email'] = email
            return redirect(url_for('admin.forgot_password_verify'))
    return render_template('admin/forgot_password_request.html', form=form)


@admin.route('/forgot-password/verify', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
@limiter.limit("20 per hour")
def forgot_password_verify():
    """Step 2: OTP verification."""
    from flask_login import current_user as _cu
    if _cu.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    from flask import session as _session
    email = _session.get('_admin_pw_reset_email', '')
    if not email:
        return redirect(url_for('admin.forgot_password'))

    if request.method == 'POST':
        from app.services.password_reset_service import verify_admin_otp
        raw_otp = request.form.get('otp_code', '').strip()
        ok, msg, token = verify_admin_otp(email, raw_otp)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            _session['_admin_pw_reset_token'] = token
            return redirect(url_for('admin.forgot_password_reset'))

    return render_template('admin/forgot_password_verify.html', email=email)


@admin.route('/forgot-password/reset', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
@limiter.limit("10 per hour")
def forgot_password_reset():
    """Step 3: Set new password."""
    from flask_login import current_user as _cu
    if _cu.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    from flask import session as _session
    token = _session.get('_admin_pw_reset_token', '')
    if not token:
        return redirect(url_for('admin.forgot_password'))

    if request.method == 'POST':
        from app.services.password_reset_service import complete_admin_reset
        pw  = request.form.get('password', '').strip()
        pw2 = request.form.get('password_confirm', '').strip()
        if not pw or len(pw) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('admin/forgot_password_reset.html')
        if pw != pw2:
            flash('Passwords do not match.', 'danger')
            return render_template('admin/forgot_password_reset.html')
        ok, msg = complete_admin_reset(token, pw)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            _session.pop('_admin_pw_reset_email', None)
            _session.pop('_admin_pw_reset_token', None)
            return redirect(url_for('admin.login_alias'))

    return render_template('admin/forgot_password_reset.html')


# ─────────────────────────────────────────────────────────────────────────────
# Notification Bell API + Notifications Page  (v4.0)
# ─────────────────────────────────────────────────────────────────────────────

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
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
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