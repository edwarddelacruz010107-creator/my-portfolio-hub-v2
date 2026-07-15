"""add tenant-bind founder dashboard read indexes

Revision ID: 0002_founder_dashboard_indexes
Revises: 0001_tenant_schema_baseline
"""
from alembic import op


revision = "0002_founder_dashboard_indexes"
down_revision = "0001_tenant_schema_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_profile_available_tenant", "profile", ["is_available", "tenant_id"])
    op.create_index("ix_projects_tenant_status_updated", "projects", ["tenant_id", "status", "updated_at"])


def downgrade():
    op.drop_index("ix_projects_tenant_status_updated", table_name="projects")
    op.drop_index("ix_profile_available_tenant", table_name="profile")
