"""
Manual payment workflow — method selection, proof upload, superadmin review.

v6.3.1 hardening:
  • approve_payment_submission() and reject_payment_submission() now wrapped in
    try/except with rollback — no partial activations on DB failure.
  • approve_payment_submission() validates tenant, profile, and subscription
    before mutating state; each check returns a descriptive error tuple.
  • Structured logging added: [PAYMENT_REVIEW] [PAYMENT_APPROVED]
    [PAYMENT_REJECTED] [SUBSCRIPTION_ACTIVATED] [TRANSACTION_FAILED]
  • Double-approval guard: returns (False, reason) if already reviewed.
  • Rejection stores reason correctly and sends in-app notification.
  • All existing function signatures preserved for backward compatibility.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from flask import current_app
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from app import db
from app.models.portfolio import (
    PaymentMethod,
    PaymentSubmission,
    Profile,
    Subscription,
    normalize_plan_name,
)
from app.services.billing import activate_subscription, get_or_create_pending_subscription
from app.services.billing.private_proof_storage import (
    PrivateProofStorageError,
    save_private_billing_proof,
)
from app.utils import generate_license_key, log_billing_event, get_plan_price

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
    """Backward-compatible alias for get_active_payment_methods_for_tenant()."""
    return get_active_payment_methods_for_tenant(tenant_id)


def get_manual_payment_methods(tenant_id: int | None) -> list[PaymentMethod]:
    """Return only non-PayMongo active methods (bank, ewallet, crypto)."""
    return [
        m for m in get_active_payment_methods_for_tenant(tenant_id)
        if m.method_type != 'paymongo'
    ]


def get_payment_method_for_tenant(method_id: int, tenant_id: int | None) -> PaymentMethod | None:
    """
    Load an active PaymentMethod, enforcing tenant isolation.
    Returns None when not found, inactive, or cross-tenant access is attempted.
    """
    method = db.session.get(PaymentMethod, method_id)
    if not method or not method.is_active:
        logger.warning(
            'BILLING: method_id=%s not found or inactive (tenant_id=%s)',
            method_id, tenant_id,
        )
        return None
    if method.tenant_id is None:
        return method
    if tenant_id is None:
        return method
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
    Enforces jpg/jpeg/png/webp/pdf whitelist + magic-byte check.
    """
    from app.security import FileUploadPolicy

    if not file_storage or not file_storage.filename:
        return None, None

    filename = secure_filename(file_storage.filename)
    if not filename:
        return None, 'Invalid filename.'

    data = file_storage.read()
    file_storage.seek(0)

    ok, err = FileUploadPolicy.validate_billing_proof_upload(filename, len(data), file_bytes=data)
    if not ok:
        return None, err

    # Payment-method QR codes are intentionally public checkout assets. Payment
    # proofs are private customer evidence and must use the separate private
    # storage boundary and opaque references.
    provider = str(current_app.config.get('STORAGE_PROVIDER') or '').strip().lower()
    if not provider and bool(current_app.config.get('USE_CLOUDINARY_STORAGE', False)):
        provider = 'cloudinary'
    if image_only and provider == 'cloudinary':
        try:
            from app.utils.cloudinary_storage import is_configured, save_image
            if not is_configured():
                return None, 'Cloudinary is selected but its credentials are incomplete.'
            remote_url = save_image(file_storage, folder='billing')
            if not remote_url:
                return None, 'The payment QR code could not be stored. Please try again.'
            return remote_url, None
        except Exception:
            logger.exception('Cloudinary payment QR upload failed')
            return None, 'The payment QR code could not be stored. Please try again.'

    if not image_only:
        try:
            return save_private_billing_proof(file_storage), None
        except PrivateProofStorageError as exc:
            logger.warning('Private billing-proof upload rejected: %s', exc)
            return None, str(exc)
        except Exception:
            logger.exception('Private billing-proof upload failed')
            return None, 'The payment proof could not be stored privately. Please try again.'

    unique_name = f'{secrets.token_hex(12)}_{filename}'
    from app.services.media.upload_storage import ensure_upload_folder
    upload_dir = ensure_upload_folder('billing')
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
    expected_amount: float | None = None,
    amount_usd: float | None = None,
    currency_code: str = 'USD',
    exchange_rate: float | None = None,
    country_code: str | None = None,
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

    # FIX [MED-COUPON-01]: capture the system-computed reference price
    # (list price minus any validated coupon) at the moment of submission,
    # independent of the tenant-editable amount_paid field, so the
    # superadmin review screen has something authoritative to diff against
    # instead of trusting the self-reported figure at face value.
    # Never let a discount-quote failure block the submission itself —
    # this is a review aid, not a gate.
    computed_expected_amount = expected_amount
    coupon_code_applied = None
    try:
        from app.services.billing import discount_checkout
        quote = discount_checkout.quote_for_context(
            tenant_id=profile.tenant_id,
            plan=plan_norm,
            billing_cycle=billing_cycle,
            code=discount_checkout.peek_coupon(profile.tenant_id),
        )
        if computed_expected_amount is None:
            computed_expected_amount = float(quote.amount_after)
        coupon_code_applied = quote.campaign.code if quote.campaign else None
    except Exception:
        logger.exception(
            'submit_manual_payment: failed to compute expected_amount for '
            'tenant_id=%s plan=%s — submission will proceed without a '
            'reference price for the reviewer.',
            profile.tenant_id, plan_norm,
        )

    submission = PaymentSubmission(
        tenant=profile.tenant,
        subscription_id=sub.id,
        payment_method_id=method.id,
        plan=plan_norm,
        amount_paid=float(amount_paid if amount_paid is not None else get_plan_price(plan_norm)),
        expected_amount=computed_expected_amount,
        amount_usd=amount_usd,
        currency_code=(currency_code or 'USD').upper()[:3],
        exchange_rate=exchange_rate,
        country_code=(country_code or '').upper()[:2] or None,
        coupon_code_applied=coupon_code_applied,
        payment_method=method.name,
        payment_reference=(payment_reference or '').strip(),
        payment_proof=proof_filename or '',
        note=(note or '').strip(),
        status='pending',
    )
    from app.services.billing.financial_conversion import set_exact_paid_amount
    set_exact_paid_amount(
        submission,
        amount=submission.amount_paid,
        currency=submission.currency_code,
        exponent=2,
    )
    db.session.add(submission)
    db.session.commit()

    log_billing_event(
        'manual_submit',
        profile.tenant_slug,
        f'Manual payment submitted via {method.name} (ref {(payment_reference or "")[:32]})',
    )
    try:
        from app.services.notification_service import Recipient, publish_notification
        publish_notification(
            recipient=Recipient.role('superadmin'),
            event_type='billing.payment_submitted',
            template_key='billing.payment_submitted',
            parameters={
                'tenant_name': profile.tenant.company_name or profile.tenant_slug,
                'currency_code': submission.currency_code or 'USD',
            },
            dedupe_key=f'billing.payment_submitted:{submission.id}',
            entity_type='payment_submission',
            entity_id=submission.id,
            actor_type='tenant',
            actor_id=profile.tenant_id,
            action_route='superadmin.billing_submissions',
            priority='high',
            commit=True,
        )
    except Exception:
        logger.exception(
            '[PAYMENT_REVIEW] Superadmin notification failed for submission_id=%s',
            submission.id,
        )
    return submission


