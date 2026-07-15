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
    inspector = sa.inspect(connection)
    if not inspector.has_table('subscriptions'):
        return

    indexes = {index['name'] for index in inspector.get_indexes('subscriptions')}
    unique_constraints = {
        constraint['name']
        for constraint in inspector.get_unique_constraints('subscriptions')
        if constraint.get('name')
    }
    with op.batch_alter_table('subscriptions') as batch:
        for name in _INDEXES_ON_LEGACY:
            if name in indexes:
                batch.drop_index(name)
            elif name in unique_constraints:
                batch.drop_constraint(name, type_='unique')

    columns = {column['name'] for column in sa.inspect(connection).get_columns('subscriptions')}
    with op.batch_alter_table('subscriptions') as batch:
        for column_name in _LEGACY_COLUMNS:
            if column_name in columns:
                batch.drop_column(column_name)


def downgrade():
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_status', sa.String(20), nullable=False, server_default='unpaid'))
        batch_op.add_column(sa.Column('license_key', sa.String(64), nullable=True))
        batch_op.add_column(sa.Column('payment_reference', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('payment_proof', sa.String(255), nullable=True))
