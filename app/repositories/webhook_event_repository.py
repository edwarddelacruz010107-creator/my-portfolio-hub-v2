"""
app/repositories/webhook_event_repository.py — WebhookEvent lookups (Phase 4b, item 4)

Scope: escape-hatch `webhook_event_repository.query` for superadmin call sites.
Every WebhookEvent.query occurrence in app/superadmin is a unique, multi-condition
chain — no named methods added per audit policy (no duplication to collapse).
"""
from __future__ import annotations

from app.models import WebhookEvent
from app.repositories.base import BaseRepository


class WebhookEventRepository(BaseRepository[WebhookEvent]):
    def __init__(self):
        super().__init__(WebhookEvent)


webhook_event_repository = WebhookEventRepository()
