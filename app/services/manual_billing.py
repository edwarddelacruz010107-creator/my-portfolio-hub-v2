"""
Manual payment workflow — method selection, proof upload, superadmin review.

v3.4.1 fixes:
  • get_active_payment_methods_for_tenant() — canonical function with explicit
    Global/tenant-scoped merge, ordered by display_order, with debug logging
    when the result set is empty so ops can diagnose missing method config.
  • get_active_payment_methods() — kept as thin alias for backward-compat.
  • get_payment_method_for_tenant() — now accepts None tenant_id gracefully
    (treats it as "superadmin context"; allows global methods through).
  • save_billing_upload() — unchanged, kept for completeness.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app import db
from app.models.portfolio import (
    Inquiry,
    PaymentMethod,
    PaymentSubmission,
    Profile,
    Subscription,
    normalize_plan_name,
)
from app.services.billing import activate_subscription, get_or_create_pending_subscription
from app.utils import generate_license_key, log_billing_event, get_plan_price, send_subscription_activated_notification

logger = logging.getLogger(__name__)


# ── Core method-visibility service ───────────────────────────────────────────

def get_active_payment_methods_for_tenant(tenant_id: int | None) -> list[PaymentMethod]:
    """
    Return ALL active PaymentMethods visible to a tenant, ordered for display.

    Visibility rules:
      - Global methods (tenant_id IS NULL): always included when active.
      - Tenant-specific methods (tenant_id == given tenant_id): included when
        active and the tenant_id matches.
      - PayMongo method_type rows are included here so callers can detect them
        — callers are responsible for routing PayMongo vs manual UI separately.

    Args:
        tenant_id: The tenant's PK. Pass None to retrieve global-only methods
                   (e.g. for anonymous or superadmin contexts).

    Returns:
        Ordered list of active PaymentMethod ORM objects.
    """
    if tenant_id is not None:
        q = (
            PaymentMethod.query
            .filter(
                PaymentMethod.is_active == True,  # noqa: E712
                or_(
                    PaymentMethod.tenant_id.is_(None),
                    PaymentMethod.tenant_id == tenant_id,
                ),
            )
        )
    else:
        # Superadmin / anonymous: global only
        q = PaymentMethod.query.filter(
            PaymentMethod.is_active == True,  # noqa: E712
            PaymentMethod.tenant_id.is_(None),
        )

    methods = q.order_by(
        PaymentMethod.is_default.desc(),
        PaymentMethod.display_order.asc(),
        PaymentMethod.name.asc(),
    ).all()

    # Diagnostic logging — helps diagnose "No payment methods configured" in UI
    if not methods:
        logger.warning(
            'BILLING visibility: no active PaymentMethods found for tenant_id=%s. '
            'Ensure at least one global method (tenant_id=NULL, is_active=TRUE) '
            'exists in payment_methods, or that PayMongo is enabled.',
            tenant_id,
        )
    else:
        logger.debug(
            'BILLING visibility: %d method(s) visible to tenant_id=%s: %s',
            len(methods),
            tenant_id,
            [f'{m.id}:{m.name}({"global" if m.tenant_id is None else m.tenant_id})' for m in methods],
        )

    return methods


def get_active_payment_methods(tenant_id: int | None) -> list[PaymentMethod]:
    """
    Backward-compatible alias for get_active_payment_methods_for_tenant().

    Callers should migrate to the explicit name; this alias remains so that
    existing imports in billing_handlers.py and admin/__init__.py continue to work.
    """
    return get_active_payment_methods_for_tenant(tenant_id)


def get_manual_payment_methods(tenant_id: int | None) -> list[PaymentMethod]:
    """
    Return only non-PayMongo active methods (bank, ewallet, crypto).
    Use this when building the "manual payment" UI section.
    """
    return [
        m for m in get_active_payment_methods_for_tenant(tenant_id)
        if m.method_type != 'paymongo'
    ]


def get_payment_method_for_tenant(method_id: int, tenant_id: int | None) -> PaymentMethod | None:
    """
    Load an active PaymentMethod, enforcing tenant isolation.

    Rules:
      - Global methods (tenant_id IS NULL): accessible by any tenant.
      - Tenant-specific: only accessible by the owning tenant.
      - If tenant_id is None (superadmin context), all active methods are allowed.

    Returns None when not found, inactive, or cross-tenant access is attempted.
    """
    method = db.session.get(PaymentMethod, method_id)
    if not method or not method.is_active:
        logger.warning(
            'BILLING: method_id=%s not found or inactive (tenant_id=%s)',
            method_id, tenant_id,
        )
        return None
    # Global methods are accessible to all
    if method.tenant_id is None:
        return method
    # Superadmin context: bypass isolation check
    if tenant_id is None:
        return method
    # Tenant-specific: enforce isolation
    if method.tenant_id != tenant_id:
        logger.warning(
            'BILLING isolation violation: tenant_id=%s attempted to access method_id=%s '
            'belonging to tenant_id=%s',
            tenant_id, method_id, method.tenant_id,
        )
        return None
    return method


# ── File upload ───────────────────────────────────────────────────────────────

def save_billing_upload(file_storage, *, image_only: bool = False) -> tuple[str | None, str | None]:
    """
    Validate and save a billing proof upload; returns (filename, error).

    v3.9 FIX #1 + #4:
      • Always uses validate_billing_proof_upload() (jpg/jpeg/png/webp/pdf only).
      • Reads leading bytes for magic-byte verification before saving.
      • image_only param kept for API compatibility but no longer changes the
        allowed-extension set (billing proofs are always restricted).
    """
    from app.security import FileUploadPolicy

    if not file_storage or not file_storage.filename:
        return None, None

    filename = secure_filename(file_storage.filename)
    if not filename:
        return None, 'Invalid filename.'

    data = file_storage.read()
    file_storage.seek(0)

    # FIX #1 + #4: always use billing-proof strict whitelist + magic-byte check
    ok, err = FileUploadPolicy.validate_billing_proof_upload(filename, len(data), file_bytes=data)
    if not ok:
        return None, err

    unique_name = f'{secrets.token_hex(12)}_{filename}'
    upload_dir = Path(current_app.static_folder) / 'uploads' / 'billing'
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_storage.save(upload_dir / unique_name)
    return unique_name, None


# ── Method management ─────────────────────────────────────────────────────────

def set_default_payment_method(method: PaymentMethod) -> None:
    """Mark one method as default (clears other defaults in same scope)."""
    scope = PaymentMethod.tenant_id == method.tenant_id
    PaymentMethod.query.filter(scope, PaymentMethod.id != method.id).update(
        {'is_default': False}, synchronize_session=False
    )
    method.is_default = True
    db.session.commit()
    log_billing_event(
        'payment_method_default',
        method.tenant.slug if method.tenant else 'global',
        f'Set default payment method: {method.name}',
    )


# ── Payment submission flow ───────────────────────────────────────────────────

def submit_manual_payment(
    profile: Profile,
    *,
    method: PaymentMethod,
    plan: str,
    amount_paid: float,
    payment_reference: str,
    note: str = '',
    proof_filename: str | None = None,
    billing_cycle: str = 'monthly',
) -> PaymentSubmission:
    """Create pending subscription + payment submission for manual review."""
    plan_norm = normalize_plan_name(plan)
    sub = get_or_create_pending_subscription(
        db.session,
        profile.tenant_id,
        plan_norm,
        billing_cycle=billing_cycle,
    )
    sub.payment_method = method.name
    db.session.flush()

    submission = PaymentSubmission(
        tenant=profile.tenant,
        subscription_id=sub.id,
        payment_method_id=method.id,
        plan=plan_norm,
        amount_paid=float(amount_paid or get_plan_price(plan_norm)),
        payment_method=method.name,
        payment_reference=(payment_reference or '').strip(),
        payment_proof=proof_filename or '',
        note=(note or '').strip(),
        status='pending',
    )
    db.session.add(submission)
    db.session.commit()

    log_billing_event(
        'manual_submit',
        profile.tenant_slug,
        f'Manual payment submitted via {method.name} (ref {(payment_reference or "")[:32]})',
    )
    return submission


def approve_payment_submission(
    submission: PaymentSubmission,
    *,
    reviewer: str,
    review_notes: str = '',
) -> tuple[bool, str]:
    """Approve submission: activate subscription and notify tenant."""
    if submission.status != 'pending':
        return False, 'Submission already reviewed.'

    profile = Profile.query.filter_by(tenant_id=submission.tenant_id).first()
    if not profile:
        return False, 'Tenant profile not found.'

    sub = submission.subscription
    if not sub:
        sub = profile.current_subscription()
    if not sub:
        return False, 'No subscription linked to this submission.'

    now = datetime.now(timezone.utc)
    submission.status = 'approved'
    submission.reviewed_at = now
    submission.reviewed_by = reviewer
    submission.review_notes = review_notes or f'Approved by {reviewer}'

    license_key = generate_license_key(submission.plan, profile.tenant_slug)

    activate_subscription(
        sub,
        plan=submission.plan,
        billing_cycle=getattr(sub, 'billing_cycle', 'monthly') or 'monthly',
    )
    sub.payment_method = submission.payment_method or 'manual'

    # BUG#1 / BUG#6 FIX: clear trial fields so the tenant never sees
    # "Trial" status after a paid subscription is activated.
    profile.free_trial_days = 0
    profile.free_trial_ends = None

    # Sync plan onto tenant/profile rows and bust caches
    if hasattr(profile, '_current_subscription_cache'):
        del profile._current_subscription_cache
    profile.sync_license_from_subscription()

    db.session.commit()

    send_subscription_activated_notification(profile, sub)

    log_billing_event(
        'approve',
        profile.tenant_slug,
        f'Manual payment approved — {submission.plan} activated (submission #{submission.id})',
    )
    return True, f'Subscription activated for {profile.tenant_slug}.'


def reject_payment_submission(
    submission: PaymentSubmission,
    *,
    reviewer: str,
    review_notes: str = '',
) -> tuple[bool, str]:
    if submission.status != 'pending':
        return False, 'Submission already reviewed.'

    submission.status = 'rejected'
    submission.reviewed_at = datetime.now(timezone.utc)
    submission.reviewed_by = reviewer
    submission.review_notes = review_notes or f'Rejected by {reviewer}'
    db.session.commit()

    profile = Profile.query.filter_by(tenant_id=submission.tenant_id).first()
    slug = profile.tenant_slug if profile else str(submission.tenant_id)
    log_billing_event('reject', slug, f'Manual payment rejected (submission #{submission.id})')
    return True, 'Payment submission rejected.'


def notify_tenant_billing_message(profile: Profile, subject: str, message: str) -> None:
    """In-app billing notification via Inquiry (superadmin sender)."""
    inquiry = Inquiry(
        tenant_slug=profile.tenant_slug,
        name='Billing Team',
        email='billing@platform',
        subject=subject,
        message=message,
        sender='superadmin',
        is_read=False,
    )
    db.session.add(inquiry)
    db.session.commit()
