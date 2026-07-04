"""
app/admin/blueprint.py — Shared blueprint object, auth guard,
before_request tenant-resolution gate, and helpers used by more than one
route module (Phase 4b admin blueprint split).

Moved here verbatim from the former monolithic app/admin/__init__.py
(v3.4.2). No behavior changes.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional

from flask import (session, Blueprint, render_template, redirect, url_for,
                   flash, request, jsonify, current_app, Response)
from flask_login import login_required, current_user as _flask_current_user

from app import db

# Expose a module-level current_user symbol so legacy tests and patching
# against app.admin.current_user continue to work.
current_user = _flask_current_user
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
    resolve_active_tenant,
    stamp_session_tenant as _tenant_stamp_session_tenant,
    RESERVED_SLUGS, session_tenant_valid,
)
from app import limiter  # Flask-Limiter instance
from app.forms import ForgotPasswordForm  # Flask-WTF form for CSRF protection

logger = logging.getLogger(__name__)
admin  = Blueprint('admin', __name__)


logger = logging.getLogger(__name__)
admin  = Blueprint('admin', __name__)

# The canonical slug for the primary administrator portfolio.
# Centralised here so all helpers reference the same constant.
_DEFAULT_TENANT_SLUG = 'default'

# Deprecated LICENSE_PLANS removed — use BILLING_PLANS from app.utils instead.
LICENSE_PLANS = BILLING_PLANS


# == Auth decorator ===========================================================

def admin_required(f):
    """Require authenticated user with is_admin=True."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if not (getattr(user, 'is_admin', False) or getattr(user, 'is_superadmin', False)):
            flash('Admin access required.', 'danger')
            return redirect(_safe_root())
        return f(*args, **kwargs)
    return decorated


def _safe_root():
    try:
        return url_for('root')
    except Exception:
        return '/'


# == Tenant resolution (shared across profile/projects/uploads/testimonials) ==

def _get_current_user():
    """Return the current_user, preferring the patchable admin package symbol."""
    try:
        import app.admin as _admin
        return getattr(_admin, 'current_user', _flask_current_user)
    except Exception:
        return _flask_current_user


def _get_stamp_session_tenant():
    """Return the patchable app.admin stamp_session_tenant helper if present."""
    try:
        import app.admin as _admin
        return getattr(_admin, 'stamp_session_tenant', _tenant_stamp_session_tenant)
    except Exception:
        return _tenant_stamp_session_tenant


def _active_tenant_slug() -> str:
    """
    v3.7: Thin wrapper over resolve_active_tenant() (app.tenant_security).
    Kept for backward compatibility — all call sites in this blueprint
    already use this name.  The actual resolution logic lives in one place.
    """
    user = _get_current_user()
    if getattr(user, 'is_authenticated', False) and not getattr(user, 'is_superadmin', False):
        slug = getattr(user, 'tenant_slug', None)
        if slug:
            return slug
    return resolve_active_tenant()


def _load_tenant_profile() -> Optional[Profile]:
    """
    Load the Profile for the active tenant.

    FIX v3.4.2: Previously fell back to Profile.query.first() which could
    return any tenant's profile.  Now falls back to the explicit default-slug
    query, and only if that also misses does it log a warning.
    """
    tenant_slug = _active_tenant_slug()

    profile = profile_repository.get_by_tenant_slug(tenant_slug)
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
    user = _get_current_user()
    if getattr(user, 'is_authenticated', False) and getattr(user, 'is_superadmin', False):
        fallback = profile_repository.get_by_tenant_slug(_DEFAULT_TENANT_SLUG)
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
    user = _get_current_user()
    if getattr(user, 'is_superadmin', False):
        return obj  # Superadmin can access any object
    tenant_slug = _active_tenant_slug()
    obj_tenant = getattr(obj, 'tenant_slug', None)
    if obj_tenant != tenant_slug:
        logger.warning(
            'TENANT isolation: user id=%s (tenant=%r) attempted to access '
            'object %s id=%s (tenant=%r) — blocked.',
            getattr(user, 'id', '?'), tenant_slug,
            type(obj).__name__, getattr(obj, 'id', '?'), obj_tenant,
        )
        return None
    return obj


def _active_tenant_plan_features() -> dict:
    profile = _load_tenant_profile()
    if profile:
        return profile.plan_features()
    tenant_slug = _active_tenant_slug()
    tenant = tenant_repository.get_by_slug(tenant_slug)
    if tenant:
        return tenant.plan_features()
    return get_plan_features('Basic')


def _active_tenant_plan_name() -> str:
    profile = _load_tenant_profile()
    if profile:
        return profile.effective_plan()
    tenant_slug = _active_tenant_slug()
    tenant = tenant_repository.get_by_slug(tenant_slug)
    return tenant.normalized_plan if tenant else 'Basic'


def _tenant_media_upload_count() -> int:
    profile = _load_tenant_profile()
    count = 1 if profile and profile.profile_image else 0
    count += _tenant_slug_filter(project_repository.query).filter(Project.image != None).filter(Project.image != '').count()
    count += _tenant_slug_filter(testimonial_repository.query).filter(Testimonial.author_avatar != None).filter(Testimonial.author_avatar != '').count()
    return count


# == before_request gate (must live on the blueprint object itself) ==========

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
    user = _get_current_user()
    _truly_authenticated = getattr(user, 'is_authenticated', False) and _session_has_user

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
    if not user.is_superadmin:
        user_tenant    = getattr(user, 'tenant_slug', None) or _DEFAULT_TENANT_SLUG
        session_tenant = session.get('tenant_slug')

        if session_tenant != user_tenant:
            logger.info(
                'TENANT correction in block_public_admin: user id=%s '
                '(assigned tenant=%r) had session tenant=%r — correcting.',
                user.id, user_tenant, session_tenant,
            )
            # FIX v3.7: Use stamp_session_tenant (HMAC) instead of a bare
            # session write.  A raw write leaves _tsig stale → TenantGuard
            # rejects the next request, kicking users mid-2FA flow.
            _get_stamp_session_tenant()(user.id, user_tenant)

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
        getattr(user, 'is_authenticated', False)
        and getattr(user, 'totp_enabled', False)
        and not session.get('totp_verified')
        and request.endpoint not in _bypass_endpoints
    ):
        # Stamp the session with a unique verification nonce to prevent
        # replay attacks where an old totp_verified=True cookie is reused.
        session['_2fa_user_id']      = user.id
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
        getattr(user, 'is_authenticated', False)
        and not getattr(user, 'is_superadmin', False)
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
        
def _get_client_ip():
    """Safely determine client IP address."""
    forwarded_for = request.headers.get("X-Forwarded-For", "")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.remote_addr or "0.0.0.0"