"""
app/repositories/tenant_repository.py — Tenant lookups (Phase 4)

Scope for this pass: slug-based lookup (the single most repeated pattern
across the codebase — 30+ call sites use `Tenant.query.filter_by(slug=...)
.first()` verbatim). Status-filtered listing and other multi-condition
queries are intentionally left inline at their call sites per
PHASE4_AUDIT.md until reviewed individually.
"""
from __future__ import annotations

from typing import Optional

from app.models import Tenant
from app.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    def __init__(self):
        super().__init__(Tenant)

    def get_by_slug(self, slug: str) -> Optional[Tenant]:
        return self.model.query.filter_by(slug=slug).first()

    def slug_exists(self, slug: str) -> bool:
        return self.get_by_slug(slug) is not None


tenant_repository = TenantRepository()
