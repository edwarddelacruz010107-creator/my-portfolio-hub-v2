"""add append-only payment ledger and reconciliation registry

Revision ID: 0057
Revises: 0056
"""
from alembic import op
import sqlalchemy as sa


revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "subscriptions",
        sa.Column("provider_state_occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_subscriptions_provider_state_occurred_at",
        "subscriptions",
        ["provider_state_occurred_at"],
    )
    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_account", sa.String(length=120), server_default="default", nullable=False),
        sa.Column("provider_environment", sa.String(length=20), server_default="live", nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("accounting_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("original_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("original_currency", sa.String(length=3), nullable=False),
        sa.Column("currency_exponent", sa.SmallInteger(), nullable=False),
        sa.Column("usd_reporting_amount", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("fx_rate", sa.Numeric(precision=28, scale=12), nullable=True),
        sa.Column("fx_rate_source", sa.String(length=120), nullable=True),
        sa.Column("fx_effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversal_of_id", sa.String(length=36), nullable=True),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.CheckConstraint("provider IN ('dodo','paymongo','manual')", name="ck_payment_transactions_provider"),
        sa.CheckConstraint("provider_environment IN ('live','test')", name="ck_payment_transactions_environment"),
        sa.CheckConstraint("accounting_type IN ('settlement','refund','reversal','adjustment','chargeback')", name="ck_payment_transactions_accounting_type"),
        sa.CheckConstraint("status IN ('posted','review_required')", name="ck_payment_transactions_status"),
        sa.CheckConstraint("currency_exponent >= 0 AND currency_exponent <= 6", name="ck_payment_transactions_currency_exponent"),
        sa.CheckConstraint("(accounting_type = 'settlement' AND original_amount_minor > 0) OR (accounting_type IN ('refund','reversal','chargeback') AND original_amount_minor < 0) OR (accounting_type = 'adjustment' AND original_amount_minor <> 0)", name="ck_payment_transactions_amount_sign"),
        sa.CheckConstraint("status = 'review_required' OR usd_reporting_amount IS NOT NULL", name="ck_payment_transactions_posted_usd"),
        sa.CheckConstraint("usd_reporting_amount IS NULL OR (accounting_type = 'settlement' AND usd_reporting_amount > 0) OR (accounting_type IN ('refund','reversal','chargeback') AND usd_reporting_amount < 0) OR (accounting_type = 'adjustment' AND usd_reporting_amount <> 0)", name="ck_payment_transactions_usd_sign"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reversal_of_id"], ["payment_transactions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_environment", "provider_event_id", "accounting_type", name="uq_payment_transactions_provider_event_type"),
        sa.UniqueConstraint("provider", "provider_environment", "provider_transaction_id", "accounting_type", name="uq_payment_transactions_provider_transaction_type"),
    )
    op.create_index("ix_payment_transactions_recorded_provider", "payment_transactions", ["recorded_at", "provider"])
    op.create_index("ix_payment_transactions_occurred_status", "payment_transactions", ["occurred_at", "status"])
    op.create_index("ix_payment_transactions_tenant_recorded", "payment_transactions", ["tenant_id", "recorded_at"])
    op.create_index("ix_payment_transactions_subscription_recorded", "payment_transactions", ["subscription_id", "recorded_at"])

    op.create_table(
        "financial_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("transaction_id", sa.String(length=36), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("safe_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("disposition IN ('posted','unreconciled','skipped')", name="ck_ledger_backfill_disposition"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["transaction_id"], ["payment_transactions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_financial_audit_transaction_created", "financial_audit_events", ["transaction_id", "created_at"])
    op.create_index("ix_financial_audit_action_created", "financial_audit_events", ["action", "created_at"])

    op.create_table(
        "ledger_backfill_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("disposition", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column("transaction_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["transaction_id"], ["payment_transactions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_type", "source_id", name="uq_ledger_backfill_source"),
    )
    op.create_index("ix_ledger_backfill_disposition", "ledger_backfill_items", ["disposition", "created_at"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_financial_ledger_mutation() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'financial ledger rows are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in ("payment_transactions", "financial_audit_events"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_financial_ledger_mutation()"
            )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        for table in ("financial_audit_events", "payment_transactions"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_financial_ledger_mutation()")
    op.drop_table("ledger_backfill_items")
    op.drop_table("financial_audit_events")
    op.drop_table("payment_transactions")
    op.drop_index("ix_subscriptions_provider_state_occurred_at", table_name="subscriptions")
    op.drop_column("subscriptions", "provider_state_occurred_at")
