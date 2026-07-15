"""add proven founder-dashboard read indexes

Revision ID: 0063
Revises: 0062
"""
from alembic import op


revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


INDEXES = (
    ("ix_tenants_created_at", "tenants", ("created_at",)),
    ("ix_tenants_plan_state", "tenants", ("plan", "subscription_state")),
    ("ix_subscription_status_tenant_occurred", "subscription_status_events", ("tenant_id", "occurred_at", "to_status")),
    ("ix_inquiries_tenant_created", "inquiries", ("tenant_id", "created_at")),
    ("ix_ai_usage_tenant_created", "ai_usage_requests", ("tenant_id", "created_at")),
    ("ix_ai_usage_provider_created", "ai_usage_requests", ("provider_code", "created_at")),
    ("ix_activitylog_tenant_created", "activity_log", ("tenant_id", "created_at")),
    ("ix_notification_deliveries_status_updated", "notification_deliveries", ("status", "updated_at")),
    ("ix_webhook_processed_received", "webhook_events", ("processed", "received_at")),
)


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns))


def downgrade():
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
