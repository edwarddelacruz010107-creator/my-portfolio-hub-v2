"""
app/repositories/base.py — Repository layer foundation (Phase 4)

CONTRACT
--------
A repository wraps `Model.query` access for ONE model. It does not change
behavior: every method here is a 1:1 wrapper around the exact SQLAlchemy
query expression it replaces at the call site (same filters, same .first()
/.all()/.count() semantics, same exception behavior incl. get_or_404).

Repositories are intentionally NOT a generic ORM abstraction layer — they
exist to:
  1. Centralize the most common lookups (get-by-id, get-by-slug, get-by-
     unique-field) so they're defined once instead of duplicated across
     20+ call sites.
  2. Give future query optimization (eager loading, caching, read-replica
     routing) a single choke point per model instead of N scattered ones.
  3. Make call sites read as intent ("find the user by email") instead of
     ORM mechanics ("User.query.filter_by(email=...).first()").

Ad-hoc / multi-condition queries that exist at exactly one call site are
NOT forced into a repository method — that would just relocate the same
one-off logic without reducing duplication, and risks behavior drift if
the wrapping isn't byte-for-byte faithful to the original filter chain.
Those stay as direct `Model.query...` expressions at the call site by
design; see PHASE4_AUDIT.md for the full classification.
"""
from __future__ import annotations

from typing import Any, Generic, Optional, Type, TypeVar

from app.extensions import db

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Generic wrapper around a single SQLAlchemy model's `.query`."""

    model: Type[ModelT]

    def __init__(self, model: Type[ModelT]):
        self.model = model

    # ------------------------------------------------------------------
    # Primitive passthroughs — identical semantics to direct ORM calls
    # ------------------------------------------------------------------
    @property
    def query(self):
        """Escape hatch: returns Model.query for call sites that need a
        complex/one-off filter chain not worth a named method. Prefer a
        named method below when the same lookup appears at >1 call site."""
        return self.model.query

    def get(self, pk: Any) -> Optional[ModelT]:
        return self.model.query.get(pk)

    def get_or_404(self, pk: Any) -> ModelT:
        return self.model.query.get_or_404(pk)

    def get_by(self, **filters: Any) -> Optional[ModelT]:
        return self.model.query.filter_by(**filters).first()

    def get_by_or_404(self, **filters: Any) -> ModelT:
        """1:1 wrapper around Model.query.filter_by(**filters).first_or_404()."""
        return self.model.query.filter_by(**filters).first_or_404()

    def list_by(self, **filters: Any) -> list[ModelT]:
        return self.model.query.filter_by(**filters).all()

    def exists(self, **filters: Any) -> bool:
        return db.session.query(
            self.model.query.filter_by(**filters).exists()
        ).scalar()

    def count(self, **filters: Any) -> int:
        if filters:
            return self.model.query.filter_by(**filters).count()
        return self.model.query.count()

    def all(self) -> list[ModelT]:
        return self.model.query.all()

    # ------------------------------------------------------------------
    # Mutation helpers — thin wrappers, caller still controls commit()
    # to preserve existing transaction-boundary behavior at call sites.
    # ------------------------------------------------------------------
    def add(self, instance: ModelT, *, flush: bool = False) -> ModelT:
        db.session.add(instance)
        if flush:
            db.session.flush()
        return instance

    def delete(self, instance: ModelT) -> None:
        db.session.delete(instance)
