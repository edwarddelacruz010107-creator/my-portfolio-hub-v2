"""
app/tenant/__init__.py — Tenant-scoped URL Blueprint (v3.1)

FIXES from v3.0:
  • 'default' slug is now EXCLUDED from tenant_bp routes (it's served at /).
    Requests to /default/ get a 301 to / instead of going through this blueprint.
  • before_request guard rejects 'default' and returns 301 redirect.
  • This prevents the double-URL problem (/default/... still works via redirect).

Route map:
  GET  /<tenant_slug>/                        → tenant portfolio homepage
  POST /<tenant_slug>/contact                 → contact form submission
  GET  /<tenant_slug>/project/<slug>          → project detail
  GET  /<tenant_slug>/auth/login              → tenant-scoped login page
  GET  /<tenant_slug>/auth/logout             → logout
  GET  /<tenant_slug>/auth/2fa               → TOTP verification
  GET  /<tenant_slug>/admin/                  → admin entry (redirects if not authed)
  GET  /<tenant_slug>/admin/login             → tenant-scoped admin login page
"""

import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import (
    Blueprint, render_template, redirect, url_for,
    abort, request, session, g, jsonify, flash, current_app,
)
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app import db, csrf, limiter, cache
from app.forms import PlanSelectionForm
from app.security import FileUploadPolicy, log_security_event
from app.models.portfolio import (
    Profile, Project, Skill, Testimonial, Service,
    Inquiry, Subscription, TenantCommunicationSettings,
    normalize_plan_name,
)
from app.utils import BILLING_PLANS, is_paymongo_enabled, log_activity
from app.services.billing import subscription_access_status, is_in_grace_period
from app.services.billing_handlers import (
    billing_payment_context,
    billing_plans_context,
    handle_billing_payment_post,
    handle_billing_plans_post,
)
from app.services.manual_billing import get_payment_method_for_tenant

logger = logging.getLogger(__name__)

tenant_bp = Blueprint('tenant', __name__, url_prefix='/<tenant_slug>')

# Slugs that CANNOT be used as tenant identifiers
_RESERVED_SLUGS = frozenset({
    'auth', 'admin', 'superadmin', 'static', 'health',
    'heartbeat', 'favicon.ico', 'robots.txt',
    'default',  # FIX: 'default' is served at root /, not as a slug
})

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,78}[a-z0-9]$|^[a-z0-9]{2,80}$')
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$')

LICENSE_PLANS = {
    'Basic': {'duration_days': 30},
    'Pro': {'duration_days': 90},
    'Enterprise': {'duration_days': 365},
}



def _license_plan_details(plan: str) -> dict:
    return LICENSE_PLANS.get(normalize_plan_name(plan), LICENSE_PLANS['Basic'])


def _is_valid_slug(slug: str) -> bool:
    if not slug:
        return False
    if slug.lower() in _RESERVED_SLUGS:
        return False
    return bool(_SLUG_RE.match(slug))


# ── Request lifecycle hooks ───────────────────────────────────────────────────

@tenant_bp.url_value_preprocessor
def pull_tenant_slug(endpoint, values):
    """Extract tenant_slug from URL and store on g before route handler runs."""
    slug = (values or {}).pop('tenant_slug', None)
    if slug:
        slug = slug.strip().lower()
    g.tenant_slug = slug


