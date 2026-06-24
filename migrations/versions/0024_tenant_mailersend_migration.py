"""0024_tenant_mailersend_migration — Per-tenant MailerSend integration

Revision ID: 0024_tenant_mailersend_migration
Revises: 0023_mailersend_migration
Create Date: 2026-06-16

Changes:
  1. tenant_communication_settings.mailersend_api_key — TEXT nullable
     Stores per-tenant Fernet-encrypted MailerSend API key.
     Allows tenants to use their own MailerSend account instead of global config.

  2. tenant_communication_settings.mailersend_from_email — VARCHAR(200)
     Email address to send from (verified in MailerSend).

  3. tenant_communication_settings.mailersend_from_name — VARCHAR(200)
     Display name for the sender (e.g., "My Support Team").

Upgrade path:
  • Three new columns added to tenant_communication_settings.
  • Existing tenants can continue using SMTP or global config.
  • When tenant fills in MailerSend credentials, email service will prefer MailerSend.
  • SMTP fields are retained for backward compatibility.

Downgrade path:
  • Columns dropped cleanly. No data loss if using SMTP elsewhere.
"""
from alembic import op
import sqlalchemy as sa

revision      = '0024_tenant_mailersend_migration'
down_revision = '0023_mailersend_migration'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('tenant_communication_settings', schema=None) as batch:
        batch.add_column(
            sa.Column(
                'mailersend_api_key',
                sa.Text(),
                nullable=True,
                server_default='',
            )
        )
        batch.add_column(
            sa.Column(
                'mailersend_from_email',
                sa.String(200),
                nullable=True,
                server_default='',
            )
        )
        batch.add_column(
            sa.Column(
                'mailersend_from_name',
                sa.String(200),
                nullable=True,
                server_default='',
            )
        )


def downgrade():
    with op.batch_alter_table('tenant_communication_settings', schema=None) as batch:
        batch.drop_column('mailersend_from_name')
        batch.drop_column('mailersend_from_email')
        batch.drop_column('mailersend_api_key')
