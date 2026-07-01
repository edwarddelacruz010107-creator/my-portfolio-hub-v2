"""
app/repositories/profile_repository.py — Profile lookups (Phase 4)

Scope for this pass: tenant_id-keyed lookup, used by bootstrap/seed
scripts. The tenant_slug-keyed variant (the dominant pattern inside
request-path code — app/admin, app/superadmin, app/main,
app/context_processors.py) is deliberately deferred: those call sites
sit on the tenant-isolation-critical path and are scheduled for
Phase 4b with per-blueprint review, not a bulk find/replace.
"""
from __future__ import annotations

from typing import Optional

from app.models import Profile
from app.repositories.base import BaseRepository


class ProfileRepository(BaseRepository[Profile]):
    def __init__(self):
        super().__init__(Profile)

    def get_by_tenant_id(self, tenant_id: int) -> Optional[Profile]:
        return self.model.query.filter_by(tenant_id=tenant_id).first()

    def get_by_tenant_id_or_404(self, tenant_id: int) -> Profile:
        return self.model.query.filter_by(tenant_id=tenant_id).first_or_404()

    def tenant_slug_exists(self, tenant_slug: str) -> bool:
        """Used by superadmin slug-uniqueness checks (2 call sites)."""
        return self.model.query.filter_by(tenant_slug=tenant_slug).first() is not None

    def get_by_tenant_slug(self, tenant_slug: str) -> Optional[Profile]:
        """Phase 4b — added for context_processors.py / app/main/__init__.py.
        1:1 wrapper around Profile.query.filter_by(tenant_slug=...).first().
        """
        return self.model.query.filter_by(tenant_slug=tenant_slug).first()

    def get_first(self) -> Optional[Profile]:
        """Phase 4b — cosmetic-only fallback used on superadmin/unauthenticated
        rendering paths. 1:1 wrapper around Profile.query.first(). Not for
        any tenant-isolation-sensitive call site (no admin write actions
        occur on the paths that use this)."""
        return self.model.query.first()


profile_repository = ProfileRepository()
