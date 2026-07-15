"""add unified notifications, receipts, and delivery outbox

Revision ID: 0058
Revises: 0057
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recipient_type", sa.String(length=20), nullable=False),
        sa.Column("recipient_id", sa.String(length=64), nullable=True),
        sa.Column("recipient_role", sa.String(length=50), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("actor_type", sa.String(length=30), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=60), nullable=True),
        sa.Column("entity_id", sa.String(length=100), nullable=True),
        sa.Column("title_template_key", sa.String(length=120), nullable=False),
        sa.Column("body_template_key", sa.String(length=120), nullable=False),
        sa.Column("safe_parameters", sa.JSON(), nullable=False),
        sa.Column("action_route", sa.String(length=120), nullable=True),
        sa.Column("action_parameters", sa.JSON(), nullable=False),
        sa.Column("priority", sa.String(length=20), server_default="normal", nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legacy_notification_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("recipient_type IN ('tenant','user','role')", name="ck_notifications_recipient_type"),
        sa.CheckConstraint("priority IN ('low','normal','high','urgent')", name="ck_notifications_priority"),
        sa.CheckConstraint("(recipient_type = 'tenant' AND tenant_id IS NOT NULL) OR (recipient_type = 'user' AND recipient_id IS NOT NULL) OR (recipient_type = 'role' AND recipient_role IS NOT NULL)", name="ck_notifications_recipient_target"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
        sa.UniqueConstraint("legacy_notification_id", name="uq_notifications_legacy_id"),
    )
    op.create_index("ix_notifications_tenant_created", "notifications", ["tenant_id", "created_at"])
    op.create_index("ix_notifications_role_created", "notifications", ["recipient_role", "created_at"])
    op.create_index("ix_notifications_event_created", "notifications", ["event_type", "created_at"])
    op.create_index("ix_notifications_expiry", "notifications", ["expires_at"])

    op.create_table(
        "notification_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("notification_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "user_id", name="uq_notification_receipts_user"),
    )
    op.create_index("ix_notification_receipts_user_read", "notification_receipts", ["user_id", "read_at"])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("notification_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_class", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("channel IN ('in_app','email')", name="ck_notification_deliveries_channel"),
        sa.CheckConstraint("status IN ('pending','processing','sent','failed','dead')", name="ck_notification_deliveries_status"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("notification_id", "channel", name="uq_notification_deliveries_channel"),
    )
    op.create_index("ix_notification_deliveries_pending", "notification_deliveries", ["status", "next_attempt_at"])

    # Preserve legacy tenant notification content and shared read state. New
    # producers switch to NotificationService in the same release.
    bind = op.get_bind()
    legacy = sa.table(
        "subscription_notifications",
        sa.column("id"), sa.column("tenant_id"), sa.column("subscription_id"),
        sa.column("notification_type"), sa.column("title"), sa.column("message"),
        sa.column("is_read"), sa.column("sent_via_email"), sa.column("created_at"),
        sa.column("read_at"),
    )
    rows = bind.execute(sa.select(legacy)).mappings().all()
    notifications = sa.table(
        "notifications",
        *[sa.column(name) for name in (
            "id", "recipient_type", "recipient_id", "recipient_role", "tenant_id",
            "actor_type", "actor_id", "event_type", "entity_type", "entity_id",
            "title_template_key", "body_template_key",
        )],
        sa.column("safe_parameters", sa.JSON()),
        sa.column("action_route"),
        sa.column("action_parameters", sa.JSON()),
        *[sa.column(name) for name in (
            "priority", "dedupe_key", "created_at", "read_at", "archived_at",
            "expires_at", "legacy_notification_id",
        )],
    )
    deliveries = sa.table(
        "notification_deliveries",
        *[sa.column(name) for name in (
            "id", "notification_id", "channel", "status", "attempt_count",
            "next_attempt_at", "provider_message_id", "error_class", "error_detail",
            "created_at", "updated_at", "sent_at",
        )],
    )
    for row in rows:
        notification_id = str(uuid.uuid4())
        created_at = row["created_at"] or datetime.now(timezone.utc)
        bind.execute(notifications.insert().values(
            id=notification_id, recipient_type="tenant", recipient_id=None,
            recipient_role=None, tenant_id=row["tenant_id"], actor_type="legacy",
            actor_id=None, event_type=f"legacy.{row['notification_type']}",
            entity_type="subscription" if row["subscription_id"] else None,
            entity_id=str(row["subscription_id"]) if row["subscription_id"] else None,
            title_template_key="legacy.literal", body_template_key="legacy.literal",
            safe_parameters={"title": row["title"], "message": row["message"]},
            action_route="admin.notifications", action_parameters={}, priority="normal",
            dedupe_key=f"legacy:subscription_notification:{row['id']}",
            created_at=created_at, read_at=row["read_at"] if row["is_read"] else None,
            archived_at=None, expires_at=None, legacy_notification_id=row["id"],
        ))
        bind.execute(deliveries.insert().values(
            id=str(uuid.uuid4()), notification_id=notification_id, channel="in_app",
            status="sent", attempt_count=1, next_attempt_at=None,
            provider_message_id=None, error_class=None, error_detail=None,
            created_at=created_at, updated_at=created_at, sent_at=created_at,
        ))
        if row["sent_via_email"]:
            bind.execute(deliveries.insert().values(
                id=str(uuid.uuid4()), notification_id=notification_id, channel="email",
                status="sent", attempt_count=1, next_attempt_at=None,
                provider_message_id=None, error_class=None, error_detail=None,
                created_at=created_at, updated_at=created_at, sent_at=created_at,
            ))


def downgrade():
    op.drop_table("notification_deliveries")
    op.drop_table("notification_receipts")
    op.drop_table("notifications")
