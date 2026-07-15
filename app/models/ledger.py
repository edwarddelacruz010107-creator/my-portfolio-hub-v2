"""Append-only core financial-ledger models."""
from __future__ import annotations

import uuid

from sqlalchemy import event

from app.extensions import db
from app.utils.datetime_utils import utc_now


class PaymentTransaction(db.Model):
    """Immutable financial posting; corrections are linked new rows."""

    __tablename__ = "payment_transactions"
    __table_args__ = (
        db.UniqueConstraint(
            "provider", "provider_environment", "provider_event_id", "accounting_type",
            name="uq_payment_transactions_provider_event_type",
        ),
        db.UniqueConstraint(
            "provider", "provider_environment", "provider_transaction_id", "accounting_type",
            name="uq_payment_transactions_provider_transaction_type",
        ),
        db.CheckConstraint("provider IN ('dodo','paymongo','manual')", name="ck_payment_transactions_provider"),
        db.CheckConstraint(
            "provider_environment IN ('live','test')",
            name="ck_payment_transactions_environment",
        ),
        db.CheckConstraint(
            "accounting_type IN ('settlement','refund','reversal','adjustment','chargeback')",
            name="ck_payment_transactions_accounting_type",
        ),
        db.CheckConstraint(
            "status IN ('posted','review_required')", name="ck_payment_transactions_status"
        ),
        db.CheckConstraint(
            "currency_exponent >= 0 AND currency_exponent <= 6",
            name="ck_payment_transactions_currency_exponent",
        ),
        db.CheckConstraint(
            "(accounting_type = 'settlement' AND original_amount_minor > 0) OR "
            "(accounting_type IN ('refund','reversal','chargeback') AND original_amount_minor < 0) OR "
            "(accounting_type = 'adjustment' AND original_amount_minor <> 0)",
            name="ck_payment_transactions_amount_sign",
        ),
        db.CheckConstraint(
            "status = 'review_required' OR usd_reporting_amount IS NOT NULL",
            name="ck_payment_transactions_posted_usd",
        ),
        db.CheckConstraint(
            "usd_reporting_amount IS NULL OR "
            "(accounting_type = 'settlement' AND usd_reporting_amount > 0) OR "
            "(accounting_type IN ('refund','reversal','chargeback') AND usd_reporting_amount < 0) OR "
            "(accounting_type = 'adjustment' AND usd_reporting_amount <> 0)",
            name="ck_payment_transactions_usd_sign",
        ),
        db.Index("ix_payment_transactions_recorded_provider", "recorded_at", "provider"),
        db.Index("ix_payment_transactions_occurred_status", "occurred_at", "status"),
        db.Index("ix_payment_transactions_tenant_recorded", "tenant_id", "recorded_at"),
        db.Index("ix_payment_transactions_subscription_recorded", "subscription_id", "recorded_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)

    provider = db.Column(db.String(20), nullable=False)
    provider_account = db.Column(db.String(120), nullable=False, default="default")
    provider_environment = db.Column(db.String(20), nullable=False, default="live")
    provider_event_id = db.Column(db.String(255), nullable=False)
    provider_transaction_id = db.Column(db.String(255), nullable=False)
    event_type = db.Column(db.String(100), nullable=False)
    accounting_type = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(30), nullable=False)

    original_amount_minor = db.Column(db.BigInteger, nullable=False)
    original_currency = db.Column(db.String(3), nullable=False)
    currency_exponent = db.Column(db.SmallInteger, nullable=False)
    usd_reporting_amount = db.Column(db.Numeric(20, 8), nullable=True)
    fx_rate = db.Column(db.Numeric(28, 12), nullable=True)
    fx_rate_source = db.Column(db.String(120), nullable=True)
    fx_effective_at = db.Column(db.DateTime(timezone=True), nullable=True)

    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    received_at = db.Column(db.DateTime(timezone=True), nullable=False)
    settled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    reversal_of_id = db.Column(db.String(36), db.ForeignKey("payment_transactions.id", ondelete="RESTRICT"), nullable=True)
    safe_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_by = db.Column(db.String(120), nullable=True)
    approved_by = db.Column(db.String(120), nullable=True)

    reversal_of = db.relationship("PaymentTransaction", remote_side=[id], lazy="joined")


class FinancialAuditEvent(db.Model):
    """Immutable reason/actor evidence for corrections and operational review."""

    __tablename__ = "financial_audit_events"
    __table_args__ = (
        db.Index("ix_financial_audit_transaction_created", "transaction_id", "created_at"),
        db.Index("ix_financial_audit_action_created", "action", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    transaction_id = db.Column(
        db.String(36), db.ForeignKey("payment_transactions.id", ondelete="RESTRICT"), nullable=True
    )
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True)
    action = db.Column(db.String(60), nullable=False)
    actor = db.Column(db.String(120), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    safe_details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class LedgerBackfillItem(db.Model):
    """Idempotent disposition for one legacy financial source row."""

    __tablename__ = "ledger_backfill_items"
    __table_args__ = (
        db.UniqueConstraint("source_type", "source_id", name="uq_ledger_backfill_source"),
        db.CheckConstraint(
            "disposition IN ('posted','unreconciled','skipped')",
            name="ck_ledger_backfill_disposition",
        ),
        db.Index("ix_ledger_backfill_disposition", "disposition", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_type = db.Column(db.String(50), nullable=False)
    source_id = db.Column(db.String(255), nullable=False)
    source_fingerprint = db.Column(db.String(64), nullable=False)
    disposition = db.Column(db.String(30), nullable=False)
    reason = db.Column(db.Text, nullable=False, default="")
    transaction_id = db.Column(
        db.String(36), db.ForeignKey("payment_transactions.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_ledger_mutation(_mapper, _connection, target):
    raise RuntimeError(
        f"{target.__class__.__name__} is append-only; post a linked correction instead"
    )


for _model in (PaymentTransaction, FinancialAuditEvent):
    event.listen(_model, "before_update", _reject_ledger_mutation)
    event.listen(_model, "before_delete", _reject_ledger_mutation)
