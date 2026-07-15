"""Add PayMongo subscription fields to Subscription model

Revision ID: 0011_add_paymongo_subscription
Revises: 0010_security_reset
Create Date: 2026-06-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0011_add_paymongo_subscription'
down_revision = '0010_security_reset'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column['name'] for column in inspector.get_columns('subscriptions')}
    additions = (
        sa.Column('billing_cycle', sa.String(20), nullable=True, server_default='monthly'),
        sa.Column('paymongo_subscription_id', sa.String(255), nullable=True),
        sa.Column('paymongo_customer_id', sa.String(255), nullable=True),
    )
    with op.batch_alter_table('subscriptions') as batch:
        for column in additions:
            if column.name not in columns:
                batch.add_column(column)
    indexes = {index['name'] for index in sa.inspect(bind).get_indexes('subscriptions')}
    if 'ix_subscriptions_paymongo_subscription_id' not in indexes:
        op.create_index(
            'ix_subscriptions_paymongo_subscription_id',
            'subscriptions',
            ['paymongo_subscription_id'],
            unique=True,
        )


def downgrade():
    # Drop indexes
    op.drop_index('ix_subscriptions_paymongo_subscription_id', table_name='subscriptions')
    
    # Drop columns
    op.drop_column('subscriptions', 'paymongo_customer_id')
    op.drop_column('subscriptions', 'paymongo_subscription_id')
    op.drop_column('subscriptions', 'billing_cycle')
