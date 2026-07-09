"""Platform settings table for superadmin toggles

Revision ID: 0016_platform_settings
Revises: 0015_drop_legacy_subscription_columns
Create Date: 2026-06-06 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '0016_platform_settings'
down_revision = '0015_drop_legacy_subscription_columns'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    # FIXED: 'platform_settings' is now created by 0001_initial_schema.py.
    if not inspector.has_table('platform_settings'):
        op.create_table(
            'platform_settings',
            sa.Column('key', sa.String(100), nullable=False),
            sa.Column('value', sa.String(500), nullable=False, server_default=''),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('key'),
        )


def downgrade():
    op.drop_table('platform_settings')
