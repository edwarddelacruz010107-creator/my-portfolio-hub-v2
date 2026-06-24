"""Add TOTP replay prevention and password reset columns to users.

Revision ID: 0010_security_reset
Revises: 0009_add_performance_indexes
Create Date: 2026-06-03
"""
from alembic import op
import sqlalchemy as sa

revision = '0010_security_reset'
down_revision = '0009_add_performance_indexes'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        # TOTP replay prevention (SEC-004 FIX)
        batch_op.add_column(sa.Column(
            'last_totp_verified_at',
            sa.DateTime(timezone=True),
            nullable=True
        ))
        batch_op.add_column(sa.Column(
            'last_totp_code_hash',
            sa.String(length=64),
            nullable=True
        ))
        # Self-service password reset
        batch_op.add_column(sa.Column(
            'password_reset_token',
            sa.String(length=100),
            nullable=True
        ))
        batch_op.add_column(sa.Column(
            'password_reset_expires',
            sa.DateTime(timezone=True),
            nullable=True
        ))

    # Index for fast token lookups
    op.create_index(
        'ix_users_password_reset_token',
        'users',
        ['password_reset_token'],
        unique=True,
        postgresql_where=sa.text("password_reset_token IS NOT NULL"),
    )

    # ActivityLog composite index (DB-003 FIX)
    try:
        op.create_index(
            'ix_activitylog_tenant_action',
            'activity_log',
            ['tenant_slug', 'action'],
        )
    except Exception:
        pass  # Already exists


def downgrade():
    op.drop_index('ix_users_password_reset_token', table_name='users')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('password_reset_expires')
        batch_op.drop_column('password_reset_token')
        batch_op.drop_column('last_totp_code_hash')
        batch_op.drop_column('last_totp_verified_at')
    try:
        op.drop_index('ix_activitylog_tenant_action', table_name='activity_log')
    except Exception:
        pass
