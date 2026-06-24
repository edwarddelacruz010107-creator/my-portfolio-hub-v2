"""PayMongo automated billing — new subscription fields + webhook_events

Revision ID: 0013_paymongo_automated_billing
Revises: 0012_trial_enforcement_v3
Create Date: 2026-06-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '0013_paymongo_automated_billing'
down_revision = '0012_trial_enforcement_v3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('subscriptions', sa.Column('paymongo_id', sa.String(255), nullable=True))
    op.add_column('subscriptions', sa.Column('paymongo_payment_id', sa.String(255), nullable=True))
    op.add_column('subscriptions', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('subscriptions', sa.Column('last_webhook_at', sa.DateTime(timezone=True), nullable=True))

    op.create_index('ix_subscriptions_paymongo_id', 'subscriptions', ['paymongo_id'], unique=False)
    op.create_index('ix_subscriptions_paymongo_payment_id', 'subscriptions', ['paymongo_payment_id'], unique=True)

    op.create_table(
        'webhook_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.String(255), nullable=False),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=True),
        sa.Column('payload_summary', sa.String(500), nullable=True),
        sa.Column('processed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id'),
    )
    op.create_index('ix_webhook_events_event_id', 'webhook_events', ['event_id'], unique=True)
    op.create_index('ix_webhook_events_type_received', 'webhook_events', ['event_type', 'received_at'], unique=False)
    op.create_index('ix_webhook_events_tenant_id', 'webhook_events', ['tenant_id'], unique=False)

    # Migrate legacy awaiting_activation → active (automated billing has no key step)
    op.execute("""
        UPDATE subscriptions
        SET status = 'active',
            started_at = COALESCE(started_at, updated_at, created_at),
            expires_at = COALESCE(
                expires_at,
                datetime(COALESCE(started_at, updated_at, created_at), '+30 days')
            )
        WHERE status = 'awaiting_activation'
    """)


def downgrade():
    op.drop_index('ix_webhook_events_tenant_id', table_name='webhook_events')
    op.drop_index('ix_webhook_events_type_received', table_name='webhook_events')
    op.drop_index('ix_webhook_events_event_id', table_name='webhook_events')
    op.drop_table('webhook_events')

    op.drop_index('ix_subscriptions_paymongo_payment_id', table_name='subscriptions')
    op.drop_index('ix_subscriptions_paymongo_id', table_name='subscriptions')
    op.drop_column('subscriptions', 'last_webhook_at')
    op.drop_column('subscriptions', 'cancelled_at')
    op.drop_column('subscriptions', 'paymongo_payment_id')
    op.drop_column('subscriptions', 'paymongo_id')
