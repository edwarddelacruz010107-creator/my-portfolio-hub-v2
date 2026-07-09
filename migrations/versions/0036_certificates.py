"""
0036 — Certificates & Badges (v6.7)

Adds:
  certificates:   (new table, tenant_data_db / __bind_key__ = "tenant")
    id, tenant_id, tenant_slug, title, issuer, description,
    credential_id, verification_url, image_path, badge_path,
    issue_date, expiration_date, skills, is_featured, is_visible,
    display_order, created_at, updated_at

SAFE: additive only. No existing tables or columns touched. No data
destroyed. Same shape as 0034_theme_catalog_entries (pure new-table create),
but this table belongs to the TENANT bind, not core — see
migrations/tenant/env.py, which reflects Certificate into its own isolated
metadata once this model is registered in app/models/tenant_data.py.

⚠️ APPLY-ORDER WARNING — read before running `flask db upgrade`
──────────────────────────────────────────────────────────────
This repo currently has multiple Alembic heads in migrations/versions/
(0011_add_paymongo_subscription, 0028_add_email_only_provider,
0035_theme_catalog_extended — see PHASE4_AUDIT.md / project memory). A plain
`flask db upgrade` will fail with "Multiple head revisions are present" until
that divergence is resolved with an explicit merge revision
(`flask db merge -m "merge heads" <rev1> <rev2> <rev3>`), which is
intentionally NOT bundled into this migration — merging heads is a
schema-wide operation that deserves its own reviewed, single-purpose
migration, not a side effect of a feature migration.

down_revision below chains off 0035_theme_catalog_entries (the most recent
commit on the tenant-schema lineage) so this migration is ready to apply
the moment the head divergence is merged — it does not resolve the
divergence itself.
"""

from alembic import op
import sqlalchemy as sa

revision      = '0036_certificates'
down_revision = '0035_theme_catalog_extended'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'certificates',
        sa.Column('id',                sa.Integer(),   primary_key=True),
        sa.Column('tenant_id',         sa.Integer(),   nullable=False),
        sa.Column('tenant_slug',       sa.String(120), nullable=False, server_default='default'),
        sa.Column('title',             sa.String(255), nullable=False),
        sa.Column('issuer',            sa.String(255), nullable=False),
        sa.Column('description',       sa.Text(),      nullable=True),
        sa.Column('credential_id',     sa.String(255), nullable=True),
        sa.Column('verification_url',  sa.String(500), nullable=True),
        sa.Column('image_path',        sa.String(255), nullable=True),
        sa.Column('badge_path',        sa.String(255), nullable=True),
        sa.Column('issue_date',        sa.Date(),      nullable=True),
        sa.Column('expiration_date',   sa.Date(),      nullable=True),
        sa.Column('skills',            sa.Text(),      nullable=True),
        sa.Column('is_featured',       sa.Boolean(),   nullable=False, server_default='0'),
        sa.Column('is_visible',        sa.Boolean(),   nullable=False, server_default='1'),
        sa.Column('display_order',     sa.Integer(),   nullable=False, server_default='0'),
        sa.Column('created_at',        sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
        sa.Column('updated_at',        sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_certificates_tenant_id', 'certificates', ['tenant_id'])
    op.create_index('ix_certificates_tenant_slug', 'certificates', ['tenant_slug'])
    op.create_index('ix_certificates_tenant_visible', 'certificates', ['tenant_id', 'is_visible'])
    op.create_index('ix_certificates_tenant_order', 'certificates', ['tenant_id', 'display_order'])
    op.create_index('ix_certificates_tenant_featured', 'certificates', ['tenant_id', 'is_featured'])


def downgrade():
    op.drop_index('ix_certificates_tenant_featured', table_name='certificates')
    op.drop_index('ix_certificates_tenant_order', table_name='certificates')
    op.drop_index('ix_certificates_tenant_visible', table_name='certificates')
    op.drop_index('ix_certificates_tenant_slug', table_name='certificates')
    op.drop_index('ix_certificates_tenant_id', table_name='certificates')
    op.drop_table('certificates')
