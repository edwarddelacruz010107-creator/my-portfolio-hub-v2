"""
app/repositories/service_repository.py — Service lookups (Phase 4b, item 3)

Scope: provides `service_repository.query` escape hatch so app/admin
call sites can be written as `_tenant_slug_filter(service_repository.query)...`
instead of `_tenant_slug_filter(Service.query)...` — identical chain
semantics, repository-layer ownership.

No named methods: every Service.query call site in app/admin is a unique,
multi-condition chain (ordering, status filters, counts, limits). Named
methods would relocate one-off logic without reducing duplication.
"""
from __future__ import annotations

from app.models.portfolio import Service
from app.repositories.base import BaseRepository


class ServiceRepository(BaseRepository[Service]):
    def __init__(self):
        super().__init__(Service)


service_repository = ServiceRepository()
