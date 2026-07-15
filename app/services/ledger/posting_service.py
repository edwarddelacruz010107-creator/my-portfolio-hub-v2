"""Only persistence boundary for immutable payment transaction rows."""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
import logging
import uuid

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.extensions import cache, db
from app.models.ledger import FinancialAuditEvent, PaymentTransaction
from app.services.ledger.adapters import ADAPTERS, FxSnapshot
from app.services.ledger.domain import LedgerPosting, exact_decimal, major_from_minor


logger = logging.getLogger(__name__)
LEDGER_CACHE_GENERATION_KEY = "ledger-analytics:generation"


def invalidate_ledger_analytics_cache() -> None:
    try:
        cache.set(LEDGER_CACHE_GENERATION_KEY, uuid.uuid4().hex, timeout=0)
    except Exception:
        logger.warning("Ledger analytics cache invalidation failed", exc_info=True)


def _find_existing(session, posting: LedgerPosting):
    return session.query(PaymentTransaction).filter(
        PaymentTransaction.provider == posting.provider,
        PaymentTransaction.provider_environment == posting.provider_environment,
        PaymentTransaction.accounting_type == posting.accounting_type,
        or_(
            PaymentTransaction.provider_event_id == posting.provider_event_id,
            PaymentTransaction.provider_transaction_id == posting.provider_transaction_id,
        ),
    ).first()


def append_posting(
    posting: LedgerPosting,
    *,
    session=None,
    invoice_id: int | None = None,
    commit: bool = False,
) -> tuple[PaymentTransaction, bool]:
    """Append once under a nested transaction; DB uniqueness wins races."""
    session = session or db.session
    existing = _find_existing(session, posting)
    if existing is not None:
        return existing, False

    row = PaymentTransaction(
        id=str(uuid.uuid4()),
        tenant_id=posting.tenant_id,
        subscription_id=posting.subscription_id,
        invoice_id=invoice_id,
        provider=posting.provider,
        provider_account=posting.provider_account,
        provider_environment=posting.provider_environment,
        provider_event_id=posting.provider_event_id,
        provider_transaction_id=posting.provider_transaction_id,
        event_type=posting.event_type,
        accounting_type=posting.accounting_type,
        status=posting.status,
        original_amount_minor=posting.original_amount_minor,
        original_currency=posting.original_currency,
        currency_exponent=posting.currency_exponent,
        usd_reporting_amount=posting.usd_reporting_amount,
        fx_rate=posting.fx_rate,
        fx_rate_source=posting.fx_rate_source,
        fx_effective_at=posting.fx_effective_at,
        occurred_at=posting.occurred_at,
        received_at=posting.received_at,
        settled_at=posting.settled_at,
        recorded_at=datetime.now(timezone.utc),
        reversal_of_id=posting.reversal_of_id,
        safe_metadata=dict(posting.safe_metadata),
        created_by=posting.created_by,
        approved_by=posting.approved_by,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = _find_existing(session, posting)
        if existing is None:
            raise
        return existing, False

    if commit:
        session.commit()
    invalidate_ledger_analytics_cache()
    return row, True


def _audit(*, session, transaction, action: str, actor: str, reason: str, details=None):
    event = FinancialAuditEvent(
        transaction_id=transaction.id if transaction else None,
        tenant_id=transaction.tenant_id if transaction else None,
        action=action,
        actor=(actor or "system")[:120],
        reason=(reason or "No reason provided").strip(),
        safe_details=dict(details or {}),
    )
    session.add(event)
    session.flush()
    return event


def post_provider_event(
    provider: str,
    payload: dict,
    *,
    tenant_id: int,
    subscription_id: int | None,
    received_at: datetime,
    environment: str,
    event_id_override: str | None = None,
    fx: FxSnapshot | None = None,
    invoice_id: int | None = None,
    session=None,
    commit: bool = False,
):
    adapter = ADAPTERS.get(str(provider).lower())
    if adapter is None:
        raise ValueError("unsupported provider adapter")
    normalized_payload = dict(payload)
    if event_id_override:
        normalized_payload["event_id"] = event_id_override
        normalized_payload.setdefault("id", event_id_override)
    posting = adapter.normalize(
        normalized_payload,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        received_at=received_at,
        environment=environment,
        fx=fx,
    )
    if posting is None:
        return None, False
    session = session or db.session
    if posting.accounting_type in {"refund", "chargeback"}:
        original = None
        if posting.source_provider_transaction_id:
            original = session.query(PaymentTransaction).filter_by(
                provider=posting.provider,
                provider_environment=posting.provider_environment,
                provider_transaction_id=posting.source_provider_transaction_id,
                accounting_type="settlement",
                status="posted",
            ).first()
        posting = replace(
            posting,
            reversal_of_id=original.id if original else None,
            status=posting.status if original else "review_required",
        )
    transaction, created = append_posting(
        posting, session=session, invoice_id=invoice_id, commit=False
    )
    if created and transaction.status == "review_required":
        _audit(
            session=session,
            transaction=transaction,
            action="financial_posting_review_required",
            actor="provider-adapter",
            reason="Posting lacks reproducible FX evidence or a required original settlement link",
            details={"source_type": posting.provider, "source_id": posting.provider_event_id},
        )
    if commit:
        session.commit()
    return transaction, created


def _legacy_decimal(value, *, name: str) -> Decimal:
    """Convert a legacy DB numeric/float snapshot once at the migration edge."""
    if value is None:
        raise ValueError(f"{name} is required")
    return Decimal(str(value))


def post_manual_submission(
    submission,
    *,
    reviewer: str,
    invoice_id: int | None = None,
    session=None,
    commit: bool = False,
):
    session = session or db.session
    amount = _legacy_decimal(submission.amount_paid, name="amount_paid")
    exact_minor = getattr(submission, "amount_paid_minor", None)
    exponent = getattr(submission, "amount_paid_exponent", None)
    if exact_minor is not None and exponent is not None:
        amount_minor = int(exact_minor)
        exponent = int(exponent)
    else:
        amount_minor = int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        exponent = 2
    currency = str(submission.currency_code or "").upper()
    occurred = submission.reviewed_at or submission.submitted_at or datetime.now(timezone.utc)
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)

    usd = None
    fx_rate = None
    fx_source = None
    fx_effective_at = None
    if currency == "USD":
        usd = major_from_minor(amount_minor, exponent)
    elif submission.amount_usd is not None and submission.exchange_rate is not None:
        usd = _legacy_decimal(submission.amount_usd, name="amount_usd")
        fx_rate = _legacy_decimal(submission.exchange_rate, name="exchange_rate")
        fx_source = "manual_submission_snapshot"
        fx_effective_at = occurred

    posting = LedgerPosting(
        tenant_id=submission.tenant_id,
        subscription_id=submission.subscription_id,
        provider="manual",
        provider_account=f"tenant:{submission.tenant_id}",
        provider_environment="live",
        provider_event_id=f"manual-submission:{submission.id}",
        provider_transaction_id=f"manual-submission:{submission.id}",
        event_type="manual.payment.approved",
        accounting_type="settlement",
        original_amount_minor=amount_minor,
        original_currency=currency,
        currency_exponent=exponent,
        usd_reporting_amount=usd,
        fx_rate=fx_rate,
        fx_rate_source=fx_source,
        fx_effective_at=fx_effective_at,
        occurred_at=occurred,
        received_at=submission.submitted_at or occurred,
        settled_at=occurred,
        approved_by=reviewer,
        safe_metadata={
            "billing_cycle": getattr(submission.subscription, "billing_cycle", None),
            "plan_code": submission.plan,
            "source_type": "payment_submission",
            "source_id": submission.id,
            "country_code": submission.country_code,
            "coupon_code": submission.coupon_code_applied,
        },
    )
    transaction, created = append_posting(
        posting, session=session, invoice_id=invoice_id, commit=False
    )
    if created:
        _audit(
            session=session,
            transaction=transaction,
            action="manual_payment_posted" if transaction.status == "posted" else "manual_payment_review_required",
            actor=reviewer,
            reason="Approved manual payment submission posted from immutable review snapshot",
            details={"source_type": "payment_submission", "source_id": str(submission.id)},
        )
    if commit:
        session.commit()
    return transaction, created


