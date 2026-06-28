"""
0020_renewal_notifications — Subscription Renewal Notification System
Alembic migration for Portfolio CMS v4.0

Adds:
  - subscription_notifications table
  - subscriptions.reminder_sent_7d column
  - subscriptions.reminder_sent_30d column
"""

revision      = '0020_renewal_notifications'
down_revision = '0019_inquiry_replies'
branch_labels = None
depends_on    = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # FIXED: 'subscription_notifications' (table + indexes) is now created
    # by 0001_initial_schema.py. The subscriptions.reminder_sent_* columns
    # below are NOT duplicated and are kept as-is.
    if not inspector.has_table('subscription_notifications'):
        # ── subscription_notifications ────────────────────────────────────────────
        op.create_table(
            'subscription_notifications',
            sa.Column('id',                 sa.Integer(),     primary_key=True),
            sa.Column('tenant_id',          sa.Integer(),     sa.ForeignKey('tenants.id',       ondelete='CASCADE'), nullable=False),
            sa.Column('subscription_id',    sa.Integer(),     sa.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True),
            sa.Column('notification_type',  sa.String(50),    nullable=False),
            sa.Column('title',              sa.String(200),   nullable=False),
            sa.Column('message',            sa.Text(),        nullable=False),
            sa.Column('is_read',            sa.Boolean(),     nullable=False, server_default='0'),
            sa.Column('sent_via_email',     sa.Boolean(),     nullable=False, server_default='0'),
            sa.Column('sent_via_dashboard', sa.Boolean(),     nullable=False, server_default='1'),
            sa.Column('created_at',         sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('read_at',            sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_sub_notif_tenant_read', 'subscription_notifications', ['tenant_id', 'is_read'])
        op.create_index('ix_sub_notif_type',        'subscription_notifications', ['notification_type'])

    # ── subscriptions: reminder dedup flags (batch for SQLite ALTER TABLE) ────
    with op.batch_alter_table('subscriptions') as batch:
        batch.add_column(sa.Column('reminder_sent_7d',  sa.Boolean(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('reminder_sent_30d', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('subscriptions') as batch:
        batch.drop_column('reminder_sent_7d')
        batch.drop_column('reminder_sent_30d')
    op.drop_index('ix_sub_notif_type',        'subscription_notifications')
    op.drop_index('ix_sub_notif_tenant_read', 'subscription_notifications')
    op.drop_table('subscription_notifications')
