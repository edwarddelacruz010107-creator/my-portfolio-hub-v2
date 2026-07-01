"""
app/repositories/subscription_repository.py — Subscription lookups (Phase 4b, item 3)

Scope: provides `subscription_repository.query` escape hatch so app/admin
call sites can be written as `_tenant_slug_filter(subscription_repository.query)...`
instead of `_tenant_slug_filter(Subscription.query)...` — identical chain
semantics, repository-layer ownership.

No named methods: every Subscription.query call site in app/admin is a unique,
multi-condition chain (ordering, status filters, counts, limits). Named
methods would relocate one-off logic without reducing duplication.
"""
from __future__ import annotations

from app.models.portfolio import Subscription
from app.repositories.base import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    def __init__(self):
        super().__init__(Subscription)


subscription_repository = SubscriptionRepository()
