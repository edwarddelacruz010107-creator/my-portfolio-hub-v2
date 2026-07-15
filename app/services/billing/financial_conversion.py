"""Bounded, resumable legacy-float conversion with immutable source backup."""
from __future__ import annotations

from app.extensions import db
from app.models import PaymentSubmission, Subscription
from app.models.billing_center import FinancialFloatBackup
from app.services.billing.money import legacy_float_to_minor, normalize_currency
from sqlalchemy import String, cast


def set_exact_paid_amount(record, *, amount, currency: str, exponent: int = 2) -> None:
    """Dual-write legacy float plus exact minor units during rollback window."""
    code = normalize_currency(currency)
    minor = legacy_float_to_minor(float(amount), exponent)
    record.amount_paid = float(amount)
    record.amount_paid_minor = minor
    record.amount_paid_exponent = exponent
    if isinstance(record, Subscription):
        record.amount_paid_currency = code
        record.provider_currency = record.provider_currency or code
    elif isinstance(record, PaymentSubmission):
        record.currency_code = code
    else:
        raise TypeError("unsupported billing record")


def _backup(source_table: str, source_id, original, currency, *, exponent=2):
    existing = FinancialFloatBackup.query.filter_by(
        source_table=source_table,
        source_id=str(source_id),
        source_column="amount_paid",
    ).first()
    if existing is not None:
        return existing
    try:
        code = normalize_currency(currency)
        minor = legacy_float_to_minor(float(original), exponent)
        disposition = "converted"
        reason = "explicit stored currency; Decimal(str(value)) rounded half-up to exponent"
    except Exception:
        code = None
        minor = None
        disposition = "review_required"
        reason = "currency or numeric provenance is insufficient; no value was inferred"
    row = FinancialFloatBackup(
        source_table=source_table,
        source_id=str(source_id),
        source_column="amount_paid",
        original_text=str(original),
        currency=code,
        exponent=exponent if code else None,
        amount_minor=minor,
        disposition=disposition,
        reason=reason,
    )
    db.session.add(row)
    return row


def backfill_financial_floats(*, batch_size: int = 200, commit: bool = True) -> dict:
    batch_size = min(max(int(batch_size), 1), 1000)
    converted = review_required = 0

    sources = (
        ("subscriptions", Subscription, Subscription.provider_currency),
        ("payment_submissions", PaymentSubmission, PaymentSubmission.currency_code),
    )
    for table_name, model, currency_column in sources:
        rows = (
            model.query
            .filter(model.amount_paid_minor.is_(None))
            .filter(~db.session.query(FinancialFloatBackup.id).filter(
                FinancialFloatBackup.source_table == table_name,
                FinancialFloatBackup.source_id == cast(model.id, String),
                FinancialFloatBackup.source_column == "amount_paid",
            ).exists())
            .order_by(model.id.asc())
            .limit(batch_size)
            .all()
        )
        for record in rows:
            backup = _backup(table_name, record.id, record.amount_paid, getattr(record, currency_column.key))
            if backup.disposition == "converted":
                record.amount_paid_minor = backup.amount_minor
                record.amount_paid_exponent = backup.exponent
                if isinstance(record, Subscription):
                    record.amount_paid_currency = backup.currency
                db.session.add(record)
                converted += 1
            else:
                review_required += 1
    db.session.flush()
    if commit:
        db.session.commit()
    return {"converted": converted, "review_required": review_required}
