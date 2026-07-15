"""Unified notification, per-user receipt, and delivery-outbox models."""
from __future__ import annotations

import uuid

from app.extensions import db
from app.utils.datetime_utils import utc_now


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (
        db.UniqueConstraint("dedupe_key", name="uq_notifications_dedupe_key"),
        db.UniqueConstraint("legacy_notification_id", name="uq_notifications_legacy_id"),
        db.CheckConstraint("recipient_type IN ('tenant','user','role')", name="ck_notifications_recipient_type"),
        db.CheckConstraint("priority IN ('low','normal','high','urgent')", name="ck_notifications_priority"),
        db.CheckConstraint(
            "(recipient_type = 'tenant' AND tenant_id IS NOT NULL) OR "
            "(recipient_type = 'user' AND recipient_id IS NOT NULL) OR "
            "(recipient_type = 'role' AND recipient_role IS NOT NULL)",
            name="ck_notifications_recipient_target",
        ),
        db.Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
        db.Index("ix_notifications_role_created", "recipient_role", "created_at"),
        db.Index("ix_notifications_event_created", "event_type", "created_at"),
        db.Index("ix_notifications_expiry", "expires_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    recipient_type = db.Column(db.String(20), nullable=False)
    recipient_id = db.Column(db.String(64), nullable=True)
    recipient_role = db.Column(db.String(50), nullable=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    actor_type = db.Column(db.String(30), nullable=True)
    actor_id = db.Column(db.String(64), nullable=True)
    event_type = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(60), nullable=True)
    entity_id = db.Column(db.String(100), nullable=True)
    title_template_key = db.Column(db.String(120), nullable=False)
    body_template_key = db.Column(db.String(120), nullable=False)
    safe_parameters = db.Column(db.JSON, nullable=False, default=dict)
    action_route = db.Column(db.String(120), nullable=True)
    action_parameters = db.Column(db.JSON, nullable=False, default=dict)
    priority = db.Column(db.String(20), nullable=False, default="normal")
    dedupe_key = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True)
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    legacy_notification_id = db.Column(db.Integer, nullable=True)

    target_tenant = db.relationship("Tenant", lazy="joined")
    deliveries = db.relationship(
        "NotificationDelivery", back_populates="notification",
        cascade="all, delete-orphan", passive_deletes=True, lazy="selectin",
    )


class NotificationReceipt(db.Model):
    """Per-user read/archive state for tenant and role-scoped notifications."""

    __tablename__ = "notification_receipts"
    __table_args__ = (
        db.UniqueConstraint("notification_id", "user_id", name="uq_notification_receipts_user"),
        db.Index("ix_notification_receipts_user_read", "user_id", "read_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_id = db.Column(db.String(36), db.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True)
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class NotificationDelivery(db.Model):
    """Durable channel attempt/outbox record."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        db.UniqueConstraint("notification_id", "channel", name="uq_notification_deliveries_channel"),
        db.CheckConstraint("channel IN ('in_app','email')", name="ck_notification_deliveries_channel"),
        db.CheckConstraint("status IN ('pending','processing','sent','failed','dead')", name="ck_notification_deliveries_status"),
        db.Index("ix_notification_deliveries_pending", "status", "next_attempt_at"),
        db.Index("ix_notification_deliveries_status_updated", "status", "updated_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_id = db.Column(db.String(36), db.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False)
    channel = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime(timezone=True), nullable=True)
    provider_message_id = db.Column(db.String(255), nullable=True)
    error_class = db.Column(db.String(80), nullable=True)
    error_detail = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)

    notification = db.relationship("Notification", back_populates="deliveries", lazy="joined")
