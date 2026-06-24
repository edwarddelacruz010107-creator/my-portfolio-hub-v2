"""0022_tenant_form_settings — Per-tenant form provider isolation

Revision ID: 0022_tenant_form_settings
Revises: 0021_resend_basin_migration
Create Date: 2026-06-12

Changes:
  1. Create tenant_form_settings table with ENUM provider column.
  2. Backfill existing basin-configured tenants.
  3. Add updated_at trigger (PostgreSQL only; SQLite-safe for tests).

Data safety:
  - No existing columns dropped.
  - No existing rows modified.
  - ON CONFLICT DO NOTHING guards backfill idempotence.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = '0022_tenant_form_settings'
down_revision = '0021_resend_basin_migration'
branch_labels = None
depends_on    = None


def upgrade():
    # ── 1. ENUM type ──────────────────────────────────────────────────────────
    provider_enum = postgresql.ENUM(
        'basin', 'web3forms', 'disabled',
        name='form_provider_enum',
        create_type=False,
    )
    provider_enum.create(op.get_bind(), checkfirst=True)

    # ── 2. tenant_form_settings table ─────────────────────────────────────────
    op.create_table(
        'tenant_form_settings',
        sa.Column('id',               sa.Integer,      primary_key=True),
        sa.Column('tenant_id',        sa.Integer,
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('provider',         sa.Enum('basin', 'web3forms', 'disabled',
                                              name='form_provider_enum'),
                  nullable=False, server_default='disabled'),
        sa.Column('api_key_encrypted', sa.Text,         nullable=False, server_default=''),
        sa.Column('form_endpoint',    sa.Text,          nullable=True),
        sa.Column('receiver_email',   sa.String(200),   nullable=True),
        sa.Column('sender_name',      sa.String(200),   nullable=True),
        sa.Column('is_enabled',       sa.Boolean,       nullable=False, server_default='false'),
        sa.Column('created_at',       sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at',       sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_form_settings'),
    )

    # ── 3. Extra indexes ──────────────────────────────────────────────────────
    op.create_index('ix_tfs_provider',   'tenant_form_settings', ['provider'])
    op.create_index('ix_tfs_is_enabled', 'tenant_form_settings', ['is_enabled'])

    # ── 4. updated_at trigger (PostgreSQL only) ───────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
        $$;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS trg_tfs_updated_at ON tenant_form_settings;
        CREATE TRIGGER trg_tfs_updated_at
            BEFORE UPDATE ON tenant_form_settings
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── 5. Backfill existing basin tenants ────────────────────────────────────
    op.execute("""
        INSERT INTO tenant_form_settings
            (tenant_id, provider, form_endpoint, receiver_email,
             sender_name, is_enabled, created_at, updated_at)
        SELECT
            t.id,
            'basin'::form_provider_enum,
            t.basin_endpoint,
            t.contact_email,
            t.company_name,
            (t.basin_endpoint IS NOT NULL AND t.basin_endpoint != ''),
            NOW(), NOW()
        FROM tenants t
        WHERE t.form_provider = 'basin'
          AND t.basin_endpoint IS NOT NULL
          AND t.basin_endpoint != ''
        ON CONFLICT (tenant_id) DO NOTHING;
    """)


def downgrade():
    op.drop_table('tenant_form_settings')
    op.execute("DROP TYPE IF EXISTS form_provider_enum;")
