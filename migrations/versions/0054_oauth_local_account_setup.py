"""Add local credential state for OAuth-created tenant accounts.

Revision ID: 0054_oauth_local_account_setup
Revises: 0053_tenant_country_payment_currency
Create Date: 2026-07-11
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0054_oauth_local_account_setup'
down_revision = '0053_tenant_country_payment_currency'
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    try:
        return {column['name'] for column in inspector.get_columns(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('users'):
        return

    existing = _columns(inspector, 'users')
    with op.batch_alter_table('users') as batch:
        if 'local_password_enabled' not in existing:
            batch.add_column(sa.Column(
                'local_password_enabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ))
        if 'oauth_setup_required' not in existing:
            batch.add_column(sa.Column(
                'oauth_setup_required',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ))

    # Older OAuth-only accounts used an opaque random placeholder in
    # password_hash. Werkzeug hashes contain ':' or '$'; placeholders do not.
    # Mark only those provider-only rows for the one-time setup. Accounts that
    # were local first and later linked use auth_provider='both' and are left
    # untouched.
    bind.execute(sa.text("""
        UPDATE users
           SET local_password_enabled = :disabled,
               oauth_setup_required = :required
         WHERE lower(COALESCE(auth_provider, '')) IN ('google', 'github')
           AND password_hash NOT LIKE '%:%'
           AND password_hash NOT LIKE '%$%'
    """), {'disabled': False, 'required': True})


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('users'):
        return
    existing = _columns(inspector, 'users')
    with op.batch_alter_table('users') as batch:
        if 'oauth_setup_required' in existing:
            batch.drop_column('oauth_setup_required')
        if 'local_password_enabled' in existing:
            batch.drop_column('local_password_enabled')
