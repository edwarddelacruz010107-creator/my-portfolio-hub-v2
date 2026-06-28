"""Add user_id and username to activity_log for superadmin audit log (v3.6)

Revision ID: 0017_activitylog_user_fields
Revises: 0016_platform_settings
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = '0017_activitylog_user_fields'
down_revision = '0016_platform_settings'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('activity_log') as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('username', sa.String(120), nullable=True))
        batch_op.create_index('ix_activitylog_user_tenant', ['user_id', 'tenant_slug'])
    # Best-effort FK; SQLite ignores it, Postgres enforces it.
    try:
        with op.batch_alter_table('activity_log') as batch_op:
            batch_op.create_foreign_key(
                'fk_activitylog_user_id', 'users', ['user_id'], ['id'],
                ondelete='SET NULL',
            )
    except Exception:
        pass


def downgrade():
    with op.batch_alter_table('activity_log') as batch_op:
        batch_op.drop_index('ix_activitylog_user_tenant')
        batch_op.drop_column('username')
        batch_op.drop_column('user_id')