def post_reversal(
    original: PaymentTransaction,
    *,
    provider_event_id: str,
    provider_transaction_id: str,
    actor: str,
    reason: str,
    occurred_at: datetime | None = None,
    session=None,
    commit: bool = False,
):
    if original.status != "posted":
        raise ValueError("only a posted transaction can be reversed")
    when = occurred_at or datetime.now(timezone.utc)
    posting = LedgerPosting(
        tenant_id=original.tenant_id,
        subscription_id=original.subscription_id,
        provider=original.provider,
        provider_account=original.provider_account,
        provider_environment=original.provider_environment,
        provider_event_id=provider_event_id,
        provider_transaction_id=provider_transaction_id,
        event_type="ledger.reversal",
        accounting_type="reversal",
        original_amount_minor=-abs(original.original_amount_minor),
        original_currency=original.original_currency,
        currency_exponent=original.currency_exponent,
        usd_reporting_amount=-abs(exact_decimal(original.usd_reporting_amount, name="usd_reporting_amount")),
        fx_rate=exact_decimal(original.fx_rate, name="fx_rate") if original.fx_rate is not None else None,
        fx_rate_source=original.fx_rate_source,
        fx_effective_at=original.fx_effective_at,
        occurred_at=when,
        received_at=when,
        settled_at=when,
        reversal_of_id=original.id,
        created_by=actor,
        safe_metadata={"reason_code": "manual_correction"},
    )
    transaction, created = append_posting(posting, session=session, commit=False)
    if created:
        _audit(
            session=session or db.session, transaction=transaction,
            action="transaction_reversed", actor=actor, reason=reason,
            details={"source_id": original.id},
        )
    if commit:
        (session or db.session).commit()
    return transaction, created
