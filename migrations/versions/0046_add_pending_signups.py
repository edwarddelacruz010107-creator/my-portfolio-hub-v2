"""Create pending_signups table for pre-verification signup lifecycle

Revision ID: 0046_add_pending_signups
Revises: 0045_allow_duplicate_user_emails
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa

revision = '0046_add_pending_signups'
down_revision = '0045_allow_duplicate_user_emails'
branch_labels = None
depends_on = None


def _has_table(inspector, table_name: str) -> bool:
    return bool(inspector.has_table(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, 'pending_signups'):
        return

    op.create_table(
        'pending_signups',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('email', sa.String(120), nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('full_name', sa.String(100), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('otp_hash', sa.String(64), nullable=False),
        sa.Column('otp_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('otp_attempts', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_otp_sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('email_verified', sa.Boolean, nullable=False, server_default=sa.text('false')),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_pending_signups_email', 'pending_signups', ['email'])
    op.create_index('ix_pending_signups_username', 'pending_signups', ['username'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, 'pending_signups'):
        return
    op.drop_index('ix_pending_signups_username', table_name='pending_signups')
    op.drop_index('ix_pending_signups_email', table_name='pending_signups')
    op.drop_table('pending_signups')
