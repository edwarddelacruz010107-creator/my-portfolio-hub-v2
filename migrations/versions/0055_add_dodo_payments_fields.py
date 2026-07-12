"""add Dodo Payments subscription identifiers

Revision ID: 0055
Revises: 0054
"""
from alembic import op
import sqlalchemy as sa

revision = '0055'
down_revision = '0054'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('subscriptions') as batch:
        batch.add_column(sa.Column('payment_provider', sa.String(length=30), nullable=True))
        batch.add_column(sa.Column('dodo_checkout_session_id', sa.String(length=255), nullable=True))
        batch.add_column(sa.Column('dodo_customer_id', sa.String(length=255), nullable=True))
        batch.add_column(sa.Column('dodo_subscription_id', sa.String(length=255), nullable=True))
        batch.add_column(sa.Column('dodo_payment_id', sa.String(length=255), nullable=True))
        batch.add_column(sa.Column('provider_currency', sa.String(length=3), nullable=True))
        batch.create_index('ix_subscriptions_payment_provider', ['payment_provider'])
        batch.create_index('ix_subscriptions_dodo_checkout_session_id', ['dodo_checkout_session_id'])
        batch.create_index('ix_subscriptions_dodo_customer_id', ['dodo_customer_id'])
        batch.create_index('ix_subscriptions_dodo_subscription_id', ['dodo_subscription_id'], unique=True)
        batch.create_index('ix_subscriptions_dodo_payment_id', ['dodo_payment_id'])


def downgrade():
    with op.batch_alter_table('subscriptions') as batch:
        batch.drop_index('ix_subscriptions_dodo_payment_id')
        batch.drop_index('ix_subscriptions_dodo_subscription_id')
        batch.drop_index('ix_subscriptions_dodo_customer_id')
        batch.drop_index('ix_subscriptions_dodo_checkout_session_id')
        batch.drop_index('ix_subscriptions_payment_provider')
        for col in ('provider_currency','dodo_payment_id','dodo_subscription_id','dodo_customer_id','dodo_checkout_session_id','payment_provider'):
            batch.drop_column(col)
