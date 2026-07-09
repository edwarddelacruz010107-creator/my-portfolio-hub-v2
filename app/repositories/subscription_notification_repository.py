"""
app/repositories/subscription_notification_repository.py — SubscriptionNotification lookups (Phase 4b, item 4)

Scope: escape-hatch `subscription_notification_repository.query` for superadmin call sites.
Every SubscriptionNotification.query occurrence in app/superadmin is a unique, multi-condition
chain — no named methods added per audit policy (no duplication to collapse).
"""
from __future__ import annotations

from app.models.portfolio import SubscriptionNotification
from app.repositories.base import BaseRepository


class SubscriptionNotificationRepository(BaseRepository[SubscriptionNotification]):
    def __init__(self):
        super().__init__(SubscriptionNotification)


subscription_notification_repository = SubscriptionNotificationRepository()
