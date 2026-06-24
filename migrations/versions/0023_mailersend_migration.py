"""0023_mailersend_migration — MailerSend integration

Revision ID: 0023_mailersend_migration
Revises: 0022_tenant_form_settings
Create Date: 2026-06-16

Changes:
  1. global_email_config.mailersend_api_key — TEXT nullable
     Stores the Fernet-encrypted MailerSend API key.
     The existing resend_api_key column is intentionally retained so that
     a rollback does not destroy live data; it becomes a no-op at the
     application layer (the property always returns '').

Upgrade path:
  • New column added.
  • Existing deployments: set MAILERSEND_API_KEY in the environment (or paste
    it in the superadmin Email Settings page) after deploying this migration.

Downgrade path:
  • Column dropped cleanly.  The resend_api_key column is never touched.
"""
from alembic import op
import sqlalchemy as sa

revision      = '0023_mailersend_migration'
down_revision = '0022_tenant_form_settings'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('global_email_config', schema=None) as batch:
        batch.add_column(
            sa.Column(
                'mailersend_api_key',
                sa.Text(),
                nullable=True,
                server_default='',
            )
        )


def downgrade():
    with op.batch_alter_table('global_email_config', schema=None) as batch:
        batch.drop_column('mailersend_api_key')
