"""expand billing center with exact money, lifecycle, invoice, and retry records

Revision ID: 0059
Revises: 0058

This revision intentionally does not guess currencies for legacy float rows.
The bounded Phase 5 backfill service first stores each source value in
financial_float_backups, converts only rows with explicit currency provenance,
and sends all other rows to review_required.
"""
from alembic import op
import sqlalchemy as sa


revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("subscriptions", "payment_submissions"):
        op.add_column(table, sa.Column("amount_paid_minor", sa.BigInteger(), nullable=True))
        op.add_column(table, sa.Column("amount_paid_exponent", sa.SmallInteger(), nullable=True))
    op.add_column("subscriptions", sa.Column("amount_paid_currency", sa.String(length=3), nullable=True))

    op.create_check_constraint(
        "ck_subscriptions_exact_amount_complete",
        "subscriptions",
        "(amount_paid_minor IS NULL AND amount_paid_currency IS NULL AND amount_paid_exponent IS NULL) OR "
        "(amount_paid_minor IS NOT NULL AND amount_paid_currency IS NOT NULL AND amount_paid_exponent BETWEEN 0 AND 6)",
    )
    op.create_check_constraint(
        "ck_payment_submissions_exact_amount_complete",
        "payment_submissions",
        "(amount_paid_minor IS NULL AND amount_paid_exponent IS NULL) OR "
        "(amount_paid_minor IS NOT NULL AND amount_paid_exponent BETWEEN 0 AND 6)",
    )

    for column in (
        sa.Column("plan_version", sa.String(length=80), nullable=True),
        sa.Column("plan_snapshot", sa.JSON(), nullable=True),
        sa.Column("original_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("original_currency", sa.String(length=3), nullable=True),
        sa.Column("currency_exponent", sa.SmallInteger(), nullable=True),
    ):
        op.add_column("invoices", column)
    op.create_check_constraint(
        "ck_invoices_original_amount_complete",
        "invoices",
        "(original_amount_minor IS NULL AND original_currency IS NULL AND currency_exponent IS NULL) OR "
        "(original_amount_minor IS NOT NULL AND original_currency IS NOT NULL AND currency_exponent BETWEEN 0 AND 6)",
    )

    op.create_table(
        "billing_plan_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_code", sa.String(length=80), nullable=False),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entitlement_snapshot", sa.JSON(), nullable=False),
        sa.Column("price_schedule", sa.JSON(), nullable=False),
        sa.Column("provider_mappings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_code", "catalog_version", name="uq_billing_plan_catalog_version"),
    )
    op.create_index("ix_billing_plan_effective", "billing_plan_versions", ["plan_code", "effective_from", "effective_to"])

    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="1", nullable=False),
        sa.Column("line_type", sa.String(length=30), server_default="subscription", nullable=False),
        sa.Column("description", sa.String(length=240), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_metadata", sa.JSON(), nullable=False),
        sa.Column("discount_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_invoice_lines_quantity"),
        sa.CheckConstraint("amount >= 0", name="ck_invoice_lines_amount"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoice_lines_invoice_position", "invoice_lines", ["invoice_id", "position"])

    op.create_table(
        "invoice_status_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "idempotency_key", name="uq_invoice_status_idempotency"),
    )
    op.create_index("ix_invoice_status_invoice_created", "invoice_status_events", ["invoice_id", "created_at"])

    op.create_table(
        "subscription_status_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("provider_event_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_subscription_status_idempotency"),
    )
    op.create_index("ix_subscription_status_subscription_created", "subscription_status_events", ["subscription_id", "created_at"])
    op.create_index("ix_subscription_status_provider_event", "subscription_status_events", ["provider", "provider_event_id"])

    op.create_table(
        "billing_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("original_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("original_currency", sa.String(length=3), nullable=False),
        sa.Column("currency_exponent", sa.SmallInteger(), nullable=False),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("safe_message", sa.String(length=300), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_number > 0", name="ck_billing_attempt_number"),
        sa.CheckConstraint("currency_exponent >= 0 AND currency_exponent <= 6", name="ck_billing_attempt_exponent"),
        sa.CheckConstraint("original_amount_minor > 0", name="ck_billing_attempt_amount"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_billing_attempt_idempotency"),
    )
    op.create_index("ix_billing_attempt_due", "billing_attempts", ["status", "next_attempt_at"])
    op.create_index("ix_billing_attempt_tenant_created", "billing_attempts", ["tenant_id", "created_at"])

    op.create_table(
        "financial_float_backups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_table", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_column", sa.String(length=60), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("exponent", sa.SmallInteger(), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("disposition", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_table", "source_id", "source_column", name="uq_financial_float_backup_source"),
    )
    op.create_index("ix_financial_float_backup_disposition", "financial_float_backups", ["disposition", "created_at"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_billing_append_only_mutation() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'billing evidence rows are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in (
            "billing_plan_versions", "invoice_lines", "invoice_status_events",
            "subscription_status_events", "financial_float_backups",
        ):
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_billing_append_only_mutation()"
            )
        op.execute("""
            CREATE FUNCTION protect_issued_invoice_financials() RETURNS trigger AS $$
            BEGIN
              IF NEW.invoice_number IS DISTINCT FROM OLD.invoice_number
                 OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                 OR NEW.subscription_id IS DISTINCT FROM OLD.subscription_id
                 OR NEW.plan IS DISTINCT FROM OLD.plan
                 OR NEW.plan_version IS DISTINCT FROM OLD.plan_version
                 OR NEW.plan_snapshot IS DISTINCT FROM OLD.plan_snapshot
                 OR NEW.billing_cycle IS DISTINCT FROM OLD.billing_cycle
                 OR NEW.amount_subtotal IS DISTINCT FROM OLD.amount_subtotal
                 OR NEW.amount_discount IS DISTINCT FROM OLD.amount_discount
                 OR NEW.tax_rate IS DISTINCT FROM OLD.tax_rate
                 OR NEW.amount_tax IS DISTINCT FROM OLD.amount_tax
                 OR NEW.amount_total IS DISTINCT FROM OLD.amount_total
                 OR NEW.original_amount_minor IS DISTINCT FROM OLD.original_amount_minor
                 OR NEW.original_currency IS DISTINCT FROM OLD.original_currency
                 OR NEW.currency_exponent IS DISTINCT FROM OLD.currency_exponent
                 OR NEW.currency IS DISTINCT FROM OLD.currency
                 OR NEW.payment_provider IS DISTINCT FROM OLD.payment_provider
                 OR NEW.payment_reference IS DISTINCT FROM OLD.payment_reference THEN
                RAISE EXCEPTION 'issued invoice financial fields are immutable';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER trg_invoices_protect_financials BEFORE UPDATE ON invoices
            FOR EACH ROW EXECUTE FUNCTION protect_issued_invoice_financials();
        """)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_invoices_protect_financials ON invoices")
        op.execute("DROP FUNCTION IF EXISTS protect_issued_invoice_financials()")
        for table in (
            "financial_float_backups", "subscription_status_events", "invoice_status_events",
            "invoice_lines", "billing_plan_versions",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_billing_append_only_mutation()")

    op.drop_table("financial_float_backups")
    op.drop_table("billing_attempts")
    op.drop_table("subscription_status_events")
    op.drop_table("invoice_status_events")
    op.drop_table("invoice_lines")
    op.drop_table("billing_plan_versions")

    op.drop_constraint("ck_invoices_original_amount_complete", "invoices", type_="check")
    for name in ("currency_exponent", "original_currency", "original_amount_minor", "plan_snapshot", "plan_version"):
        op.drop_column("invoices", name)

    op.drop_constraint("ck_payment_submissions_exact_amount_complete", "payment_submissions", type_="check")
    op.drop_column("payment_submissions", "amount_paid_exponent")
    op.drop_column("payment_submissions", "amount_paid_minor")
    op.drop_constraint("ck_subscriptions_exact_amount_complete", "subscriptions", type_="check")
    op.drop_column("subscriptions", "amount_paid_currency")
    op.drop_column("subscriptions", "amount_paid_exponent")
    op.drop_column("subscriptions", "amount_paid_minor")
