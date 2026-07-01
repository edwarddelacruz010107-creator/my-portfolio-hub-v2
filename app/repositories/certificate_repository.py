"""
app/repositories/certificate_repository.py — Certificate lookups (Certificates
& Badges feature, v6.7)

Follows the exact convention established by TestimonialRepository
(app/repositories/testimonial_repository.py): a `.query` escape hatch for
the multi-condition, tenant-scoped chains that live in admin routes and the
public portfolio routes, plus a small set of named methods for lookups that
recur across >1 call site.
"""
from __future__ import annotations

from app.models.portfolio import Certificate
from app.repositories.base import BaseRepository


class CertificateRepository(BaseRepository[Certificate]):
    def __init__(self):
        super().__init__(Certificate)

    # ------------------------------------------------------------------
    # Named methods — used at more than one call site, so centralized here
    # rather than left as ad-hoc chains (see base.py CONTRACT docstring).
    # ------------------------------------------------------------------
    def list_for_tenant(self, tenant_slug: str, *, visible_only: bool = False):
        """All certificates for a tenant, ordered for admin/public display."""
        q = Certificate.query.filter_by(tenant_slug=tenant_slug)
        if visible_only:
            q = q.filter_by(is_visible=True)
        return q.order_by(Certificate.display_order.asc(), Certificate.id.asc())

    def featured_for_tenant(self, tenant_slug: str, *, limit: int | None = None):
        """Visible + featured certificates for public portfolio rendering."""
        q = (
            Certificate.query
            .filter_by(tenant_slug=tenant_slug, is_visible=True, is_featured=True)
            .order_by(Certificate.display_order.asc(), Certificate.id.asc())
        )
        if limit:
            q = q.limit(limit)
        return q

    def get_for_tenant(self, certificate_id: int, tenant_slug: str) -> Certificate | None:
        """Fetch by id scoped to tenant_slug — the IDOR-safe read path.

        Prefer this (or `_require_tenant_object` on top of a plain get) over
        `db.session.get(Certificate, id)` at any new call site so tenant
        isolation can never be forgotten by accident.
        """
        return Certificate.query.filter_by(id=certificate_id, tenant_slug=tenant_slug).first()


certificate_repository = CertificateRepository()
