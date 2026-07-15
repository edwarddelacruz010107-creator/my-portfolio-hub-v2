"""Add tenant work experience timeline table.

Revision ID: 0051_add_work_experience_timeline
Revises: 0050_add_github_oauth_fields
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0051_add_work_experience_timeline'
down_revision = '0050_add_github_oauth_fields'
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


def _create_index_if_missing(inspector, name: str, table: str, columns: list[str]) -> None:
    if not _has_index(inspector, table, name):
        op.create_index(name, table, columns)


def upgrade() -> None:
    # Tenant-bind DDL is applied by migrations/tenant. Keep this core revision
    # as a no-op so existing Alembic histories remain connected.
    return

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('work_experiences'):
        op.create_table(
            'work_experiences',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('tenant_id', sa.Integer(), nullable=False),
            sa.Column('tenant_slug', sa.String(length=120), nullable=False, server_default='default'),
            sa.Column('role', sa.String(length=160), nullable=False),
            sa.Column('company', sa.String(length=160), nullable=False),
            sa.Column('employment_type', sa.String(length=80), nullable=True, server_default='Full-time'),
            sa.Column('location', sa.String(length=160), nullable=True),
            sa.Column('start_date', sa.Date(), nullable=True),
            sa.Column('end_date', sa.Date(), nullable=True),
            sa.Column('is_current', sa.Boolean(), nullable=True, server_default=sa.false()),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('achievements', sa.Text(), nullable=True),
            sa.Column('technologies', sa.Text(), nullable=True),
            sa.Column('icon', sa.String(length=100), nullable=True, server_default='lucide:briefcase-business'),
            sa.Column('display_order', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('is_visible', sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        )
        inspector = sa.inspect(bind)
    else:
        with op.batch_alter_table('work_experiences') as batch:
            for column in [
                sa.Column('tenant_id', sa.Integer(), nullable=False, server_default='0'),
                sa.Column('tenant_slug', sa.String(length=120), nullable=False, server_default='default'),
                sa.Column('role', sa.String(length=160), nullable=False, server_default='Experience'),
                sa.Column('company', sa.String(length=160), nullable=False, server_default='Company'),
                sa.Column('employment_type', sa.String(length=80), nullable=True, server_default='Full-time'),
                sa.Column('location', sa.String(length=160), nullable=True),
                sa.Column('start_date', sa.Date(), nullable=True),
                sa.Column('end_date', sa.Date(), nullable=True),
                sa.Column('is_current', sa.Boolean(), nullable=True, server_default=sa.false()),
                sa.Column('description', sa.Text(), nullable=True),
                sa.Column('achievements', sa.Text(), nullable=True),
                sa.Column('technologies', sa.Text(), nullable=True),
                sa.Column('icon', sa.String(length=100), nullable=True, server_default='lucide:briefcase-business'),
                sa.Column('display_order', sa.Integer(), nullable=True, server_default='0'),
                sa.Column('is_visible', sa.Boolean(), nullable=True, server_default=sa.true()),
                sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
                sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
            ]:
                if not _has_column(inspector, 'work_experiences', column.name):
                    batch.add_column(column)
        inspector = sa.inspect(bind)

    _create_index_if_missing(inspector, 'ix_work_experiences_tenant_id', 'work_experiences', ['tenant_id'])
    _create_index_if_missing(inspector, 'ix_work_experiences_tenant_visible', 'work_experiences', ['tenant_id', 'is_visible'])
    _create_index_if_missing(inspector, 'ix_work_experiences_tenant_order', 'work_experiences', ['tenant_id', 'display_order'])
    _create_index_if_missing(inspector, 'ix_work_experiences_tenant_current', 'work_experiences', ['tenant_id', 'is_current'])


def downgrade() -> None:
    return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('work_experiences'):
        return
    for name in (
        'ix_work_experiences_tenant_current',
        'ix_work_experiences_tenant_order',
        'ix_work_experiences_tenant_visible',
        'ix_work_experiences_tenant_id',
    ):
        try:
            if _has_index(inspector, 'work_experiences', name):
                op.drop_index(name, table_name='work_experiences')
        except Exception:
            pass
    op.drop_table('work_experiences')
