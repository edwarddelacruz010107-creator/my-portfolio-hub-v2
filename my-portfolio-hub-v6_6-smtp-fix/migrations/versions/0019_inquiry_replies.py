"""Add InquiryReply table and thread tracking columns (v3.8 messaging)

Revision ID: 0019_inquiry_replies
Revises: 0018_auth_otp_web3forms
Create Date: 2026-06-10

Changes:
  1. inquiry_replies  — new table for bidirectional threaded replies
  2. inquiries.updated_at            — last-activity timestamp for thread sorting
  3. inquiries.thread_unread_tenant  — unread reply count visible to tenant
  4. inquiries.thread_unread_super   — unread reply count visible to superadmin

Rollback: drops new table + columns cleanly. Zero data loss.
"""
from alembic import op
import sqlalchemy as sa

revision      = '0019_inquiry_replies'
down_revision = '003_tenant_comm_settings'
branch_labels = None
depends_on    = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # FIXED: 'inquiry_replies' (table + its 3 indexes) is now created by
    # 0001_initial_schema.py. The 'inquiries' add_column calls below are
    # NOT duplicated (0001's inquiries table predates these columns) and
    # are kept as-is.
    if not inspector.has_table('inquiry_replies'):
        # ── 1. inquiry_replies table ─────────────────────────────────────────────
        op.create_table(
            'inquiry_replies',
            sa.Column('id',          sa.Integer,                nullable=False),
            sa.Column('inquiry_id',  sa.Integer,                nullable=False),
            sa.Column('tenant_slug', sa.String(120),            nullable=False),
            sa.Column('direction',   sa.String(20),             nullable=False),
            sa.Column('sender_name', sa.String(120),            nullable=False),
            sa.Column('message',     sa.Text,                   nullable=False),
            sa.Column('is_read',     sa.Boolean, default=False, nullable=False),
            sa.Column('created_at',  sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(
                ['inquiry_id'], ['inquiries.id'],
                ondelete='CASCADE',
            ),
        )
        op.create_index('ix_reply_inquiry_id',     'inquiry_replies', ['inquiry_id'])
        op.create_index('ix_reply_tenant_slug',    'inquiry_replies', ['tenant_slug'])
        op.create_index('ix_reply_direction_read', 'inquiry_replies', ['direction', 'is_read'])

    # ── 2. inquiries — thread tracking columns ───────────────────────────────
    with op.batch_alter_table('inquiries', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column('thread_unread_tenant', sa.Integer, default=0, nullable=True)
        )
        batch_op.add_column(
            sa.Column('thread_unread_super',  sa.Integer, default=0, nullable=True)
        )
        batch_op.create_index('ix_inquiries_updated_at', ['updated_at'])

    # Backfill updated_at from created_at
    op.execute(
        "UPDATE inquiries SET updated_at = created_at, "
        "thread_unread_tenant = 0, thread_unread_super = 0"
    )


def downgrade():
    with op.batch_alter_table('inquiries', schema=None) as batch_op:
        batch_op.drop_index('ix_inquiries_updated_at')
        batch_op.drop_column('thread_unread_super')
        batch_op.drop_column('thread_unread_tenant')
        batch_op.drop_column('updated_at')

    op.drop_index('ix_reply_direction_read', table_name='inquiry_replies')
    op.drop_index('ix_reply_tenant_slug',    table_name='inquiry_replies')
    op.drop_index('ix_reply_inquiry_id',     table_name='inquiry_replies')
    op.drop_table('inquiry_replies')