@tenant_bp.before_request
def load_tenant():
    """
    Validate and load tenant Profile from DB.
    FIX: 'default' slug is rejected here with a 301 to root.
         This handles any /<slug>/ requests where slug='default'.
    """
    slug = getattr(g, 'tenant_slug', None)

    # FIX: redirect /default/... → root equivalent paths
    if slug == 'default':
        # Strip /default prefix from the path and redirect
        path = request.path
        new_path = path[len('/default'):]  # e.g. /default/auth/login → /auth/login
        if not new_path or new_path == '/':
            return redirect('/', 301)
        return redirect(new_path, 301)

    if not slug or not _is_valid_slug(slug):
        abort(404)

    if not hasattr(g, 'tenant_profile'):
        profile = Profile.query.filter_by(tenant_slug=slug).first()
        # FIX: Admin/auth login routes must work even when the Profile row does
        # not exist yet (e.g. tenant was created by superadmin but admin has not
        # filled in their profile). Only 404 for PUBLIC portfolio routes.
        admin_routes = ('/admin/', '/admin/login', '/auth/login', '/auth/2fa')
        is_admin_route = any(request.path.rstrip('/').endswith(r.rstrip('/')) for r in admin_routes)
        if not profile and not is_admin_route:
            abort(404)
        g.tenant_profile = profile  # may be None for admin login routes

    # v3.7 VULN-07 FIX: Only set session tenant from URL for unauthenticated
    # visitors or superadmins (tenant-switching). For authenticated non-superadmin
    # users the DB value is authoritative — we never override it from the URL.
    if current_user.is_authenticated and not current_user.is_superadmin:
        # Silently ignore URL slug; resolve_active_tenant() will return DB value
        pass
    elif current_user.is_authenticated and current_user.is_superadmin:
        # Superadmin explicitly navigating to a tenant's public page — update
        # session so admin panel reflects chosen tenant
        session['tenant_slug'] = slug
    else:
        # Unauthenticated visitor — safe to set from URL
        session['tenant_slug'] = slug

    # ── Trial / subscription expiry gate (v3.3) ──────────────────────────────
    # On public routes: show suspended page immediately.
    # On billing routes: always allow through so tenant can subscribe.
    # On admin auth routes: allow through so tenant can log in.
    _allow_paths = (
        '/billing', '/auth/', '/admin/login',
    )
    _is_billing_or_auth = any(
        request.path.rstrip('/').startswith(f'/{slug}{p.rstrip("/")}')
        for p in _allow_paths
    ) or request.path.rstrip('/').endswith('/auth/login')       or request.path.rstrip('/').endswith('/auth/2fa')

    profile = getattr(g, 'tenant_profile', None)
    if profile and not _is_billing_or_auth and profile.is_expired():
        profile.enforce_expiry(commit=True)
        return render_template(
            'tenant/suspended.html',
            profile=profile,
            tenant_slug=slug,
            license_status=profile.license_status(),
            subscription_status=subscription_access_status(profile),
            trial_days_left=profile.trial_days_remaining(),
            in_grace=is_in_grace_period(profile),
        ), 402


# ── Contact helpers ───────────────────────────────────────────────────────────

# ── Public portfolio routes ───────────────────────────────────────────────────

@cache.cached(
    timeout=60,
    key_prefix=lambda: f"portfolio_page:{g.get('tenant_slug', 'default')}",
    unless=lambda: hasattr(g, '_skip_cache') or request.args.get('preview'),
)
@tenant_bp.route('/')
@tenant_bp.route('')
def portfolio():
    """Public portfolio homepage for a non-default tenant."""
    tenant  = g.tenant_slug
    profile = g.tenant_profile

    # is_expired() is the authoritative check — catches both trial + subscription expiry.
    # The before_request gate already handles this for non-cached requests;
    # this guard handles the cached path.
    if profile and profile.is_expired():
        profile.enforce_expiry(commit=True)
        return render_template(
            'tenant/suspended.html',
            profile=profile,
            tenant_slug=tenant,
            license_status=profile.license_status(),
            trial_days_left=profile.trial_days_remaining(),
        ), 402

    all_projects = Project.published_for_tenant(tenant).all()
    featured_projects = [p for p in all_projects if p.is_featured]
    other_projects = [p for p in all_projects if not p.is_featured]

    skills = (
        Skill.query
        .filter(
            Skill.tenant_slug == tenant,
            or_(Skill.is_visible == True, Skill.is_visible.is_(None)),
        )
        .order_by(Skill.category.asc(), Skill.order.asc())
        .all()
    )
    testimonials = (
        Testimonial.query
        .filter_by(is_visible=True, tenant_slug=tenant)
        .order_by(Testimonial.order.asc())
        .all()
    )
    services = (
        Service.query
        .filter_by(is_visible=True, tenant_slug=tenant)
        .order_by(Service.display_order.asc(), Service.id.asc())
        .all()
    )

    skills_by_category = {}
    for skill in skills:
        skills_by_category.setdefault(skill.category, []).append(skill)

    categories = sorted({p.category for p in featured_projects + other_projects if p.category})

    stats = {
        'projects_count':   Project.query.filter_by(status='published', tenant_slug=tenant).count(),
        'years_experience': profile.get_years_experience() if profile else 0,
        'clients_count':    profile.clients_count if profile else 0,
    }

    trial_days_left = profile.trial_days_remaining() if profile else 0
    license_status = profile.license_status() if profile else 'unlicensed'

    return render_template(
        'main/index.html',
        profile=profile,
        featured_projects=featured_projects,
        other_projects=other_projects,
        skills=skills,
        skills_by_category=skills_by_category,
        testimonials=testimonials,
        services=services,
        stats=stats,
        categories=categories,
        tenant_slug=tenant,
        contact_url=url_for('tenant.contact', tenant_slug=tenant),
        is_root_domain=False,
        trial_days_left=trial_days_left,
        license_status=license_status,
    )


