"""
v5.9 — Tenant Email Services Infrastructure

Creates four new tables for the multi-provider tenant email system:
  • tenant_email_providers   — provider registry with priority/status
  • tenant_smtp_settings     — encrypted SMTP credentials per tenant
  • tenant_resend_settings   — encrypted Resend API credentials per tenant
  • tenant_mailersend_settings — encrypted MailerSend API credentials per tenant

All tables cascade-delete with tenants. All credential columns are
Fernet-encrypted at the application layer before storage.

Migration is additive-only — no existing tables are modified.
Backward compatible: existing tenants simply have no rows in the new tables
(treated as "no provider configured") until they configure via the dashboard.

Revision ID: 0031_tenant_email_services_v5_9
Revises: 0030_merge_all_heads_v5_6
"""

revision      = '0031_tenant_email_services_v5_9'
down_revision = '0030_merge_all_heads_v5_6'
branch_labels = None
depends_on    = None


def upgrade():
    from alembic import op
    import sqlalchemy as sa

    # ── tenant_email_providers ────────────────────────────────────────────────
    op.create_table(
        'tenant_email_providers',
        sa.Column('id',                sa.Integer(),     primary_key=True),
        sa.Column('tenant_id',         sa.Integer(),     sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider_name',     sa.String(50),    nullable=False),
        sa.Column('active',            sa.Boolean(),     nullable=False, server_default='0'),
        sa.Column('priority',          sa.Integer(),     nullable=False, server_default='99'),
        sa.Column('status',            sa.String(50),    nullable=False, server_default='unconfigured'),
        sa.Column('last_tested_at',    sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error',        sa.Text(),        nullable=True),
        sa.Column('emails_sent_today', sa.Integer(),     nullable=False, server_default='0'),
        sa.Column('last_sent_at',      sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at',        sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at',        sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('tenant_id', 'provider_name', name='uq_tenant_email_provider'),
    )
    op.create_index('ix_tep_tenant_active', 'tenant_email_providers', ['tenant_id', 'active'])
    op.create_index('ix_tep_tenant_id',     'tenant_email_providers', ['tenant_id'])

    # ── tenant_smtp_settings ─────────────────────────────────────────────────
    op.create_table(
        'tenant_smtp_settings',
        sa.Column('id',                     sa.Integer(),  primary_key=True),
        sa.Column('tenant_id',              sa.Integer(),  sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('smtp_host',              sa.String(300), server_default=''),
        sa.Column('smtp_port',              sa.Integer(),   server_default='587'),
        sa.Column('smtp_username',          sa.String(300), server_default=''),
        sa.Column('smtp_password_encrypted', sa.Text(),     server_default=''),
        sa.Column('sender_email',           sa.String(300), server_default=''),
        sa.Column('sender_name',            sa.String(200), server_default=''),
        sa.Column('encryption_type',        sa.String(20),  server_default='tls'),
        sa.Column('created_at',             sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at',             sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_smtp'),
    )
    op.create_index('ix_tss_tenant_id', 'tenant_smtp_settings', ['tenant_id'])

    # ── tenant_resend_settings ───────────────────────────────────────────────
    op.create_table(
        'tenant_resend_settings',
        sa.Column('id',               sa.Integer(),  primary_key=True),
        sa.Column('tenant_id',        sa.Integer(),  sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(),    server_default=''),
        sa.Column('domain',           sa.String(300), server_default=''),
        sa.Column('sender_email',     sa.String(300), server_default=''),
        sa.Column('sender_name',      sa.String(200), server_default=''),
        sa.Column('created_at',       sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at',       sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_resend'),
    )
    op.create_index('ix_trs_tenant_id', 'tenant_resend_settings', ['tenant_id'])

    # ── tenant_mailersend_settings ───────────────────────────────────────────
    op.create_table(
        'tenant_mailersend_settings',
        sa.Column('id',                 sa.Integer(),  primary_key=True),
        sa.Column('tenant_id',          sa.Integer(),  sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('api_token_encrypted', sa.Text(),    server_default=''),
        sa.Column('domain',             sa.String(300), server_default=''),
        sa.Column('sender_email',       sa.String(300), server_default=''),
        sa.Column('sender_name',        sa.String(200), server_default=''),
        sa.Column('created_at',         sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at',         sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_mailersend'),
    )
    op.create_index('ix_tms_tenant_id', 'tenant_mailersend_settings', ['tenant_id'])


def downgrade():
    from alembic import op

    op.drop_index('ix_tms_tenant_id',   table_name='tenant_mailersend_settings')
    op.drop_index('ix_trs_tenant_id',   table_name='tenant_resend_settings')
    op.drop_index('ix_tss_tenant_id',   table_name='tenant_smtp_settings')
    op.drop_index('ix_tep_tenant_id',   table_name='tenant_email_providers')
    op.drop_index('ix_tep_tenant_active', table_name='tenant_email_providers')

    op.drop_table('tenant_mailersend_settings')
    op.drop_table('tenant_resend_settings')
    op.drop_table('tenant_smtp_settings')
    op.drop_table('tenant_email_providers')
