"""
0034 — Theme Catalog Entries (SuperAdmin Theme CRUD, v6.4)

Adds:
  theme_catalog_entries:   (new table)
    id, slug (unique), name, description, category,
    is_active, is_premium, required_plan, sort_order,
    created_at, updated_at

This is a pure DB overlay on top of the existing filesystem-discovered
themes (themes/<slug>/theme.json). No existing tables or columns are
touched. SuperAdmin can now manage active/inactive, premium gating,
required plan, and listing order for installed themes without editing
theme.json by hand. Themes with no row here keep working exactly as
before (ThemeEngine falls back to theme.json metadata).

SAFE: additive only. No existing columns modified. No data destroyed.

Down-revision: 0033_storage_quota_plan_caps
"""

from alembic import op
import sqlalchemy as sa

revision      = '0034_theme_catalog_entries'
down_revision = '0033_storage_quota_plan_caps'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'theme_catalog_entries',
        sa.Column('id',            sa.Integer(),    primary_key=True),
        sa.Column('slug',          sa.String(64),   nullable=False, unique=True),
        sa.Column('name',          sa.String(150),  nullable=True),
        sa.Column('description',  sa.Text(),       nullable=True),
        sa.Column('category',      sa.String(60),   nullable=True),
        sa.Column('is_active',     sa.Boolean(),    nullable=False, server_default='1'),
        sa.Column('is_premium',    sa.Boolean(),    nullable=True),
        sa.Column('required_plan', sa.String(20),   nullable=True),
        sa.Column('sort_order',    sa.Integer(),    nullable=False, server_default='0'),
        sa.Column('created_at',    sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
        sa.Column('updated_at',    sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.func.now()),
    )
    op.create_index('ix_theme_catalog_entries_slug', 'theme_catalog_entries', ['slug'])


def downgrade():
    op.drop_index('ix_theme_catalog_entries_slug', table_name='theme_catalog_entries')
    op.drop_table('theme_catalog_entries')
