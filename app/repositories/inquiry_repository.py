"""
app/repositories/inquiry_repository.py — Inquiry lookups (Phase 4b, item 3)

Scope: provides `inquiry_repository.query` escape hatch so app/admin
call sites can be written as `_tenant_slug_filter(inquiry_repository.query)...`
instead of `_tenant_slug_filter(Inquiry.query)...` — identical chain
semantics, repository-layer ownership.

No named methods: every Inquiry.query call site in app/admin is a unique,
multi-condition chain (ordering, status filters, counts, limits). Named
methods would relocate one-off logic without reducing duplication.
"""
from __future__ import annotations

from app.models.portfolio import Inquiry
from app.repositories.base import BaseRepository


class InquiryRepository(BaseRepository[Inquiry]):
    def __init__(self):
        super().__init__(Inquiry)


inquiry_repository = InquiryRepository()
