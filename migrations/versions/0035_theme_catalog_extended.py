"""
0035 — Theme Catalog Extended (v6.5)

Adds to theme_catalog_entries:
  thumbnail_url     — main card thumbnail (uploaded or URL)
  banner_url        — hero banner for preview modal
  preview_images    — JSON array of screenshot URLs
  theme_author      — author / studio name
  theme_version     — semantic version string
  theme_tags        — JSON array of string tags
  feature_matrix    — JSON object: { hero:true, blog:false, ... }
  is_featured       — featured / spotlight flag
  install_count     — running total of tenant activations

SAFE: purely additive. No existing columns modified. No data removed.

Down-revision: 0034_theme_catalog_entries
"""

from alembic import op
import sqlalchemy as sa

revision      = '0035_theme_catalog_extended'
down_revision = '0034_theme_catalog_entries'
branch_labels = None
depends_on    = None


def upgrade():
    with op.batch_alter_table('theme_catalog_entries') as batch_op:
        batch_op.add_column(sa.Column('thumbnail_url',   sa.String(512),  nullable=True))
        batch_op.add_column(sa.Column('banner_url',      sa.String(512),  nullable=True))
        batch_op.add_column(sa.Column('preview_images',  sa.Text(),       nullable=True))  # JSON []
        batch_op.add_column(sa.Column('theme_author',    sa.String(120),  nullable=True))
        batch_op.add_column(sa.Column('theme_version',   sa.String(30),   nullable=True))
        batch_op.add_column(sa.Column('theme_tags',      sa.Text(),       nullable=True))  # JSON []
        batch_op.add_column(sa.Column('feature_matrix',  sa.Text(),       nullable=True))  # JSON {}
        batch_op.add_column(sa.Column('is_featured',     sa.Boolean(),    nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('install_count',   sa.Integer(),    nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('theme_catalog_entries') as batch_op:
        for col in ('thumbnail_url', 'banner_url', 'preview_images', 'theme_author',
                    'theme_version', 'theme_tags', 'feature_matrix', 'is_featured', 'install_count'):
            batch_op.drop_column(col)
