"""Resumable provenance-first migration from legacy payment records."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json

from app.extensions import db
from app.models import LedgerBackfillItem, PaymentSubmission, Subscription
from app.services.ledger.domain import LedgerPosting, major_from_minor
from app.services.ledger.posting_service import append_posting, post_manual_submission


@dataclass
class BackfillReport:
    inspected: int = 0
    eligible: int = 0
    posted: int = 0
    unreconciled: int = 0
    already_processed: int = 0

    def to_dict(self):
        return asdict(self)


def _fingerprint(source_type: str, source_id, values: dict) -> str:
    payload = json.dumps([source_type, str(source_id), values], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_disposition(*, source_type, source_id, fingerprint, disposition, reason, transaction_id=None):
    row = LedgerBackfillItem(
        source_type=source_type, source_id=str(source_id), source_fingerprint=fingerprint,
        disposition=disposition, reason=reason, transaction_id=transaction_id,
    )
    db.session.add(row)
    db.session.flush()


def run_legacy_backfill(*, apply_changes: bool = False) -> BackfillReport:
    report = BackfillReport()
    approved = PaymentSubmission.query.filter_by(status="approved").order_by(PaymentSubmission.id).all()
    subscriptions = Subscription.query.filter(
        Subscription.status.in_(["active", "expired", "cancelled"])
    ).order_by(Subscription.id).all()

    for submission in approved:
        report.inspected += 1
        source_type, source_id = "payment_submission", submission.id
        if LedgerBackfillItem.query.filter_by(source_type=source_type, source_id=str(source_id)).first():
            report.already_processed += 1
            continue
        values = {
            "tenant_id": submission.tenant_id, "amount_paid": submission.amount_paid,
            "amount_usd": submission.amount_usd, "currency": submission.currency_code,
            "exchange_rate": str(submission.exchange_rate) if submission.exchange_rate is not None else None,
            "reviewed_at": submission.reviewed_at,
        }
        fingerprint = _fingerprint(source_type, source_id, values)
        complete = all((submission.tenant_id, submission.amount_paid is not None, submission.currency_code, submission.reviewed_at))
        currency = str(submission.currency_code or "").upper()
        fx_complete = currency == "USD" or all((submission.amount_usd is not None, submission.exchange_rate is not None))
        if not (complete and fx_complete):
            report.unreconciled += 1
            if apply_changes:
                _record_disposition(source_type=source_type, source_id=source_id, fingerprint=fingerprint, disposition="unreconciled", reason="Missing amount/currency/review time or reproducible FX snapshot")
            continue
        report.eligible += 1
        if apply_changes:
            transaction, _ = post_manual_submission(
                submission, reviewer=submission.reviewed_by or "legacy-backfill",
                session=db.session, commit=False,
            )
            _record_disposition(source_type=source_type, source_id=source_id, fingerprint=fingerprint, disposition="posted", reason="Approved manual payment with complete immutable snapshot", transaction_id=transaction.id)
            report.posted += 1

    for subscription in subscriptions:
        provider = str(subscription.payment_provider or "").lower()
        transaction_id = None
        if provider == "dodo" or subscription.dodo_payment_id:
            provider, transaction_id = "dodo", subscription.dodo_payment_id
        elif provider == "paymongo" or subscription.paymongo_payment_id:
            provider, transaction_id = "paymongo", subscription.paymongo_payment_id
        if not transaction_id:
            continue
        report.inspected += 1
        source_type, source_id = "subscription_payment", subscription.id
        if LedgerBackfillItem.query.filter_by(source_type=source_type, source_id=str(source_id)).first():
            report.already_processed += 1
            continue
        values = {
            "tenant_id": subscription.tenant_id, "transaction_id": transaction_id,
            "amount": subscription.amount_paid, "currency": subscription.provider_currency,
            "started_at": subscription.started_at, "provider": provider,
        }
        fingerprint = _fingerprint(source_type, source_id, values)
        currency = str(subscription.provider_currency or "").upper()
        complete = all((subscription.tenant_id, transaction_id, subscription.amount_paid, currency, subscription.started_at))
        if not complete or currency != "USD":
            report.unreconciled += 1
            if apply_changes:
                _record_disposition(source_type=source_type, source_id=source_id, fingerprint=fingerprint, disposition="unreconciled", reason="Provider row lacks complete provenance or reproducible historical USD conversion")
            continue
        report.eligible += 1
        if apply_changes:
            amount = Decimal(str(subscription.amount_paid))
            amount_minor = int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            occurred = subscription.started_at
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            posting = LedgerPosting(
                tenant_id=subscription.tenant_id, subscription_id=subscription.id,
                provider=provider, provider_event_id=f"legacy-subscription:{subscription.id}",
                provider_transaction_id=str(transaction_id), event_type="legacy.settlement",
                accounting_type="settlement", original_amount_minor=amount_minor,
                original_currency="USD", currency_exponent=2,
                usd_reporting_amount=major_from_minor(amount_minor, 2),
                occurred_at=occurred, received_at=occurred, settled_at=occurred,
                created_by="legacy-backfill",
                safe_metadata={"source_type": source_type, "source_id": source_id, "billing_cycle": subscription.billing_cycle, "plan_code": subscription.plan},
            )
            transaction, _ = append_posting(posting, session=db.session, commit=False)
            _record_disposition(source_type=source_type, source_id=source_id, fingerprint=fingerprint, disposition="posted", reason="Provider settlement with complete USD provenance", transaction_id=transaction.id)
            report.posted += 1

    if apply_changes:
        db.session.commit()
    else:
        db.session.rollback()
    return report
