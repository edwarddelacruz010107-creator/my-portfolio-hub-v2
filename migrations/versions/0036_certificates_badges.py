"""Add certificates and badges tables

Revision ID: 0036_certificates_badges
Revises: 0035_theme_catalog_metadata
Create Date: 2025-08-01

"""
from alembic import op
import sqlalchemy as sa

revision = "0036_certificates_badges"
down_revision = "0035_theme_catalog_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "certificates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("credential_id", sa.String(200), nullable=True),
        sa.Column("credential_url", sa.String(512), nullable=True),
        sa.Column("image_filename", sa.String(255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_certificates_tenant_id", "certificates", ["tenant_id"])

    op.create_table(
        "badges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("image_filename", sa.String(255), nullable=True),
        sa.Column("image_url_external", sa.String(512), nullable=True),
        sa.Column("verification_url", sa.String(512), nullable=True),
        sa.Column("issued_date", sa.Date(), nullable=True),
        sa.Column("skill_tag", sa.String(100), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_badges_tenant_id", "badges", ["tenant_id"])


def downgrade():
    op.drop_index("ix_badges_tenant_id", table_name="badges")
    op.drop_table("badges")
    op.drop_index("ix_certificates_tenant_id", table_name="certificates")
    op.drop_table("certificates")
