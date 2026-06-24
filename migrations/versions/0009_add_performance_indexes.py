"""add_performance_indexes

Revision ID: 0009_add_performance_indexes
Revises: 0008_add_user_security_fields
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '0009_add_performance_indexes'
down_revision = '0008_add_user_security_fields'
branch_labels = None
depends_on = None


def _index_exists(inspector, table_name, index_name):
    return any(
        index['name'] == index_name
        for index in inspector.get_indexes(table_name)
    )


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    index_definitions = [
        ('projects', 'ix_projects_tenant_category_status', ['tenant_slug', 'category', 'status']),
        ('testimonials', 'ix_testimonials_tenant_visible', ['tenant_slug', 'is_visible']),
        ('inquiries', 'ix_inquiries_tenant_sender_read', ['tenant_slug', 'sender', 'is_read']),
        ('subscriptions', 'ix_subscriptions_status_expires_at', ['status', 'expires_at']),
        ('payment_instructions', 'ix_payment_instructions_tenant_active', ['tenant_id', 'is_active']),
        ('payment_submissions', 'ix_payment_submissions_status_submitted_at', ['status', 'submitted_at']),
        ('profile', 'ix_profile_updated_at', ['updated_at']),
        ('profile', 'ix_profile_is_available', ['is_available']),
        ('skills', 'ix_skills_tenant_visible', ['tenant_slug', 'is_visible']),
        ('users', 'ix_users_tenant_admin', ['tenant_slug', 'is_admin']),
    ]

    for table_name, index_name, columns in index_definitions:
        if inspector.has_table(table_name) and not _index_exists(inspector, table_name, index_name):
            op.create_index(index_name, table_name, columns)


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    index_names = [
        ('projects', 'ix_projects_tenant_category_status'),
        ('testimonials', 'ix_testimonials_tenant_visible'),
        ('inquiries', 'ix_inquiries_tenant_sender_read'),
        ('subscriptions', 'ix_subscriptions_status_expires_at'),
        ('payment_instructions', 'ix_payment_instructions_tenant_active'),
        ('payment_submissions', 'ix_payment_submissions_status_submitted_at'),
        ('profile', 'ix_profile_updated_at'),
        ('profile', 'ix_profile_is_available'),
        ('skills', 'ix_skills_tenant_visible'),
        ('users', 'ix_users_tenant_admin'),
    ]

    for table_name, index_name in index_names:
        if inspector.has_table(table_name) and _index_exists(inspector, table_name, index_name):
            op.drop_index(index_name, table_name=table_name)
