"""
app/repositories/testimonial_repository.py — Testimonial lookups (Phase 4b, item 3)

Scope: provides `testimonial_repository.query` escape hatch so app/admin
call sites can be written as `_tenant_slug_filter(testimonial_repository.query)...`
instead of `_tenant_slug_filter(Testimonial.query)...` — identical chain
semantics, repository-layer ownership.

No named methods: every Testimonial.query call site in app/admin is a unique,
multi-condition chain (ordering, status filters, counts, limits). Named
methods would relocate one-off logic without reducing duplication.
"""
from __future__ import annotations

from app.models.portfolio import Testimonial
from app.repositories.base import BaseRepository


class TestimonialRepository(BaseRepository[Testimonial]):
    def __init__(self):
        super().__init__(Testimonial)


testimonial_repository = TestimonialRepository()
