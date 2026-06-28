"""
0032 — Superadmin Multi-Provider Email Config

Adds SMTP + Resend credentials and provider priority ordering
to the global_email_config singleton (superadmin portal use only).

New columns on global_email_config:
  smtp columns:
    sa_smtp_host, sa_smtp_port, sa_smtp_username,
    sa_smtp_password_encrypted, sa_smtp_sender_email,
    sa_smtp_sender_name, sa_smtp_encryption
  resend columns:
    sa_resend_api_key_encrypted, sa_resend_sender_email, sa_resend_sender_name
  priority + toggle:
    sa_provider_priority (JSON string: e.g. '["smtp","mailersend","resend"]')
    sa_smtp_active, sa_resend_active, sa_mailersend_active
"""
from alembic import op
import sqlalchemy as sa

revision      = '0032_superadmin_email_providers'
down_revision = '0031_tenant_email_services_v5_9'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('global_email_config', schema=None) as batch_op:
        # SMTP
        batch_op.add_column(sa.Column('sa_smtp_host',               sa.String(300), nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_smtp_port',               sa.Integer(),   nullable=True, server_default='587'))
        batch_op.add_column(sa.Column('sa_smtp_username',           sa.String(300), nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_smtp_password_encrypted', sa.Text(),      nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_smtp_sender_email',       sa.String(300), nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_smtp_sender_name',        sa.String(200), nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_smtp_encryption',         sa.String(20),  nullable=True, server_default='tls'))
        batch_op.add_column(sa.Column('sa_smtp_active',             sa.Boolean(),   nullable=True, server_default='0'))
        # Resend
        batch_op.add_column(sa.Column('sa_resend_api_key_encrypted', sa.Text(),      nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_resend_sender_email',      sa.String(300), nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_resend_sender_name',       sa.String(200), nullable=True, server_default=''))
        batch_op.add_column(sa.Column('sa_resend_active',            sa.Boolean(),   nullable=True, server_default='0'))
        # MailerSend active toggle (existing key columns already present)
        batch_op.add_column(sa.Column('sa_mailersend_active',        sa.Boolean(),   nullable=True, server_default='1'))
        # Priority order
        batch_op.add_column(sa.Column('sa_provider_priority',        sa.String(200), nullable=True, server_default='["mailersend","smtp","resend"]'))


def downgrade():
    with op.batch_alter_table('global_email_config', schema=None) as batch_op:
        for col in [
            'sa_smtp_host', 'sa_smtp_port', 'sa_smtp_username',
            'sa_smtp_password_encrypted', 'sa_smtp_sender_email',
            'sa_smtp_sender_name', 'sa_smtp_encryption', 'sa_smtp_active',
            'sa_resend_api_key_encrypted', 'sa_resend_sender_email',
            'sa_resend_sender_name', 'sa_resend_active',
            'sa_mailersend_active', 'sa_provider_priority',
        ]:
            batch_op.drop_column(col)