@tenant_bp.route('/project/<slug>')
def project_detail(slug: str):
    """Public project detail page for a non-default tenant."""
    tenant  = g.tenant_slug
    profile = g.tenant_profile

    # is_expired() is the authoritative check — catches both trial + subscription expiry.
    # The before_request gate already handles this for non-cached requests;
    # this guard handles the cached path.
    if profile and profile.is_expired():
        profile.enforce_expiry(commit=True)
        return render_template(
            'tenant/suspended.html',
            profile=profile,
            tenant_slug=tenant,
            license_status=profile.license_status(),
            trial_days_left=profile.trial_days_remaining(),
        ), 402

    project = (
        Project.query
        .filter_by(slug=slug, status='published', tenant_slug=tenant)
        .first_or_404()
    )
    project.increment_views()
    db.session.commit()

    related = (
        Project.query
        .filter(
            Project.status      == 'published',
            Project.id          != project.id,
            Project.category    == project.category,
            Project.tenant_slug == tenant,
        )
        .order_by(Project.order.asc())
        .limit(3)
        .all()
    )

    return render_template(
        'main/project.html',
        project=project,
        profile=profile,
        related=related,
        tenant_slug=tenant,
    )


@tenant_bp.route('/license', methods=['GET', 'POST'])
def license():
    tenant = g.tenant_slug
    flash('License keys are no longer required. Subscribe via PayMongo on the Billing page.', 'info')
    return redirect(url_for('tenant.billing_plans', tenant_slug=tenant))


def _tenant_billing_access_allowed(tenant: str) -> bool:
    if not current_user.is_authenticated:
        return False
    if current_user.is_superadmin:
        return True
    return current_user.is_admin and current_user.tenant_slug == tenant


@tenant_bp.route('/billing')
@login_required
def billing():
    tenant = g.tenant_slug
    profile = g.tenant_profile
    if profile is None:
        abort(404)
    if not _tenant_billing_access_allowed(tenant):
        abort(403)
    subscription = profile.current_subscription()
    return render_template(
        'billing/index.html',
        profile=profile,
        subscription=subscription,
        subscription_status=subscription_access_status(profile),
        license_status=profile.license_status(),
        trial_days_left=profile.trial_days_remaining(),
        plans=BILLING_PLANS,
        tenant_slug=tenant,
        paymongo_enabled=is_paymongo_enabled(),
        billing_routes={
            'overview': 'tenant.billing',
            'plans': 'tenant.billing_plans',
            'history': 'tenant.billing_history',
        },
    )


