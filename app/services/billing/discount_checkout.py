"""
app/services/billing/discount_checkout.py
=========================================
Thin glue layer between the checkout flow and discount_service.

Design goals
------------
* Zero changes to the DiscountCampaign / DiscountRedemption models.
* Zero changes to discount_service's public API (quote/redeem/validate).
* Zero changes to PayMongo webhook signature verification or the
  Subscription activation math in billing.py::activate_subscription.
* The coupon lives in flask.session between plan-selection POST and the
  redemption call — it never touches the Subscription row, so an aborted
  checkout leaves no half-applied state.

Public API (call sites shown in INTEGRATION.md)
-----------------------------------------------
    stash_coupon(tenant_id, code)          # after form validation
    pop_coupon(tenant_id)                  # before activation
    peek_coupon(tenant_id)                 # for context/preview only
    quote_for_context(tenant_id, plan, cycle, code=None) -> DiscountQuote
    apply_on_activation(tenant_id, subscription, plan, cycle) -> Optional[DiscountRedemption]

`apply_on_activation` is idempotent per (tenant_id, subscription_id): it
pops the stashed coupon, re-validates, records the redemption and returns
the row. If anything is off (expired, over limit, race lost) it swallows
the DiscountError, logs it, and returns None so the subscription still
activates at full price rather than blocking payment.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from flask import session

from app.services.billing import discount_service
from app.services.billing.discount_service import (
    DiscountError,
    DiscountQuote,
)

logger = logging.getLogger(__name__)

# Namespaced session key — one pending coupon per tenant, per browser session.
_SESSION_KEY = "pending_coupon_by_tenant"


# ---------------------------------------------------------------------------
# Session stash
# ---------------------------------------------------------------------------

def _bucket() -> dict:
    bucket = session.get(_SESSION_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
        session[_SESSION_KEY] = bucket
    return bucket


def stash_coupon(tenant_id: int, code: str) -> None:
    """Remember a coupon code between plan-selection and payment redirect.
    Normalizes to upper-case; empty/None clears the stash."""
    bucket = _bucket()
    key = str(tenant_id)
    if not code:
        bucket.pop(key, None)
    else:
        bucket[key] = code.strip().upper()
    session.modified = True


def peek_coupon(tenant_id: int) -> Optional[str]:
    return _bucket().get(str(tenant_id))


def pop_coupon(tenant_id: int) -> Optional[str]:
    code = _bucket().pop(str(tenant_id), None)
    session.modified = True
    return code


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def quote_for_context(
    tenant_id: int,
    plan: str,
    billing_cycle: str,
    code: Optional[str] = None,
) -> DiscountQuote:
    """
    Safe wrapper for template rendering. Never raises — an invalid coupon
    downgrades to the auto-apply / full-price quote so the plans page can
    still render.
    """
    try:
        return discount_service.quote_discount(
            tenant_id=tenant_id, plan=plan, billing_cycle=billing_cycle, code=code,
        )
    except DiscountError as exc:
        logger.info("coupon %r not usable for tenant %s: %s", code, tenant_id, exc)
        # Fall back to auto-apply (or no discount) so the page still renders.
        return discount_service.quote_discount(
            tenant_id=tenant_id, plan=plan, billing_cycle=billing_cycle, code=None,
        )


# ---------------------------------------------------------------------------
# Activation-time redemption
# ---------------------------------------------------------------------------

def apply_on_activation(
    *,
    tenant_id: int,
    subscription,
    plan: str,
    billing_cycle: str,
    commit: bool = True,
    code: Optional[str] = None,
) -> Optional[object]:
    """
    Call this right AFTER activate_subscription() (or your manual-approval
    equivalent) and BEFORE committing the outer transaction — this is the
    single write path for a redemption.

    `code` should be passed explicitly by any caller running outside the
    tenant's own browser session — PayMongo webhooks, superadmin manual
    approval, superadmin resync. Those requests do not carry the tenant's
    Flask session, so `pop_coupon()` would silently return None there even
    when a coupon was legitimately selected at checkout. The durable
    reference is `subscription.coupon_code` (set at plan-selection time).
    If `code` is omitted, this falls back to the session stash — that path
    only reliably works for same-session flows (e.g. an immediate-activation
    variant called from within the tenant's own request).

    Never raises: coupon problems downgrade the subscription to full price
    instead of failing the payment.
    """
    if code is None:
        code = pop_coupon(tenant_id)
    else:
        # Explicit code supplied: still clear any stale session stash so a
        # later same-session call doesn't redeem it a second time.
        pop_coupon(tenant_id)
    if not code:
        return None

    try:
        quote = discount_service.quote_discount(
            tenant_id=tenant_id, plan=plan, billing_cycle=billing_cycle, code=code,
        )
    except DiscountError as exc:
        logger.warning(
            "coupon %r for tenant %s failed at activation: %s", code, tenant_id, exc,
        )
        return None

    try:
        redemption = discount_service.redeem_discount(
            tenant_id=tenant_id,
            quote=quote,
            subscription_id=getattr(subscription, "id", None),
            commit=commit,
        )
    except DiscountError as exc:
        logger.warning(
            "redeem lost race for coupon %r tenant %s: %s", code, tenant_id, exc,
        )
        return None

    # If a real discount landed, keep subscription.amount_paid in sync with
    # what the tenant actually paid. Do not decrease it below the amount
    # already recorded by the payment provider — trust the payment record.
    if redemption is not None:
        try:
            paid = Decimal(str(subscription.amount_paid or 0))
            if paid == 0 or paid > quote.amount_after:
                subscription.amount_paid = float(quote.amount_after)
        except Exception:  # pragma: no cover — defensive only
            logger.exception("failed to sync amount_paid after redemption")

    # Clear the durable coupon reference on the subscription row now that
    # this activation attempt is resolved (success or failure). Subscription
    # rows are reused across renewals (get_or_create_pending_subscription),
    # so leaving a stale coupon_code here would risk it being read again —
    # and re-redeemed — the next time this same row is activated.
    if hasattr(subscription, "coupon_code"):
        try:
            subscription.coupon_code = None
        except Exception:  # pragma: no cover — defensive only
            logger.exception("failed to clear coupon_code after redemption attempt")

    return redemption
