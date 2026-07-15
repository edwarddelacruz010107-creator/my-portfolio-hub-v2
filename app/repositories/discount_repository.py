"""
app/repositories/discount_repository.py — Discount campaign & redemption
lookups (v6.6 — Discount & Promotion Manager, Phase 1).

Unlike most repositories in this layer (which are 1:1 escape-hatch wrappers
around pre-existing call sites — see base.py docstring), this is new code
with no legacy call sites to preserve byte-for-byte. Named methods here are
real query consolidation: discount_service.py, the superadmin CRUD routes,
and checkout all need the same lookups (active campaign by code, tenant
redemption count, etc.), so they're defined once.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.extensions import db
from app.models.core import DiscountCampaign, DiscountRedemption
from app.repositories.base import BaseRepository


class DiscountCampaignRepository(BaseRepository[DiscountCampaign]):
    def __init__(self):
        super().__init__(DiscountCampaign)

    def get_by_code(self, code: str) -> Optional[DiscountCampaign]:
        """Case-insensitive coupon code lookup."""
        if not code:
            return None
        return (
            DiscountCampaign.query
            .filter(db.func.lower(DiscountCampaign.code) == code.strip().lower())
            .first()
        )

    def get_for_update(self, campaign_id: int) -> Optional[DiscountCampaign]:
        """Lock one campaign while validating and consuming a redemption.

        PostgreSQL serializes last-use contenders on this row. SQLite is used
        only for local tests and ignores row-level locking semantics.
        """
        query = DiscountCampaign.query.filter_by(id=campaign_id)
        if db.session.get_bind().dialect.name != "sqlite":
            query = query.with_for_update()
        return query.first()

    def list_active(self) -> list[DiscountCampaign]:
        """Active campaigns whose date window currently includes now.

        Usage-limit exhaustion is NOT checked here (that's a per-request
        concern handled by the service, since usage_count changes between
        page renders) — this is for admin listing / auto-apply candidate
        gathering only.
        """
        now = datetime.now(timezone.utc)
        return (
            DiscountCampaign.query
            .filter(DiscountCampaign.is_active.is_(True))
            .filter(db.or_(DiscountCampaign.starts_at.is_(None), DiscountCampaign.starts_at <= now))
            .filter(db.or_(DiscountCampaign.expires_at.is_(None), DiscountCampaign.expires_at > now))
            .order_by(DiscountCampaign.created_at.desc())
            .all()
        )

    def list_auto_apply_candidates(self, plan_slug: Optional[str] = None) -> list[DiscountCampaign]:
        """Active, global (couponless) campaigns eligible for auto-apply,
        optionally narrowed to a specific plan (NULL plan_slug = all plans)."""
        campaigns = [c for c in self.list_active() if c.is_global]
        if plan_slug:
            campaigns = [c for c in campaigns if c.plan_slug in (None, plan_slug)]
        return campaigns

    def list_all_ordered(self) -> list[DiscountCampaign]:
        return DiscountCampaign.query.order_by(DiscountCampaign.created_at.desc()).all()

    def code_exists(self, code: str, *, exclude_id: Optional[int] = None) -> bool:
        if not code:
            return False
        q = DiscountCampaign.query.filter(
            db.func.lower(DiscountCampaign.code) == code.strip().lower()
        )
        if exclude_id is not None:
            q = q.filter(DiscountCampaign.id != exclude_id)
        return db.session.query(q.exists()).scalar()


class DiscountRedemptionRepository(BaseRepository[DiscountRedemption]):
    def __init__(self):
        super().__init__(DiscountRedemption)

    def count_for_tenant(self, campaign_id: int, tenant_id: int) -> int:
        return DiscountRedemption.query.filter_by(
            campaign_id=campaign_id, tenant_id=tenant_id
        ).count()

    def tenant_has_any_redemption(self, tenant_id: int) -> bool:
        """Used for first_time_only campaign eligibility — has this tenant
        ever redeemed ANY discount before (not just this campaign)?"""
        return db.session.query(
            DiscountRedemption.query.filter_by(tenant_id=tenant_id).exists()
        ).scalar()

    def list_for_campaign(self, campaign_id: int) -> list[DiscountRedemption]:
        return (
            DiscountRedemption.query
            .filter_by(campaign_id=campaign_id)
            .order_by(DiscountRedemption.redeemed_at.desc())
            .all()
        )

    def list_for_tenant(self, tenant_id: int) -> list[DiscountRedemption]:
        return (
            DiscountRedemption.query
            .filter_by(tenant_id=tenant_id)
            .order_by(DiscountRedemption.redeemed_at.desc())
            .all()
        )

    def total_revenue_impact(self, campaign_id: Optional[int] = None) -> float:
        """Sum of amount_discounted across redemptions, optionally scoped
        to one campaign. Used by the superadmin analytics dashboard."""
        q = db.session.query(db.func.coalesce(db.func.sum(DiscountRedemption.amount_discounted), 0))
        if campaign_id is not None:
            q = q.filter(DiscountRedemption.campaign_id == campaign_id)
        return float(q.scalar() or 0)


discount_campaign_repository = DiscountCampaignRepository()
discount_redemption_repository = DiscountRedemptionRepository()
