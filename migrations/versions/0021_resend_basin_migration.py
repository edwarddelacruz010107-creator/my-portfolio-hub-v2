"""0021_resend_basin_migration — Resend + Basin integration

Revision ID: 0021_resend_basin_migration
Revises: 0020_renewal_notifications
Create Date: 2026-06-12

Changes:
  1. tenants.form_provider      — VARCHAR(20) DEFAULT 'internal'
     Allowed values: 'internal', 'basin'
  2. tenants.basin_endpoint     — TEXT nullable
     Format: https://usebasin.com/f/<id>
  3. global_email_config.resend_api_key — TEXT nullable (Fernet-encrypted)
     Replaces Web3Forms as the primary transactional email key.

Rollback: downgrade() removes all three columns safely.
Data safety: existing rows are unaffected; new columns have defaults.
"""
from alembic import op
import sqlalchemy as sa

revision      = '0021_resend_basin_migration'
down_revision = '0020_renewal_notifications'
branch_labels = None
depends_on    = None


def upgrade():
    # ── 1. tenants: contact form provider ────────────────────────────────────
    with op.batch_alter_table('tenants', schema=None) as batch:
        batch.add_column(sa.Column(
            'form_provider',
            sa.String(20),
            nullable=False,
            server_default='internal',
        ))
        batch.add_column(sa.Column(
            'basin_endpoint',
            sa.Text(),
            nullable=True,
        ))
        batch.create_index('ix_tenants_form_provider', ['form_provider'])

    # Backfill: all existing tenants use internal CMS
    op.execute("UPDATE tenants SET form_provider = 'internal' WHERE form_provider IS NULL OR form_provider = ''")

    # ── 2. global_email_config: Resend API key ────────────────────────────────
    with op.batch_alter_table('global_email_config', schema=None) as batch:
        batch.add_column(sa.Column(
            'resend_api_key',
            sa.Text(),
            nullable=True,
            comment='Fernet-encrypted Resend API key',
        ))


def downgrade():
    with op.batch_alter_table('global_email_config', schema=None) as batch:
        batch.drop_column('resend_api_key')

    with op.batch_alter_table('tenants', schema=None) as batch:
        batch.drop_index('ix_tenants_form_provider')
        batch.drop_column('basin_endpoint')
        batch.drop_column('form_provider')
