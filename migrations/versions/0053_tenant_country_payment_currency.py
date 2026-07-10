"""Add tenant country preferences and payment currency snapshots.

Revision ID: 0053_tenant_country_payment_currency
Revises: 0052_project_case_studies_and_seo
Create Date: 2026-07-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = '0053_tenant_country_payment_currency'
down_revision = '0052_project_case_studies_and_seo'
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
    if inspector.has_table('tenants'):
        existing = _columns(inspector, 'tenants')
        with op.batch_alter_table('tenants') as batch:
            if 'country_code' not in existing:
                batch.add_column(sa.Column('country_code', sa.String(2), nullable=True))
            if 'preferred_currency' not in existing:
                batch.add_column(sa.Column('preferred_currency', sa.String(3), nullable=True))
            if 'country_source' not in existing:
                batch.add_column(sa.Column('country_source', sa.String(30), nullable=False, server_default='unconfirmed'))
            if 'country_updated_at' not in existing:
                batch.add_column(sa.Column('country_updated_at', sa.DateTime(timezone=True), nullable=True))

    if inspector.has_table('payment_submissions'):
        existing = _columns(inspector, 'payment_submissions')
        with op.batch_alter_table('payment_submissions') as batch:
            if 'amount_usd' not in existing:
                batch.add_column(sa.Column('amount_usd', sa.Float(), nullable=True))
            if 'currency_code' not in existing:
                batch.add_column(sa.Column('currency_code', sa.String(3), nullable=False, server_default='USD'))
            if 'exchange_rate' not in existing:
                batch.add_column(sa.Column('exchange_rate', sa.Numeric(18, 8), nullable=True))
            if 'country_code' not in existing:
                batch.add_column(sa.Column('country_code', sa.String(2), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('payment_submissions'):
        existing = _columns(inspector, 'payment_submissions')
        with op.batch_alter_table('payment_submissions') as batch:
            for name in ('country_code', 'exchange_rate', 'currency_code', 'amount_usd'):
                if name in existing:
                    batch.drop_column(name)
    if inspector.has_table('tenants'):
        existing = _columns(inspector, 'tenants')
        with op.batch_alter_table('tenants') as batch:
            for name in ('country_updated_at', 'country_source', 'preferred_currency', 'country_code'):
                if name in existing:
                    batch.drop_column(name)
