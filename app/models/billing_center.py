"""Phase 5 billing-center models.

All rows here are expand-only additions.  Legacy subscription and submission
float columns remain during the rollback window, while new writes use explicit
minor units and original currency.
"""
from __future__ import annotations

import uuid

from sqlalchemy import event, inspect

from app.extensions import db
from app.models.core import Invoice
from app.utils.datetime_utils import utc_now


class BillingPlanVersion(db.Model):
    __tablename__ = "billing_plan_versions"
    __table_args__ = (
        db.UniqueConstraint("plan_code", "catalog_version", name="uq_billing_plan_catalog_version"),
        db.Index("ix_billing_plan_effective", "plan_code", "effective_from", "effective_to"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_code = db.Column(db.String(80), nullable=False)
    catalog_version = db.Column(db.String(80), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    effective_from = db.Column(db.DateTime(timezone=True), nullable=True)
    effective_to = db.Column(db.DateTime(timezone=True), nullable=True)
    entitlement_snapshot = db.Column(db.JSON, nullable=False, default=dict)
    price_schedule = db.Column(db.JSON, nullable=False, default=dict)
    provider_mappings = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class InvoiceLine(db.Model):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_invoice_lines_quantity"),
        db.CheckConstraint("amount >= 0", name="ck_invoice_lines_amount"),
        db.Index("ix_invoice_lines_invoice_position", "invoice_id", "position"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=1)
    line_type = db.Column(db.String(30), nullable=False, default="subscription")
    description = db.Column(db.String(240), nullable=False)
    quantity = db.Column(db.Numeric(18, 6), nullable=False)
    unit_amount = db.Column(db.Numeric(20, 6), nullable=False)
    amount = db.Column(db.Numeric(20, 6), nullable=False)
    tax_metadata = db.Column(db.JSON, nullable=False, default=dict)
    discount_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    invoice = db.relationship("Invoice", lazy="joined")


class InvoiceStatusEvent(db.Model):
    __tablename__ = "invoice_status_events"
    __table_args__ = (
        db.UniqueConstraint("invoice_id", "idempotency_key", name="uq_invoice_status_idempotency"),
        db.Index("ix_invoice_status_invoice_created", "invoice_id", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False)
    from_status = db.Column(db.String(30), nullable=True)
    to_status = db.Column(db.String(30), nullable=False)
    actor = db.Column(db.String(120), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    idempotency_key = db.Column(db.String(160), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class SubscriptionStatusEvent(db.Model):
    __tablename__ = "subscription_status_events"
    __table_args__ = (
        db.UniqueConstraint("idempotency_key", name="uq_subscription_status_idempotency"),
        db.Index("ix_subscription_status_subscription_created", "subscription_id", "created_at"),
        db.Index("ix_subscription_status_provider_event", "provider", "provider_event_id"),
        db.Index("ix_subscription_status_tenant_occurred", "tenant_id", "occurred_at", "to_status"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    from_status = db.Column(db.String(30), nullable=True)
    to_status = db.Column(db.String(30), nullable=False)
    actor = db.Column(db.String(120), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    provider = db.Column(db.String(30), nullable=True)
    provider_event_id = db.Column(db.String(255), nullable=True)
    idempotency_key = db.Column(db.String(160), nullable=False)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class BillingAttempt(db.Model):
    __tablename__ = "billing_attempts"
    __table_args__ = (
        db.UniqueConstraint("idempotency_key", name="uq_billing_attempt_idempotency"),
        db.CheckConstraint("attempt_number > 0", name="ck_billing_attempt_number"),
        db.CheckConstraint("currency_exponent >= 0 AND currency_exponent <= 6", name="ck_billing_attempt_exponent"),
        db.CheckConstraint("original_amount_minor > 0", name="ck_billing_attempt_amount"),
        db.Index("ix_billing_attempt_due", "status", "next_attempt_at"),
        db.Index("ix_billing_attempt_tenant_created", "tenant_id", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    idempotency_key = db.Column(db.String(160), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    provider = db.Column(db.String(30), nullable=False)
    action = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    attempt_number = db.Column(db.Integer, nullable=False, default=1)
    original_amount_minor = db.Column(db.BigInteger, nullable=False)
    original_currency = db.Column(db.String(3), nullable=False)
    currency_exponent = db.Column(db.SmallInteger, nullable=False)
    provider_reference = db.Column(db.String(255), nullable=True)
    error_code = db.Column(db.String(80), nullable=True)
    safe_message = db.Column(db.String(300), nullable=True)
    next_attempt_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class FinancialFloatBackup(db.Model):
    __tablename__ = "financial_float_backups"
    __table_args__ = (
        db.UniqueConstraint("source_table", "source_id", "source_column", name="uq_financial_float_backup_source"),
        db.Index("ix_financial_float_backup_disposition", "disposition", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_table = db.Column(db.String(60), nullable=False)
    source_id = db.Column(db.String(64), nullable=False)
    source_column = db.Column(db.String(60), nullable=False)
    original_text = db.Column(db.Text, nullable=False)
    currency = db.Column(db.String(3), nullable=True)
    exponent = db.Column(db.SmallInteger, nullable=True)
    amount_minor = db.Column(db.BigInteger, nullable=True)
    disposition = db.Column(db.String(30), nullable=False)
    reason = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_append_only(_mapper, _connection, target):
    raise RuntimeError(f"{target.__class__.__name__} is append-only")


for _append_only_model in (
    BillingPlanVersion,
    InvoiceLine,
    InvoiceStatusEvent,
    SubscriptionStatusEvent,
    FinancialFloatBackup,
):
    event.listen(_append_only_model, "before_update", _reject_append_only)
    event.listen(_append_only_model, "before_delete", _reject_append_only)


_IMMUTABLE_INVOICE_FIELDS = frozenset({
    "invoice_number", "tenant_id", "subscription_id", "discount_redemption_id",
    "plan", "plan_version", "plan_snapshot", "billing_cycle",
    "amount_subtotal", "amount_discount", "tax_rate", "amount_tax", "amount_total",
    "original_amount_minor", "original_currency", "currency_exponent", "currency",
    "coupon_code", "payment_method", "payment_provider", "payment_reference", "issued_at",
})


def _protect_invoice_financials(_mapper, _connection, target):
    state = inspect(target)
    changed = [name for name in _IMMUTABLE_INVOICE_FIELDS if state.attrs[name].history.has_changes()]
    if changed:
        raise RuntimeError("issued invoice financial fields are immutable: " + ", ".join(sorted(changed)))


event.listen(Invoice, "before_update", _protect_invoice_financials)
