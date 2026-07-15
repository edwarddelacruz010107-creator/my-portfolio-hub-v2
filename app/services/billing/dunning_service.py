"""Idempotent retry/dunning evidence; provider calls remain in adapters."""
from __future__ import annotations

from datetime import timedelta

from app.extensions import db
from app.models.billing_center import BillingAttempt
from app.utils.datetime_utils import utc_now


MAX_ATTEMPTS = 4
BACKOFF_DAYS = (0, 1, 3, 7)


def schedule_attempt(*, tenant_id: int, subscription_id: int | None, invoice_id: int | None,
                     provider: str, amount_minor: int, currency: str, exponent: int,
                     idempotency_key: str, attempt_number: int = 1, commit: bool = False) -> BillingAttempt:
    existing = BillingAttempt.query.filter_by(idempotency_key=idempotency_key).first()
    if existing is not None:
        return existing
    if not 1 <= attempt_number <= MAX_ATTEMPTS:
        raise ValueError("attempt_number is outside the dunning policy")
    attempt = BillingAttempt(
        idempotency_key=idempotency_key,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        invoice_id=invoice_id,
        provider=str(provider).lower(),
        action="charge" if attempt_number == 1 else "retry",
        status="pending",
        attempt_number=attempt_number,
        original_amount_minor=amount_minor,
        original_currency=str(currency).upper(),
        currency_exponent=exponent,
        next_attempt_at=utc_now() + timedelta(days=BACKOFF_DAYS[attempt_number - 1]),
    )
    db.session.add(attempt)
    db.session.flush()
    if commit:
        db.session.commit()
    return attempt


def complete_attempt(attempt: BillingAttempt, *, succeeded: bool, provider_reference: str | None = None,
                     error_code: str | None = None, safe_message: str | None = None,
                     commit: bool = False) -> BillingAttempt:
    if attempt.status in {"succeeded", "dead"}:
        return attempt
    attempt.status = "succeeded" if succeeded else ("dead" if attempt.attempt_number >= MAX_ATTEMPTS else "failed")
    attempt.provider_reference = provider_reference
    attempt.error_code = error_code
    attempt.safe_message = safe_message
    attempt.completed_at = utc_now()
    db.session.add(attempt)
    if commit:
        db.session.commit()
    return attempt