def approve_payment_submission(
    submission: PaymentSubmission,
    *,
    reviewer: str,
    review_notes: str = '',
) -> tuple[bool, str]:
    """
    Approve submission: validate → activate subscription → notify tenant.

    v6.3.1 hardening:
      • Double-approval guard (idempotent check before any mutation).
      • Full try/except with rollback to prevent partial state.
      • Structured logging at each phase.
      • In-app notification on success.
    """
    review_notes = str(review_notes or '').strip()
    if not review_notes:
        return False, 'A review reason is required.'
    logger.info(
        '[PAYMENT_REVIEW] approve_payment_submission called: submission_id=%s tenant_id=%s status=%s reviewer=%s',
        submission.id, submission.tenant_id, submission.status, reviewer,
    )

    query = PaymentSubmission.query.filter_by(id=submission.id)
    if db.session.get_bind().dialect.name != 'sqlite':
        query = query.with_for_update()
    submission = query.populate_existing().first()
    if submission is None:
        return False, 'Payment submission not found.'

    # ── Guard: already reviewed ───────────────────────────────────────────────
    if submission.status != 'pending':
        logger.warning(
            '[PAYMENT_REVIEW] submission_id=%s already in status=%s — skipping',
            submission.id, submission.status,
        )
        return False, f'Submission already reviewed (status: {submission.status}).'

    # ── Validate profile ──────────────────────────────────────────────────────
    profile = Profile.query.filter_by(tenant_id=submission.tenant_id).first()
    if not profile:
        logger.error(
            '[PAYMENT_REVIEW] Profile not found for tenant_id=%s (submission_id=%s)',
            submission.tenant_id, submission.id,
        )
        return False, 'Tenant profile not found — cannot activate subscription.'

    # ── Validate tenant record ────────────────────────────────────────────────
    if not submission.tenant:
        logger.error(
            '[PAYMENT_REVIEW] submission.tenant is None for submission_id=%s', submission.id,
        )
        return False, 'Tenant record missing from submission — contact support.'

    # ── Validate subscription ─────────────────────────────────────────────────
    sub = submission.subscription
    if not sub:
        sub = profile.current_subscription()
    if not sub:
        logger.error(
            '[PAYMENT_REVIEW] No subscription linked to submission_id=%s tenant_id=%s',
            submission.id, submission.tenant_id,
        )
        return False, 'No subscription found to activate. Create a pending subscription first.'

    # ── Apply changes inside transaction ─────────────────────────────────────
    try:
        now = datetime.now(timezone.utc)

        # Mark submission approved
        submission.status       = 'approved'
        submission.reviewed_at  = now
        submission.reviewed_by  = reviewer
        submission.review_notes = review_notes

        # Generate license key (informational — stored on Profile)
        license_key = generate_license_key(submission.plan, profile.tenant_slug)
        logger.debug(
            '[PAYMENT_APPROVED] Generated license_key=%s for tenant=%s plan=%s',
            license_key, profile.tenant_slug, submission.plan,
        )

        # Activate the subscription (sets status=active, started_at, expires_at)
        billing_cycle = getattr(sub, 'billing_cycle', 'monthly') or 'monthly'
        activate_subscription(
            sub,
            plan=submission.plan,
            billing_cycle=billing_cycle,
            amount=float(submission.amount_paid or 0),
            currency=submission.currency_code,
            currency_exponent=getattr(submission, 'amount_paid_exponent', None) or 2,
            source=f'manual-approval:{submission.id}',
        )
        sub.payment_method = submission.payment_method or 'manual'

        # Redeem any coupon selected at plan-selection time. Uses the
        # durable sub.coupon_code (not the Flask session) since this handler
        # runs in the superadmin's request, not the tenant's — the tenant's
        # session-stashed coupon is not visible here.
        from app.services.billing import discount_checkout
        from app.services.billing.currency import get_plan_usd_amount
        base_amount_usd = get_plan_usd_amount(submission.plan, billing_cycle)
        redemption = discount_checkout.apply_on_activation(
            tenant_id=submission.tenant_id,
            subscription=sub,
            plan=submission.plan,
            billing_cycle=billing_cycle,
            code=sub.coupon_code,
            base_amount_override=base_amount_usd,
            # The subscription/payment snapshot is in the tenant's chosen
            # settlement currency. Do not overwrite it with a USD coupon quote.
            sync_subscription_amount=False,
            commit=False,
        )

        # Preserve the exact locally converted amount approved by the reviewer.
        from app.services.billing.financial_conversion import set_exact_paid_amount
        set_exact_paid_amount(
            sub,
            amount=float(submission.amount_paid or 0),
            currency=submission.currency_code,
            exponent=getattr(submission, 'amount_paid_exponent', None) or 2,
        )
        try:
            sub.price_paid = float(submission.amount_paid or 0)
        except Exception:
            pass

        fx_rate = Decimal(str(submission.exchange_rate or 1))
        subtotal_local = (Decimal(str(base_amount_usd)) * fx_rate).quantize(Decimal('0.01'))
        total_local = Decimal(str(submission.amount_paid or 0)).quantize(Decimal('0.01'))
        discount_local = max(Decimal('0.00'), subtotal_local - total_local)

        from app.services.billing import invoice_service
        invoice = invoice_service.record_invoice(
            tenant_id=submission.tenant_id,
            subscription=sub,
            plan=submission.plan,
            billing_cycle=billing_cycle,
            payment_method=sub.payment_method,
            payment_provider='manual',
            payment_reference=submission.payment_reference,
            redemption=redemption,
            amount_subtotal_override=subtotal_local,
            amount_discount_override=discount_local,
            amount_total_override=total_local,
            currency_override=submission.currency_code or 'USD',
            original_amount_minor=submission.amount_paid_minor,
            original_currency=submission.currency_code if submission.amount_paid_minor is not None else None,
            currency_exponent=submission.amount_paid_exponent if submission.amount_paid_minor is not None else None,
            actor=reviewer,
            commit=False,
        )

        from app.services.ledger import post_manual_submission
        post_manual_submission(
            submission,
            reviewer=reviewer,
            invoice_id=invoice.id if invoice else None,
            session=db.session,
            commit=False,
        )

        logger.info(
            '[SUBSCRIPTION_ACTIVATED] tenant=%s plan=%s expires_at=%s',
            profile.tenant_slug, sub.plan, sub.expires_at,
        )

        # Clear trial state — tenant is now a paid subscriber
        profile.free_trial_days = 0
        profile.free_trial_ends = None

        # Sync plan onto profile row + bust subscription cache
        if hasattr(profile, '_current_subscription_cache'):
            del profile._current_subscription_cache
        profile.sync_license_from_subscription()

        logger.debug(
            '[PAYMENT_APPROVED] Profile synced: tenant=%s plan=%s',
            profile.tenant_slug, profile.plan,
        )

        # Commit the full transaction
        db.session.commit()
        logger.info(
            '[PAYMENT_APPROVED] Transaction committed for submission_id=%s tenant=%s',
            submission.id, profile.tenant_slug,
        )

    except Exception as exc:
        db.session.rollback()
        logger.exception(
            '[TRANSACTION_FAILED] approve_payment_submission rolled back: submission_id=%s error=%s',
            submission.id, exc,
        )
        return False, f'Database error during approval — rolled back. Please retry. ({type(exc).__name__})'

    # ── Post-commit: notifications (non-fatal) ────────────────────────────────
    try:
        from app.services.notification_service import Recipient, publish_notification
        publish_notification(
            recipient=Recipient.tenant(int(submission.tenant_id)),
            event_type='billing.payment_approved',
            template_key='billing.payment_approved',
            parameters={'plan_name': (submission.plan or 'Subscription').title()},
            dedupe_key=f'billing.payment_approved:{submission.id}',
            entity_type='payment_submission',
            entity_id=submission.id,
            actor_type='superadmin',
            action_route='admin.billing_overview',
            priority='high',
            channels=('in_app', 'email'),
            commit=True,
        )
        _process_email_outbox_best_effort()
    except Exception as exc:
        logger.warning(
            '[PAYMENT_APPROVED] Notification failed (non-fatal): %s', exc,
        )

    log_billing_event(
        'approve',
        profile.tenant_slug,
        f'Manual payment approved — {submission.plan} activated (submission #{submission.id})',
    )

    return True, f'Payment approved. {submission.plan} subscription activated for {profile.tenant_slug}.'


