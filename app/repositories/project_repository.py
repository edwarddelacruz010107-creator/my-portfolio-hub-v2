"""
app/repositories/project_repository.py — Project lookups (Phase 4b, item 2)

Scope for this pass: the global (non-tenant-scoped) `slug` uniqueness check
that's duplicated verbatim at two call sites (`app/admin/__init__.py`,
`app/__init__.py`) during new-project slug generation. Promoted to a named
method per PHASE4_AUDIT.md's own criterion: a query repeated at >1 call
site is a named-method candidate; a one-off chain is not.

Every other `Project.query` call site in `app/admin/__init__.py` is a
distinct, tenant-scoped, multi-condition chain built on top of the
`_tenant_slug_filter()` helper (status filters, ordering, pagination,
ilike search, counts). Per the same audit's classification, those are
NOT collapsed into named repository methods here — that would relocate
one-off logic without reducing duplication and risks behavior drift if a
filter chain isn't reproduced byte-for-byte. They are migrated by
swapping the bare `Project.query` base for the `.query` escape hatch
(`project_repository.query`), which is a pure rename — `BaseRepository
.query` is `self.model.query`, so the resulting expression is identical
to the original at every chained call site.
"""
from __future__ import annotations

from app.models import Project
from app.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    def __init__(self):
        super().__init__(Project)

    def slug_exists(self, slug: str) -> bool:
        """1:1 wrapper around Project.query.filter_by(slug=slug).first()
        is not None — duplicated at app/admin/__init__.py and
        app/__init__.py inside slug-uniqueness-check loops. NOTE: this is
        a GLOBAL slug check (no tenant_slug filter), matching the
        original call sites exactly — do not add tenant scoping here
        without auditing both call sites first."""
        return self.model.query.filter_by(slug=slug).first() is not None


project_repository = ProjectRepository()
