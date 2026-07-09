"""
app/superadmin/blueprint.py — Shared blueprint object, auth guard, and
helpers used by more than one route module (Phase 4b blueprint split).

Moved here verbatim from the former monolithic app/superadmin/__init__.py
(v3.1). No behavior changes — see PHASE4_AUDIT.md / blueprint split plan.
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


logger = logging.getLogger(__name__)
superadmin = Blueprint('superadmin', __name__)


# == Auth guard ==============================================================

def superadmin_required(f):
    """Decorator: requires authenticated superadmin. Safe redirect on failure."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            # FIX: always redirect to superadmin.login, not 'root'
            return redirect(url_for('superadmin.login', next=request.url))
        if not current_user.is_superadmin:
            flash('Superadmin access required.', 'danger')
            # FIX: safe fallback — 'root' endpoint is defined in create_app
            return redirect(_safe_root())
        return f(*args, **kwargs)
    return decorated


def _safe_root():
    """Return root URL safely without risking BuildError."""
    try:
        return url_for('root')
    except Exception:
        return '/'


# == before_request gate (defense-in-depth — root-cause fix for RBAC-01) =====
#
# FIX [RBAC-01]: Prior to this, superadmin routes relied ENTIRELY on each
# individual view carrying its own @superadmin_required decorator (or, in
# system_check.py's case, a manual inline check). There was no blueprint-
# level backstop — a single missed decorator on a future route (or on a
# child blueprint mounted under this one, e.g. superadmin_billing_plans)
# meant a fully unauthenticated superadmin-tier endpoint, silently, with
# no test or lint catching it. The `admin` blueprint (app/admin/blueprint.py
# ::block_public_admin) already uses this pattern; this mirrors it here so
# the two admin-tier surfaces have equivalent baseline protection.
#
# This does NOT replace @superadmin_required on individual routes — keep
# using it explicitly for clarity/self-documentation. This is a backstop,
# not a replacement: if a route decorator is ever forgotten, this still
# blocks the request instead of silently allowing it through.
_PUBLIC_SUPERADMIN_ENDPOINTS = {
    'superadmin.login',
    'superadmin.logout',
    'superadmin.forgot_password',
    'superadmin.forgot_password_request',
    'superadmin.forgot_password_verify',
    'superadmin.forgot_password_reset',
}


@superadmin.before_request
def block_public_superadmin():
    """Gate every /superadmin/* request (including nested child blueprints
    registered via superadmin.register_blueprint(...)) behind an
    authenticated superadmin session, except the explicit login/logout/
    password-recovery endpoints above."""
    if request.endpoint in _PUBLIC_SUPERADMIN_ENDPOINTS:
        return None

    if not current_user.is_authenticated:
        return redirect(url_for('superadmin.login', next=request.url))

    if not current_user.is_superadmin:
        flash('Superadmin access required.', 'danger')
        return redirect(_safe_root())

    return None


# == Context processor ========================================================

@superadmin.context_processor
def inject_tenant_count():
    try:
        count = profile_repository.query.count()
    except Exception:
        count = 0

    try:
        unread_count = inquiry_repository.query.filter(Inquiry.thread_unread_super > 0).count()
    except Exception:
        unread_count = 0

    return dict(
        tenant_count=count,
        message_unread_count=unread_count,
    )


# == Shared helpers (used by >1 route module) =================================

def _normalize_timestamp(value):
    """Return a UTC-aware datetime for arithmetic, preserving UTC if naive."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')