@tenant_bp.route('/billing/plans', methods=['GET', 'POST'])
@login_required
def billing_plans():
    tenant = g.tenant_slug
    profile = g.tenant_profile
    if profile is None:
        abort(404)
    if not _tenant_billing_access_allowed(tenant):
        abort(403)

    subscription = profile.current_subscription()
    form = PlanSelectionForm(plan=normalize_plan_name(subscription.plan if subscription else profile.plan or 'Basic'))

    # Check for payment success/failure from PayMongo
    status = request.args.get('status')
    if status == 'success':
        flash('Payment received! Your subscription is now active.', 'success')
    elif status == 'failed':
        flash('Payment failed. Please try again or choose a different payment method.', 'danger')
    elif status == 'cancelled':
        flash('Payment was cancelled. You can try again anytime.', 'warning')

    billing_routes = {
        'overview': 'tenant.billing',
        'plans': 'tenant.billing_plans',
        'history': 'tenant.billing_history',
        'payment': 'tenant.billing_payment',
    }
    paymongo_enabled = is_paymongo_enabled()

    if request.method == 'POST':
        response, exc = handle_billing_plans_post(
            profile,
            tenant_slug=tenant,
            billing_routes=billing_routes,
            paymongo_enabled=paymongo_enabled,
            payment_route_builder=lambda mid, **kw: url_for(
                'tenant.billing_payment',
                tenant_slug=tenant,
                method_id=mid,
                billing_cycle=kw.get('billing_cycle', 'monthly'),
            ),
        )
        if response is not None:
            return response
        if exc is not None:
            logger.exception('tenant billing_plans update failed: %s', exc)
            flash('Failed to update plan. Please try again.', 'danger')
            return redirect(url_for('tenant.billing_plans', tenant_slug=tenant))

    ctx = billing_plans_context(
        profile,
        tenant_slug=tenant,
        billing_routes=billing_routes,
        paymongo_enabled=paymongo_enabled,
    )
    return render_template('billing/plans.html', **ctx)


@tenant_bp.route('/billing/payment/<int:method_id>', methods=['GET', 'POST'])
@login_required
def billing_payment(method_id):
    tenant = g.tenant_slug
    profile = g.tenant_profile
    if profile is None:
        abort(404)
    if not _tenant_billing_access_allowed(tenant):
        abort(403)

    method = get_payment_method_for_tenant(method_id, profile.tenant_id)
    if not method:
        flash('Payment method not found or inactive.', 'danger')
        return redirect(url_for('tenant.billing_plans', tenant_slug=tenant))

    billing_cycle = request.args.get('billing_cycle', 'monthly')
    billing_routes = {
        'overview': 'tenant.billing',
        'plans': 'tenant.billing_plans',
        'history': 'tenant.billing_history',
        'payment': 'tenant.billing_payment',
    }

    if request.method == 'POST':
        response = handle_billing_payment_post(
            profile,
            method,
            billing_cycle=billing_cycle,
            success_redirect=url_for('tenant.billing', tenant_slug=tenant),
        )
        if response is not None:
            return response

    ctx = billing_payment_context(
        profile,
        method,
        tenant_slug=tenant,
        billing_routes=billing_routes,
        billing_cycle=billing_cycle,
    )
    return render_template('billing/payment.html', **ctx)


@tenant_bp.route('/billing/history')
@login_required
def billing_history():
    """Tenant billing: show payment submission history and subscription timeline."""
    tenant = g.tenant_slug
    profile = g.tenant_profile
    if profile is None:
        abort(404)
    if not _tenant_billing_access_allowed(tenant):
        abort(403)

    subscriptions = (
        Subscription.query
        .filter_by(tenant_id=profile.tenant_id)
        .order_by(Subscription.created_at.desc())
        .all()
    )
    return render_template(
        'billing/history.html',
        profile=profile,
        subscriptions=subscriptions,
        tenant_slug=tenant,
        billing_routes={
            'overview': 'tenant.billing',
            'plans': 'tenant.billing_plans',
            'history': 'tenant.billing_history',
        },
    )


@tenant_bp.route('/contact', methods=['POST'])
@csrf.exempt
@limiter.limit(
    "5 per minute; 20 per hour",
    key_func=lambda: f"{g.get('tenant_slug', 'default')}:{request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()}"
)
def contact():
    """
    Contact form submission for a tenant (v5.8).

    Delegates to contact_service.process_contact_submission() which handles:
      - Input sanitization and validation
      - Inquiry persistence (zero message loss guarantee)
      - Provider dispatch: basin | web3forms | email_only | disabled
      - Automatic fallback to internal inbox on external provider failure
      - Admin notification on fallback
      - Full structured logging at every step

    The Inquiry is ALWAYS saved to DB before any external call.
    """
    # Honeypot
    if request.form.get('website', ''):
        return jsonify(status='success', message='Your message has been sent.')

    tenant_slug = g.tenant_slug

    # Extract raw form fields — contact_service handles sanitization
    raw = request.form
    name       = raw.get('name', '').strip()
    email      = raw.get('email', '').strip()
    subject    = raw.get('subject', '').strip()
    message    = raw.get('message', '').strip()
    sub_id     = raw.get('submission_id', '').strip()[:80]

    # Client IP (prefer first hop of X-Forwarded-For)
    ip = (request.headers.get('X-Forwarded-For', request.remote_addr) or '')
    if ',' in ip:
        ip = ip.split(',')[0].strip()

    from app.services.contact_service import process_contact_submission
    result = process_contact_submission(
        tenant_slug=tenant_slug,
        name=name,
        email=email,
        subject=subject,
        message=message,
        ip_address=ip,
        user_agent=(request.headers.get('User-Agent') or '')[:300],
        submission_id=sub_id or None,
    )

    if not result.success:
        return jsonify(status='error', message=result.delivery_error or 'Submission failed.'), 400

    log_activity('create', 'inquiry', name, f'Contact from {email} to tenant {tenant_slug!r}')

    profile = getattr(g, 'tenant_profile', None)
    tenant_display = (
        profile.name
        if profile and getattr(profile, 'name', None)
        else tenant_slug.replace('-', ' ').title()
    )
    return jsonify(
        status='success',
        message=f"Message sent to {tenant_display}. I'll get back to you soon!",
    )


