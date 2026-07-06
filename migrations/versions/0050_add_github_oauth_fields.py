"""Add GitHub OAuth fields to users.

Revision ID: 0050_add_github_oauth_fields
Revises: 0049_tenant_custom_domains
Create Date: 2026-07-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0050_add_github_oauth_fields'
down_revision = '0049_tenant_custom_domains'
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    try:
        return any(col.get('name') == column for col in inspector.get_columns(table))
    except Exception:
        return False


def _has_index(inspector, table: str, name: str) -> bool:
    try:
        return any(ix.get('name') == name for ix in inspector.get_indexes(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('users'):
        return

    with op.batch_alter_table('users') as batch:
        if not _has_column(inspector, 'users', 'github_id'):
            batch.add_column(sa.Column('github_id', sa.String(length=255), nullable=True))

    inspector = sa.inspect(bind)
    if _has_column(inspector, 'users', 'github_id') and not _has_index(inspector, 'users', 'ix_users_github_id'):
        with op.batch_alter_table('users') as batch:
            batch.create_index('ix_users_github_id', ['github_id'], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('users'):
        return

    with op.batch_alter_table('users') as batch:
        if _has_index(inspector, 'users', 'ix_users_github_id'):
            batch.drop_index('ix_users_github_id')
        if _has_column(inspector, 'users', 'github_id'):
            batch.drop_column('github_id')
