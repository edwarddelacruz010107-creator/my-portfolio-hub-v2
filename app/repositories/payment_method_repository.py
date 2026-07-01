"""
app/repositories/payment_method_repository.py — PaymentMethod lookups (Phase 4b, item 4)

Scope: escape-hatch `payment_method_repository.query` for superadmin call sites.
Every PaymentMethod.query occurrence in app/superadmin is a unique, multi-condition
chain — no named methods added per audit policy (no duplication to collapse).
"""
from __future__ import annotations

from app.models.portfolio import PaymentMethod
from app.repositories.base import BaseRepository


class PaymentMethodRepository(BaseRepository[PaymentMethod]):
    def __init__(self):
        super().__init__(PaymentMethod)


payment_method_repository = PaymentMethodRepository()
