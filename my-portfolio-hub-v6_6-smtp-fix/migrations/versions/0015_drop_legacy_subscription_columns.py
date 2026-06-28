"""Drop legacy subscription columns removed in v3.4 ORM

Revision ID: 0015_drop_legacy_subscription_columns
Revises: 0014_manual_payment_methods
Create Date: 2026-06-06 20:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '0015_drop_legacy_subscription_columns'
down_revision = '0014_manual_payment_methods'
branch_labels = None
depends_on = None

_LEGACY_COLUMNS = (
    'payment_status',
    'license_key',
    'payment_reference',
    'payment_proof',
)

_INDEXES_ON_LEGACY = (
    'ix_subscriptions_license_key',
    'uq_subscriptions_license_key',
)


def _existing_columns(connection, table: str) -> set[str]:
    rows = connection.execute(sa.text(f'PRAGMA table_info({table})')).fetchall()
    return {row[1] for row in rows}


def _existing_indexes(connection, table: str) -> set[str]:
    rows = connection.execute(sa.text(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t"
    ), {'t': table}).fetchall()
    return {row[0] for row in rows}


def upgrade():
    connection = op.get_bind()
    indexes = _existing_indexes(connection, 'subscriptions')

    for idx in _INDEXES_ON_LEGACY:
        if idx in indexes:
            connection.execute(sa.text(f'DROP INDEX IF EXISTS {idx}'))

    for col in _LEGACY_COLUMNS:
        if col not in _existing_columns(connection, 'subscriptions'):
            continue
        connection.execute(sa.text(f'ALTER TABLE subscriptions DROP COLUMN {col}'))


def downgrade():
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_status', sa.String(20), nullable=False, server_default='unpaid'))
        batch_op.add_column(sa.Column('license_key', sa.String(64), nullable=True))
        batch_op.add_column(sa.Column('payment_reference', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('payment_proof', sa.String(255), nullable=True))
