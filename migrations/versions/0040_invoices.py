"""
0040 — Invoice accounting record (v7.7 stabilization)

CONTEXT
───────
There was no invoice subsystem anywhere in this codebase prior to this
migration — verified before writing anything: no Invoice model, no
invoice service, no prior Alembic migration. The only trace was a raw
`invoices` table defined in migrations/upgrade_postgresql.sql (a
standalone legacy v4.1 script meant to be run manually via
`psql $DATABASE_URL -f upgrade_postgresql.sql`, never wired into
`flask db upgrade`). Nothing in current app code reads or writes that
table, and it has no FK relationship to this migration's table — this is
a fresh build, not a continuation of that legacy schema.

Adds (CORE bind, same as 0037-0039):
  invoices table
    Immutable-by-convention accounting record. See the Invoice model
    docstring in app/models/core.py for the full immutability contract
    (corrections go through void, never UPDATE of financial columns).

  invoice_number_seq (PostgreSQL only)
    Backs genuinely sequential invoice numbering — a real bookkeeping
    requirement, unlike Subscription's existing license-key generator
    which is intentionally random/non-sequential. SQLite (dev/test) has
    no native sequence object; invoice_service.py falls back to a
    non-atomic max()+1 there, gated the same way this repo already gates
    other Postgres-only behav0r (see discount_repository.get_for_update
    from migration 0038/0039's era — same db.engine.dialect.name pattern).

SAFE: additive only. No existing columns/tables altered or dropped.
Chains onto 0039 (the current single head, confirmed via
tests/test_migration_chain_integrity.py before writing this file).
"""
from alembic import op
import sqlalchemy as sa

revision      = '0040_invoices'
down_revision = '0039_backfill_paymongo_payment_method'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'invoices',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('invoice_number', sa.String(30), nullable=False),
        sa.Column('tenant_id', sa.Integer,
                   sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                   nullable=False, index=True),
        sa.Column('subscription_id', sa.Integer,
                   sa.ForeignKey('subscriptions.id', ondelete='SET NULL'),
                   nullable=True, index=True),
        sa.Column('discount_redemption_id', sa.Integer,
                   sa.ForeignKey('discount_redemptions.id', ondelete='SET NULL'),
                   nullable=True),
        sa.Column('plan', sa.String(50), nullable=False),
        sa.Column('billing_cycle', sa.String(20), nullable=False, server_default='monthly'),
        sa.Column('amount_subtotal', sa.Numeric(10, 2), nullable=False),
        sa.Column('amount_discount', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('tax_rate', sa.Numeric(5, 4), nullable=False, server_default='0'),
        sa.Column('amount_tax', sa.Numeric(10, 2), nullable=False, server_default='0'),
        sa.Column('amount_total', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(10), nullable=False, server_default='PHP'),
        sa.Column('coupon_code', sa.String(100), nullable=True),
        sa.Column('payment_method', sa.String(100), nullable=False, server_default=''),
        sa.Column('payment_provider', sa.String(30), nullable=False, server_default=''),
        sa.Column('payment_reference', sa.String(255), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='issued'),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('void_reason', sa.Text, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.UniqueConstraint('invoice_number', name='uq_invoices_invoice_number'),
    )
    op.create_index('ix_invoices_tenant_issued', 'invoices', ['tenant_id', 'issued_at'])
    op.create_index('ix_invoices_invoice_number', 'invoices', ['invoice_number'])

    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        # Idempotency backstop — see uq_discount_redemptions_subscription in
        # 0038 for the same rationale (webhook retry / superadmin resync
        # racing the same subscription+payment_reference).
        op.execute(sa.text(
            """
            CREATE UNIQUE INDEX uq_invoices_subscription_payment_ref
            ON invoices (subscription_id, payment_reference)
            WHERE payment_reference IS NOT NULL
            """
        ))
        op.execute(sa.text("CREATE SEQUENCE IF NOT EXISTS invoice_number_seq START 1"))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        op.execute(sa.text("DROP SEQUENCE IF EXISTS invoice_number_seq"))
        op.execute(sa.text("DROP INDEX IF EXISTS uq_invoices_subscription_payment_ref"))
    op.drop_index('ix_invoices_invoice_number', table_name='invoices')
    op.drop_index('ix_invoices_tenant_issued', table_name='invoices')
    op.drop_table('invoices')