# ── Auth proxy routes ─────────────────────────────────────────────────────────

@tenant_bp.route('/auth/login', methods=['GET', 'POST'])
def auth_login():
    """Tenant-scoped login. Session already has tenant_slug from before_request."""
    from app.auth import _handle_login
    tenant = g.tenant_slug
    return _handle_login(
        require_admin=True,
        default_next=url_for('admin.dashboard'),
        action_url=url_for('tenant.auth_login', tenant_slug=tenant),
        page_title=f'{tenant.replace("-", " ").title()} Admin Portal',
        page_subtitle=f'Sign in to manage the {tenant} portfolio',
    )


@tenant_bp.route('/auth/logout')
@login_required
def auth_logout():
    """Tenant-aware logout."""
    from flask_login import logout_user
    tenant = g.tenant_slug
    log_activity('logout', 'user', current_user.username)
    session.pop('totp_verified', None)
    session.pop('tenant_slug', None)
    logout_user()
    flash('You have been signed out.', 'info')
    return redirect(url_for('tenant.portfolio', tenant_slug=tenant))


@tenant_bp.route('/auth/2fa', methods=['GET', 'POST'])
def auth_2fa():
    """TOTP verification step for tenant login flow."""
    from app.auth import verify_2fa as _verify_2fa_handler
    return _verify_2fa_handler()


# ── Admin redirect routes ─────────────────────────────────────────────────────

@tenant_bp.route('/admin/login', methods=['GET', 'POST'])
@tenant_bp.route('/admin/login/', methods=['GET', 'POST'])
def admin_login():
    """Tenant-scoped admin login page at /<tenant_slug>/admin/login."""
    from app.auth import _handle_login
    tenant = g.tenant_slug

    if current_user.is_authenticated:
        if current_user.is_superadmin or current_user.tenant_slug == tenant:
            session['tenant_slug'] = tenant
            return redirect(url_for('admin.dashboard'))
        flash('You do not have access to this tenant admin.', 'danger')
        return redirect(url_for('tenant.auth_login', tenant_slug=tenant))

    return _handle_login(
        require_admin=True,
        default_next=url_for('admin.dashboard'),
        action_url=url_for('tenant.admin_login', tenant_slug=tenant),
        page_title=f'{tenant.replace("-", " ").title()} Admin Portal',
        page_subtitle=f'Sign in to manage the {tenant} portfolio',
    )


@tenant_bp.route('/admin/')
@tenant_bp.route('/admin')
def admin_root():
    """
    /<tenant_slug>/admin/ - Gate to admin panel.
    Sets tenant context in session then forwards to /admin/.
    """
    tenant = g.tenant_slug
    if current_user.is_authenticated:
        if current_user.is_superadmin or current_user.tenant_slug == tenant:
            session['tenant_slug'] = tenant
            return redirect(url_for('admin.dashboard'))
        else:
            flash('You do not have access to this tenant admin.', 'danger')
            return redirect(url_for('tenant.admin_login', tenant_slug=tenant))
    return redirect(url_for('tenant.admin_login', tenant_slug=tenant))


