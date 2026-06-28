"""billing_v3.3 - Add license_key + payment_status to subscriptions, subscription_id FK to payment_submissions

Revision ID: 0004_billing_v3_3
Revises: 0010_backfill_default_tenant
Create Date: 2026-06-19

Changes
-------
subscriptions
  + license_key        VARCHAR(64)  UNIQUE  NULLABLE
  + payment_status     VARCHAR(20)  NOT NULL DEFAULT 'unpaid'
  + INDEX ix_subscriptions_tenant_status (tenant_id, status)

payment_submissions
  + subscription_id    INTEGER REFERENCES subscriptions(id)  NULLABLE  INDEX
  + INDEX ix_payment_submissions_tenant_status (tenant_id, status)

Data migration
  - Backfill payment_status = 'paid' for every existing active subscription.
  - Backfill payment_status = 'pending' for pending subs that already have a proof file.

Rollback
  - Down migration removes all added columns (destructive - back up first).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '0004_billing_v3_3'
down_revision = '0010_backfill_default_tenant'
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name):
    return inspector.has_table(table_name)


def _column_exists(inspector, table_name, column_name):
    if not _table_exists(inspector, table_name):
        return False
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    # subscriptions table
    if _table_exists(inspector, 'subscriptions'):
        with op.batch_alter_table('subscriptions', schema=None) as batch_op:
            if not _column_exists(inspector, 'subscriptions', 'license_key'):
                batch_op.add_column(
                    sa.Column('license_key', sa.String(length=64), nullable=True)
                )
            if not _column_exists(inspector, 'subscriptions', 'payment_status'):
                batch_op.add_column(
                    sa.Column(
                        'payment_status',
                        sa.String(length=20),
                        nullable=False,
                        server_default='unpaid',
                    )
                )
            try:
                batch_op.create_unique_constraint(
                    'uq_subscriptions_license_key', ['license_key']
                )
            except Exception:
                pass
            try:
                batch_op.create_index(
                    'ix_subscriptions_tenant_status', ['tenant_id', 'status']
                )
            except Exception:
                pass

    # payment_submissions table
    if _table_exists(inspector, 'payment_submissions'):
        with op.batch_alter_table('payment_submissions', schema=None) as batch_op:
            if not _column_exists(inspector, 'payment_submissions', 'subscription_id'):
                batch_op.add_column(
                    sa.Column('subscription_id', sa.Integer(), nullable=True)
                )
            try:
                batch_op.create_foreign_key(
                    'fk_payment_submissions_subscription_id',
                    'subscriptions',
                    ['subscription_id'], ['id'],
                )
            except Exception:
                pass
            try:
                batch_op.create_index(
                    'ix_payment_subscriptions_subscription_id', ['subscription_id']
                )
            except Exception:
                pass
            try:
                batch_op.create_index(
                    'ix_payment_submissions_tenant_status', ['tenant_id', 'status']
                )
            except Exception:
                pass

    # Data backfill
    if _table_exists(inspector, 'subscriptions'):
        try:
            op.execute(
                "UPDATE subscriptions SET payment_status = 'paid' WHERE status = 'active'"
            )
        except Exception:
            pass
        try:
            op.execute(
                """
                UPDATE subscriptions
                SET payment_status = 'pending'
                WHERE status = 'pending'
                  AND payment_proof IS NOT NULL
                  AND payment_proof != ''
                """
            )
        except Exception:
            pass


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    if _table_exists(inspector, 'payment_submissions'):
        with op.batch_alter_table('payment_submissions', schema=None) as batch_op:
            try:
                batch_op.drop_index('ix_payment_submissions_tenant_status')
            except Exception:
                pass
            try:
                batch_op.drop_index('ix_payment_subscriptions_subscription_id')
            except Exception:
                pass
            try:
                batch_op.drop_constraint(
                    'fk_payment_submissions_subscription_id', type_='foreignkey'
                )
            except Exception:
                pass
            if _column_exists(inspector, 'payment_submissions', 'subscription_id'):
                batch_op.drop_column('subscription_id')

    if _table_exists(inspector, 'subscriptions'):
        with op.batch_alter_table('subscriptions', schema=None) as batch_op:
            try:
                batch_op.drop_index('ix_subscriptions_tenant_status')
            except Exception:
                pass
            try:
                batch_op.drop_constraint('uq_subscriptions_license_key', type_='unique')
            except Exception:
                pass
            if _column_exists(inspector, 'subscriptions', 'license_key'):
                batch_op.drop_column('license_key')
            if _column_exists(inspector, 'subscriptions', 'payment_status'):
                batch_op.drop_column('payment_status')