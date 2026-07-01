"""
app/services/billing/discount_service.py — Discount & Promotion engine
(v6.6 — Discount & Promotion Manager, Phase 1).

This is the ONLY place discount math happens. Routes and checkout call
into here; nothing downstream recomputes a discounted price itself.

Flow:
    quote = quote_discount(tenant_id, plan, billing_cycle, code=...)
    # ... show quote.amount_after to the user, proceed to payment ...
    redeem_discount(tenant_id, subscription_id, quote)   # after payment succeeds

quote_discount() is READ-ONLY (no DB writes, safe to call repeatedly while
a user is editing their checkout form). redeem_discount() is the only
function that writes a DiscountRedemption row and increments usage_count,
and it must only be called once payment/activation has actually happened —
mirroring how activate_subscription() in billing.py is only called after
payment confirmation, not at form-submit time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from app.extensions import db
from app.models.core import DiscountCampaign, DiscountRedemption
from app.repositories.discount_repository import (
    discount_campaign_repository,
    discount_redemption_repository,
)
from app.utils import get_plan_price, normalize_plan_name

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DiscountError(Exception):
    """Base class for all discount validation failures. Safe to catch
    broadly at the route layer and surface `str(exc)` as a flash message —
    every subclass below sets a user-facing message."""


class InvalidCouponError(DiscountError):
    def __init__(self, message: str = "That coupon code isn't valid."):
        super().__init__(message)


class InactiveCampaignError(DiscountError):
    def __init__(self, message: str = "That coupon is no longer active."):
        super().__init__(message)


class ExpiredCampaignError(DiscountError):
    def __init__(self, message: str = "That coupon has expired."):
        super().__init__(message)


class NotYetStartedError(DiscountError):
    def __init__(self, message: str = "That coupon isn't active yet."):
        super().__init__(message)


class UsageLimitExceededError(DiscountError):
    def __init__(self, message: str = "That coupon has reached its usage limit."):
        super().__init__(message)


class PerTenantLimitExceededError(DiscountError):
    def __init__(self, message: str = "You've already used this coupon."):
        super().__init__(message)


class NotFirstTimeError(DiscountError):
    def __init__(self, message: str = "That coupon is only valid for first-time subscribers."):
        super().__init__(message)


class PlanMismatchError(DiscountError):
    def __init__(self, message: str = "That coupon doesn't apply to the selected plan."):
        super().__init__(message)


class CycleMismatchError(DiscountError):
    def __init__(self, message: str = "That coupon doesn't apply to this billing cycle."):
        super().__init__(message)


# ---------------------------------------------------------------------------
# Quote result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscountQuote:
    campaign: Optional[DiscountCampaign]
    plan: str
    billing_cycle: str
    amount_before: Decimal
    amount_discounted: Decimal
    amount_after: Decimal

    @property
    def has_discount(self) -> bool:
        return self.campaign is not None and self.amount_discounted > 0

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign.id if self.campaign else None,
            "campaign_name": self.campaign.name if self.campaign else None,
            "code": self.campaign.code if self.campaign else None,
            "plan": self.plan,
            "billing_cycle": self.billing_cycle,
            "amount_before": float(self.amount_before),
            "amount_discounted": float(self.amount_discounted),
            "amount_after": float(self.amount_after),
        }


def _to_decimal(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Eligibility validation
# ---------------------------------------------------------------------------

def _check_window_and_status(campaign: DiscountCampaign) -> None:
    if not campaign.is_active:
        raise InactiveCampaignError()
    if not campaign.has_started:
        raise NotYetStartedError()
    if campaign.is_expired:
        raise ExpiredCampaignError()


def _check_plan_and_cycle(campaign: DiscountCampaign, plan: str, billing_cycle: str) -> None:
    if campaign.plan_slug and normalize_plan_name(campaign.plan_slug) != normalize_plan_name(plan):
        raise PlanMismatchError()
    if campaign.applies_to != "all" and campaign.applies_to != billing_cycle:
        # 'one_time' campaigns are cycle-agnostic at the pricing level (they
        # constrain to first invoice only, enforced via first_time_only +
        # per_tenant_limit=1, not via billing_cycle matching).
        if campaign.applies_to != "one_time":
            raise CycleMismatchError()


def _check_usage_limits(campaign: DiscountCampaign, tenant_id: int) -> None:
    if campaign.usage_limit is not None and (campaign.usage_count or 0) >= campaign.usage_limit:
        raise UsageLimitExceededError()

    if campaign.per_tenant_limit is not None:
        used = discount_redemption_repository.count_for_tenant(campaign.id, tenant_id)
        if used >= campaign.per_tenant_limit:
            raise PerTenantLimitExceededError()

    if campaign.first_time_only:
        if discount_redemption_repository.tenant_has_any_redemption(tenant_id):
            raise NotFirstTimeError()


def validate_campaign(
    campaign: DiscountCampaign,
    *,
    tenant_id: int,
    plan: str,
    billing_cycle: str,
) -> None:
    """Raises a DiscountError subclass on the first failed check.
    Order matters for message clarity: status/window first, then
    plan/cycle fit, then usage limits (cheapest DB-free checks first)."""
    _check_window_and_status(campaign)
    _check_plan_and_cycle(campaign, plan, billing_cycle)
    _check_usage_limits(campaign, tenant_id)


def get_campaign_by_code(code: str) -> DiscountCampaign:
    campaign = discount_campaign_repository.get_by_code(code)
    if campaign is None:
        raise InvalidCouponError()
    return campaign


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------

def calculate_discount_amount(base_amount: Decimal, campaign: DiscountCampaign) -> Decimal:
    """Never returns a negative total — fixed-amount discounts are clamped
    to the base amount so a coupon can never make a plan free-and-owe-money."""
    base = _to_decimal(base_amount)
    if campaign.discount_type == "percent":
        pct = _to_decimal(campaign.value)
        discount = _quantize(base * pct / Decimal("100"))
    else:  # fixed
        discount = _to_decimal(campaign.value)

    return min(discount, base)


def quote_discount(
    *,
    tenant_id: int,
    plan: str,
    billing_cycle: str,
    code: Optional[str] = None,
) -> DiscountQuote:
    """
    Read-only. Resolves the price a tenant would pay for `plan`/`billing_cycle`
    with a coupon (if `code` given) or the best eligible auto-applied global
    campaign (if not). Never writes to the database.

    Raises a DiscountError subclass if an explicit `code` was supplied and
    is not valid/eligible — callers should catch this and re-quote with
    code=None to fall back to the undiscounted (or auto-applied) price
    rather than blocking checkout entirely.
    """
    norm_plan = normalize_plan_name(plan)
    base_amount = _to_decimal(get_plan_price(norm_plan, billing_cycle))

    campaign: Optional[DiscountCampaign] = None

    if code:
        campaign = get_campaign_by_code(code)
        validate_campaign(campaign, tenant_id=tenant_id, plan=norm_plan, billing_cycle=billing_cycle)
    else:
        # No code entered — try the best eligible auto-applied campaign.
        candidates = discount_campaign_repository.list_auto_apply_candidates(plan_slug=norm_plan)
        best_discount = Decimal("0")
        for candidate in candidates:
            try:
                validate_campaign(candidate, tenant_id=tenant_id, plan=norm_plan, billing_cycle=billing_cycle)
            except DiscountError:
                continue
            amt = calculate_discount_amount(base_amount, candidate)
            if amt > best_discount:
                best_discount = amt
                campaign = candidate

    if campaign is None:
        return DiscountQuote(
            campaign=None, plan=norm_plan, billing_cycle=billing_cycle,
            amount_before=base_amount, amount_discounted=Decimal("0.00"), amount_after=base_amount,
        )

    discount_amount = calculate_discount_amount(base_amount, campaign)
    amount_after = _quantize(base_amount - discount_amount)

    return DiscountQuote(
        campaign=campaign, plan=norm_plan, billing_cycle=billing_cycle,
        amount_before=base_amount, amount_discounted=discount_amount, amount_after=amount_after,
    )


def get_promo_banner_campaign() -> Optional[DiscountCampaign]:
    """Best global (couponless) campaign to feature on the plans page as a
    marketing banner. Plan-agnostic by design — the banner advertises a
    sale, it doesn't quote a specific line item, so it intentionally does
    not reuse quote_discount()'s per-plan eligibility logic.

    Prefers the soonest-expiring campaign with a hard deadline (so the
    countdown shown to the tenant is meaningful and never misleading);
    falls back to the highest-value open-ended campaign if none have an
    expiry. Returns None if there's nothing eligible to show.
    """
    candidates = discount_campaign_repository.list_auto_apply_candidates()
    if not candidates:
        return None
    with_deadline = [c for c in candidates if c.expires_at is not None]
    if with_deadline:
        return min(with_deadline, key=lambda c: c.expires_at)
    return max(candidates, key=lambda c: c.value)


# ---------------------------------------------------------------------------
# Redemption (the only write path)
# ---------------------------------------------------------------------------

def redeem_discount(
    *,
    tenant_id: int,
    quote: DiscountQuote,
    subscription_id: Optional[int] = None,
    commit: bool = True,
) -> Optional[DiscountRedemption]:
    """
    Records a redemption and increments the campaign's usage_count.
    No-op (returns None) if the quote had no discount applied — call this
    unconditionally after activation and let it decide.

    Re-validates the campaign right before writing (usage/date state can
    have changed since quote_discount() was called, e.g. two tenants racing
    the last redemption of a limited coupon) and raises if it's no longer
    eligible; caller should treat that as "proceed at full price", not as
    a failed payment.
    """
    if quote.campaign is None or quote.amount_discounted <= 0:
        return None

    campaign = discount_campaign_repository.get(quote.campaign.id)
    if campaign is None:
        return None

    # Idempotency guard: apply_on_activation can legitimately be invoked
    # more than once for the same subscription (e.g. a PayMongo webhook
    # succeeds, then a superadmin later triggers a manual resync of the
    # same subscription). Without this check each call would mint a new
    # DiscountRedemption row and double-increment campaign.usage_count,
    # silently bypassing usage_limit/per_tenant_limit. Return the existing
    # redemption instead of writing a duplicate.
    if subscription_id is not None:
        existing = (
            db.session.query(DiscountRedemption)
            .filter_by(subscription_id=subscription_id)
            .first()
        )
        if existing is not None:
            logger.info(
                "redeem_discount: subscription_id=%s already redeemed (redemption_id=%s) — skipping duplicate",
                subscription_id, existing.id,
            )
            return existing

    validate_campaign(campaign, tenant_id=tenant_id, plan=quote.plan, billing_cycle=quote.billing_cycle)

    redemption = DiscountRedemption(
        campaign_id=campaign.id,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        amount_before=quote.amount_before,
        amount_discounted=quote.amount_discounted,
        amount_after=quote.amount_after,
        billing_cycle=quote.billing_cycle,
    )
    db.session.add(redemption)
    campaign.usage_count = (campaign.usage_count or 0) + 1
    campaign.updated_at = datetime.now(timezone.utc)

    if commit:
        db.session.commit()
    else:
        db.session.flush()

    logger.info(
        "discount redeemed: campaign_id=%s tenant_id=%s amount_discounted=%s",
        campaign.id, tenant_id, quote.amount_discounted,
    )
    return redemption


# ---------------------------------------------------------------------------
# Superadmin analytics helpers
# ---------------------------------------------------------------------------

def campaign_analytics(campaign: DiscountCampaign) -> dict:
    redemptions = discount_redemption_repository.list_for_campaign(campaign.id)
    return {
        "campaign_id": campaign.id,
        "total_redemptions": len(redemptions),
        "revenue_impact": discount_redemption_repository.total_revenue_impact(campaign.id),
        "usage_remaining": campaign.usage_remaining,
        "is_expired": campaign.is_expired,
    }
