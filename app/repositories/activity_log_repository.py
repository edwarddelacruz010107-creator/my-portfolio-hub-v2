"""
app/repositories/activity_log_repository.py — ActivityLog lookups (Phase 4b, item 3)

Scope: provides `activity_log_repository.query` escape hatch so app/admin
call sites can be written as `_tenant_slug_filter(activity_log_repository.query)...`
instead of `_tenant_slug_filter(ActivityLog.query)...` — identical chain
semantics, repository-layer ownership.

No named methods: every ActivityLog.query call site in app/admin is a unique,
multi-condition chain (ordering, status filters, counts, limits). Named
methods would relocate one-off logic without reducing duplication.
"""
from __future__ import annotations

from app.models.portfolio import ActivityLog
from app.repositories.base import BaseRepository


class ActivityLogRepository(BaseRepository[ActivityLog]):
    def __init__(self):
        super().__init__(ActivityLog)


activity_log_repository = ActivityLogRepository()
