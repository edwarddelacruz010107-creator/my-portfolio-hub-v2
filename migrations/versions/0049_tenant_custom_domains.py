"""Add tenant custom domains.

Revision ID: 0049_tenant_custom_domains
Revises: 0048_restrict_duplicate_user_emails
Create Date: 2026-07-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0049_tenant_custom_domains'
down_revision = '0048_restrict_duplicate_user_emails'
branch_labels = None
depends_on = None


def _has_table(inspector, table: str) -> bool:
    try:
        return inspector.has_table(table)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_table(inspector, 'tenant_custom_domains'):
        return

    op.create_table(
        'tenant_custom_domains',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_slug', sa.String(length=120), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('normalized_domain', sa.String(length=255), nullable=False),
        sa.Column('verification_token', sa.String(length=120), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False, server_default='pending'),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_reason', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('normalized_domain', name='uq_tenant_custom_domains_normalized_domain'),
    )
    op.create_index('ix_tenant_custom_domains_tenant_id', 'tenant_custom_domains', ['tenant_id'])
    op.create_index('ix_tenant_custom_domains_tenant_slug', 'tenant_custom_domains', ['tenant_slug'])
    op.create_index('ix_tenant_custom_domains_normalized_domain', 'tenant_custom_domains', ['normalized_domain'])
    op.create_index('ix_tenant_custom_domains_verification_token', 'tenant_custom_domains', ['verification_token'])
    op.create_index('ix_tenant_custom_domains_status_domain', 'tenant_custom_domains', ['status', 'normalized_domain'])
    op.create_index('ix_tenant_custom_domains_tenant_status', 'tenant_custom_domains', ['tenant_id', 'status'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_table(inspector, 'tenant_custom_domains'):
        return
    for index_name in (
        'ix_tenant_custom_domains_tenant_status',
        'ix_tenant_custom_domains_status_domain',
        'ix_tenant_custom_domains_verification_token',
        'ix_tenant_custom_domains_normalized_domain',
        'ix_tenant_custom_domains_tenant_slug',
        'ix_tenant_custom_domains_tenant_id',
    ):
        try:
            op.drop_index(index_name, table_name='tenant_custom_domains')
        except Exception:
            pass
    op.drop_table('tenant_custom_domains')
