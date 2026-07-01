"""
app/repositories/skill_repository.py — Skill lookups (Phase 4b, item 3)

Scope: provides `skill_repository.query` escape hatch so app/admin
call sites can be written as `_tenant_slug_filter(skill_repository.query)...`
instead of `_tenant_slug_filter(Skill.query)...` — identical chain
semantics, repository-layer ownership.

No named methods: every Skill.query call site in app/admin is a unique,
multi-condition chain (ordering, status filters, counts, limits). Named
methods would relocate one-off logic without reducing duplication.
"""
from __future__ import annotations

from app.models.portfolio import Skill
from app.repositories.base import BaseRepository


class SkillRepository(BaseRepository[Skill]):
    def __init__(self):
        super().__init__(Skill)


skill_repository = SkillRepository()
