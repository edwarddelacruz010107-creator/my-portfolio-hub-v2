"""add theme customization drafts and published history

Revision ID: 0061
Revises: 0060
"""
from alembic import op
import sqlalchemy as sa


revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "theme_customization_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("theme_id", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("restored_from_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_number > 0", name="ck_theme_customization_version_positive"),
        sa.CheckConstraint("source IN ('publish', 'rollback')", name="ck_theme_customization_version_source"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["restored_from_id"], ["theme_customization_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "theme_id", "version_number",
            name="uq_theme_customization_version_number",
        ),
    )
    op.create_index(
        "ix_theme_customization_version_tenant_theme_created",
        "theme_customization_versions",
        ["tenant_id", "theme_id", "created_at"],
    )
    op.create_table(
        "theme_customization_drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("theme_id", sa.String(length=64), nullable=False),
        sa.Column("base_version_id", sa.String(length=36), nullable=True),
        sa.Column("tokens", sa.JSON(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_version_id"], ["theme_customization_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "theme_id", name="uq_theme_customization_draft_tenant_theme"),
    )
    op.create_index("ix_theme_customization_drafts_tenant_id", "theme_customization_drafts", ["tenant_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_theme_customization_version_mutation() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'theme customization versions are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(
            "CREATE TRIGGER trg_theme_customization_versions_append_only "
            "BEFORE UPDATE OR DELETE ON theme_customization_versions "
            "FOR EACH ROW EXECUTE FUNCTION reject_theme_customization_version_mutation()"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_theme_customization_versions_append_only ON theme_customization_versions")
        op.execute("DROP FUNCTION IF EXISTS reject_theme_customization_version_mutation()")
    op.drop_index("ix_theme_customization_drafts_tenant_id", table_name="theme_customization_drafts")
    op.drop_table("theme_customization_drafts")
    op.drop_index(
        "ix_theme_customization_version_tenant_theme_created",
        table_name="theme_customization_versions",
    )
    op.drop_table("theme_customization_versions")