def reject_payment_submission(
    submission: PaymentSubmission,
    *,
    reviewer: str,
    review_notes: str = '',
) -> tuple[bool, str]:
    """
    Reject submission: set status, record reason, notify tenant.

    v6.3.1 hardening:
      • Full try/except with rollback.
      • In-app rejection notification with reason.
      • Structured logging.
    """
    logger.info(
        '[PAYMENT_REVIEW] reject_payment_submission called: submission_id=%s tenant_id=%s status=%s reviewer=%s',
        submission.id, submission.tenant_id, submission.status, reviewer,
    )

    query = PaymentSubmission.query.filter_by(id=submission.id)
    if db.session.get_bind().dialect.name != 'sqlite':
        query = query.with_for_update()
    submission = query.populate_existing().first()
    if submission is None:
        return False, 'Payment submission not found.'

    if submission.status != 'pending':
        logger.warning(
            '[PAYMENT_REVIEW] submission_id=%s already reviewed (status=%s)',
            submission.id, submission.status,
        )
        return False, f'Submission already reviewed (status: {submission.status}).'

    reason = review_notes.strip() if review_notes else ''
    if not reason:
        return False, 'A review reason is required.'

    try:
        submission.status       = 'rejected'
        submission.reviewed_at  = datetime.now(timezone.utc)
        submission.reviewed_by  = reviewer
        submission.review_notes = reason

        from app.models import FinancialAuditEvent
        db.session.add(FinancialAuditEvent(
            transaction_id=None,
            tenant_id=submission.tenant_id,
            action='manual_payment_rejected',
            actor=reviewer,
            reason=reason,
            safe_details={'submission_id': submission.id, 'idempotency_key': f'manual-review:{submission.id}:reject'},
        ))

        db.session.commit()
        logger.info(
            '[PAYMENT_REJECTED] submission_id=%s tenant_id=%s reason=%s',
            submission.id, submission.tenant_id, reason,
        )

    except Exception as exc:
        db.session.rollback()
        logger.exception(
            '[TRANSACTION_FAILED] reject_payment_submission rolled back: submission_id=%s error=%s',
            submission.id, exc,
        )
        return False, f'Database error during rejection — rolled back. ({type(exc).__name__})'

    # ── Post-commit: in-app notification (non-fatal) ──────────────────────────
    try:
        from app.services.notification_service import Recipient, publish_notification
        profile = Profile.query.filter_by(tenant_id=submission.tenant_id).first()
        publish_notification(
            recipient=Recipient.tenant(int(submission.tenant_id)),
            event_type='billing.payment_rejected',
            template_key='billing.payment_rejected',
            parameters={'review_reason': reason[:240]},
            dedupe_key=f'billing.payment_rejected:{submission.id}',
            entity_type='payment_submission',
            entity_id=submission.id,
            actor_type='superadmin',
            action_route='admin.billing_overview',
            priority='high',
            channels=('in_app', 'email'),
            commit=True,
        )
        _process_email_outbox_best_effort()
        slug = profile.tenant_slug if profile else str(submission.tenant_id)
    except Exception as exc:
        logger.warning('[PAYMENT_REJECTED] In-app notification failed (non-fatal): %s', exc)
        slug = str(submission.tenant_id)

    log_billing_event('reject', slug, f'Manual payment rejected (submission #{submission.id}): {reason}')
    return True, f'Payment submission rejected. Tenant notified.'


def notify_tenant_billing_message(profile: Profile, subject: str, message: str) -> None:
    """Compatibility entrypoint; publishes to the unified notification feed."""
    from app.services.notification_service import Recipient, publish_notification
    publish_notification(
        recipient=Recipient.tenant(int(profile.tenant_id)),
        event_type='legacy.billing_message',
        template_key='legacy.literal',
        parameters={'title': subject, 'message': message},
        dedupe_key=f'legacy.billing_message:{profile.tenant_id}:{secrets.token_hex(8)}',
        actor_type='superadmin',
        action_route='admin.billing_overview',
        commit=True,
    )


def _process_email_outbox_best_effort() -> None:
    """Attempt queued email now; retry metadata remains durable on failure."""
    from app.services.notification_service import process_pending_email_deliveries
    from app.services.mailersend_service import send_email

    def resolve(notification):
        tenant = notification.target_tenant
        if tenant is None:
            return None
        recipient = tenant.contact_email or tenant.email or None
        if recipient:
            return recipient
        try:
            user = tenant.users.first()
            return user.email if user else None
        except Exception:
            return None

    process_pending_email_deliveries(
        recipient_resolver=resolve,
        sender=send_email,
        limit=10,
    )