# ── Tenant Forgot Password (v3.8) ─────────────────────────────────────────────
# Route prefix: /<tenant_slug>/auth/forgot-password/...
# Tenant A can NEVER reset Tenant B — tenant_id enforced in service layer.

@tenant_bp.route('/auth/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5 per minute; 15 per hour',
               key_func=lambda: f"tenant-fpw:{g.get('tenant_slug','?')}:{request.remote_addr}")
def auth_forgot_password():
    """Step 1: email (+ optional username) → OTP dispatch."""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        from app.services.password_reset_service import initiate_tenant_reset
        email    = request.form.get('email', '').strip().lower()
        username = request.form.get('username', '').strip()
        if not email or not username:
            flash('Username and email are both required.', 'danger')
            return render_template('tenant/forgot_password_request.html',
                                   tenant_slug=g.tenant_slug)
        ok, msg = initiate_tenant_reset(email, username, g.tenant_slug)
        flash(msg, 'info' if ok else 'danger')
        if ok:
            session['_tenant_pw_reset_email']  = email
            session['_tenant_pw_reset_slug']   = g.tenant_slug
            return redirect(url_for('tenant.auth_forgot_password_verify',
                                    tenant_slug=g.tenant_slug))
    return render_template('tenant/forgot_password_request.html',
                           tenant_slug=g.tenant_slug)


@tenant_bp.route('/auth/forgot-password/verify', methods=['GET', 'POST'])
@limiter.limit('10 per minute',
               key_func=lambda: f"tenant-otp:{g.get('tenant_slug','?')}:{request.remote_addr}")
def auth_forgot_password_verify():
    """Step 2: OTP entry."""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    email = session.get('_tenant_pw_reset_email', '')
    slug  = session.get('_tenant_pw_reset_slug', '')
    if not email or slug != g.tenant_slug:
        return redirect(url_for('tenant.auth_forgot_password', tenant_slug=g.tenant_slug))

    if request.method == 'POST':
        from app.services.password_reset_service import verify_tenant_otp
        raw_otp = request.form.get('otp_code', '').strip()
        ok, msg, token = verify_tenant_otp(email, raw_otp, g.tenant_slug)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            session['_tenant_pw_reset_token'] = token
            return redirect(url_for('tenant.auth_forgot_password_reset',
                                    tenant_slug=g.tenant_slug))

    from app.services.password_reset_service import _get_ttl_minutes
    return render_template('tenant/forgot_password_verify.html',
                           tenant_slug=g.tenant_slug, email=email,
                           otp_ttl_minutes=_get_ttl_minutes())


@tenant_bp.route('/auth/forgot-password/reset', methods=['GET', 'POST'])
def auth_forgot_password_reset():
    """Step 3: New password + tenant isolation enforcement."""
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    token = session.get('_tenant_pw_reset_token', '')
    slug  = session.get('_tenant_pw_reset_slug', '')
    if not token or slug != g.tenant_slug:
        return redirect(url_for('tenant.auth_forgot_password', tenant_slug=g.tenant_slug))

    if request.method == 'POST':
        from app.services.password_reset_service import complete_tenant_reset
        from app.models.portfolio import Tenant as _Tenant
        tenant = _Tenant.query.filter_by(slug=g.tenant_slug).first()
        if not tenant:
            abort(404)
        pw  = request.form.get('password', '').strip()
        pw2 = request.form.get('password_confirm', '').strip()
        if not pw or len(pw) < 8:
            flash('Password must be at least 8 characters.', 'danger')
            return render_template('tenant/forgot_password_reset.html',
                                   tenant_slug=g.tenant_slug)
        if pw != pw2:
            flash('Passwords do not match.', 'danger')
            return render_template('tenant/forgot_password_reset.html',
                                   tenant_slug=g.tenant_slug)
        ok, msg = complete_tenant_reset(token, pw, tenant.id)
        flash(msg, 'success' if ok else 'danger')
        if ok:
            session.pop('_tenant_pw_reset_email', None)
            session.pop('_tenant_pw_reset_token', None)
            session.pop('_tenant_pw_reset_slug', None)
            return redirect(url_for('tenant.auth_login', tenant_slug=g.tenant_slug))

    return render_template('tenant/forgot_password_reset.html',
                           tenant_slug=g.tenant_slug)
