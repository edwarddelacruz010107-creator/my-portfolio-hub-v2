"""Add TenantCommunicationSettings table

Revision ID: 003_tenant_comm_settings
Revises: 002  (adjust to your actual previous revision id)
Create Date: 2026-06-10

Changes:
  - Creates tenant_communication_settings table
  - One row per tenant (unique constraint on tenant_id)
  - Encrypted columns: web3forms_key, mail_password (Fernet at app layer)
  - Cascade delete on tenant removal
"""

from alembic import op
import sqlalchemy as sa

revision = '003_tenant_comm_settings'
down_revision = None   # ← set to your actual previous migration revision
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tenant_communication_settings',
        sa.Column('id',         sa.Integer, primary_key=True),
        sa.Column('tenant_id',  sa.Integer,
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('tenant_slug', sa.String(120), nullable=False),
        # Encrypted at application layer (Fernet); stored as opaque text
        sa.Column('web3forms_key', sa.Text,    nullable=False, server_default=''),
        # SMTP
        sa.Column('mail_username',       sa.String(200), nullable=False, server_default=''),
        sa.Column('mail_password',       sa.Text,        nullable=False, server_default=''),
        sa.Column('mail_default_sender', sa.String(200), nullable=False, server_default=''),
        sa.Column('admin_email',         sa.String(200), nullable=False, server_default=''),
        sa.Column('smtp_host',           sa.String(200), nullable=False, server_default=''),
        sa.Column('smtp_port',           sa.Integer,     nullable=False, server_default='587'),
        sa.Column('smtp_tls',            sa.Boolean,     nullable=False, server_default='1'),
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Unique: one row per tenant
    op.create_unique_constraint(
        'uq_tenant_comm_settings',
        'tenant_communication_settings',
        ['tenant_id'],
    )

    # Index on tenant_slug for fast lookups in contact form resolution
    op.create_index(
        'ix_tenant_comm_slug',
        'tenant_communication_settings',
        ['tenant_slug'],
    )


def downgrade():
    op.drop_index('ix_tenant_comm_slug', 'tenant_communication_settings')
    op.drop_constraint('uq_tenant_comm_settings', 'tenant_communication_settings')
    op.drop_table('tenant_communication_settings')
