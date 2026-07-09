"""add_user_security_fields

Revision ID: 0008_add_user_security_fields
Revises: 001_backfill_default_tenant
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = '0008_add_user_security_fields'
down_revision = '0007_add_manual_payment_workflow'  # FIXED: was '001_backfill_default_tenant' (missing; '0010_backfill_default_tenant' would create a cycle)
branch_labels = None
depends_on = None


def _has_unique_constraint(inspector, table_name, constraint_name):
    return any(
        constraint['name'] == constraint_name
        for constraint in inspector.get_unique_constraints(table_name)
    )


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_columns = {col['name'] for col in inspector.get_columns('users')}

    if 'last_login_ip' not in existing_columns:
        op.add_column('users', sa.Column('last_login_ip', sa.String(length=45), nullable=True))

    if 'totp_secret' not in existing_columns:
        op.add_column('users', sa.Column('totp_secret', sa.String(length=64), nullable=True))

    if 'totp_enabled' not in existing_columns:
        op.add_column(
            'users',
            sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        )

    if 'totp_backup_codes' not in existing_columns:
        op.add_column('users', sa.Column('totp_backup_codes', sa.Text(), nullable=True))

    if 'failed_login_attempts' not in existing_columns:
        op.add_column(
            'users',
            sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
        )

    if 'last_failed_login_at' not in existing_columns:
        op.add_column('users', sa.Column('last_failed_login_at', sa.DateTime(timezone=True), nullable=True))

    if 'require_password_reset' not in existing_columns:
        op.add_column(
            'users',
            sa.Column('require_password_reset', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        )

    if 'last_password_changed' not in existing_columns:
        op.add_column('users', sa.Column('last_password_changed', sa.DateTime(timezone=True), nullable=True))

    if 'session_token' not in existing_columns:
        op.add_column('users', sa.Column('session_token', sa.String(length=255), nullable=True))

    # Populate default values for legacy rows if fields already existed or were just added.
    try:
        conn.execute(text('UPDATE users SET failed_login_attempts = 0 WHERE failed_login_attempts IS NULL'))
    except Exception:
        pass

    try:
        conn.execute(text('UPDATE users SET totp_enabled = false WHERE totp_enabled IS NULL'))
    except Exception:
        pass

    # Ensure session_token uniqueness via a named constraint.
    if not _has_unique_constraint(inspector, 'users', 'uq_users_session_token'):
        try:
            with op.batch_alter_table('users') as batch_op:
                batch_op.create_unique_constraint('uq_users_session_token', ['session_token'])
        except Exception:
            pass


def downgrade():
    try:
        with op.batch_alter_table('users') as batch_op:
            batch_op.drop_constraint('uq_users_session_token', type_='unique')
    except Exception:
        pass

    for column_name in [
        'session_token',
        'last_password_changed',
        'require_password_reset',
        'last_failed_login_at',
        'failed_login_attempts',
        'totp_backup_codes',
        'totp_enabled',
        'totp_secret',
        'last_login_ip',
    ]:
        try:
            op.drop_column('users', column_name)
        except Exception:
            pass
