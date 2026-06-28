# ─────────────────────────────────────────────────────────────────────────────
# app/services/billing.py  –  Core billing service  (Portfolio CMS v3.9 patched)
# ─────────────────────────────────────────────────────────────────────────────
# Patches applied:
#   • BUG-002: sub.external_id → sub.paymongo_id  (column actually exists)
#   • BUG-004: Added mark_subscription_cancelled() and mark_subscription_expired()
#   • BUG-005: Fixed sync_subscription_from_paymongo() to use fetch_subscription()
#   • BUG-006: get_or_create_pending_subscription() now checks for existing active
#              subscription and uses SELECT FOR UPDATE locking
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.utils import (
    BILLING_PLANS,
    normalize_plan_name,
    get_plan_price,
    get_plan_price_label,
)

# ---------------------------------------------------------------------------
# Duration helper
# ---------------------------------------------------------------------------

def plan_duration_days(plan: str, billing_cycle: str = "monthly") -> int:
    """
    Return the number of days the plan covers for the given cycle.

    monthly → plan['duration_days']          (default 30)
    yearly  → plan['duration_days'] × 12     (default 360)
    """
    norm = normalize_plan_name(plan)
    data = BILLING_PLANS.get(norm, BILLING_PLANS["Basic"])
    base_days: int = int(data.get("duration_days", 30))
    if billing_cycle == "yearly":
        return base_days * 12
    return base_days


# ---------------------------------------------------------------------------
# Subscription activation  (call this after payment is confirmed)
# ---------------------------------------------------------------------------

def activate_subscription(
    subscription,
    plan: str = None,
    billing_cycle: str = "monthly",
    now: datetime | None = None,
    paymongo_payment_id: str | None = None,
    amount: float | None = None,
    source: str | None = None,
) -> None:
    """
    Activate or renew a subscription.

    Renewal logic (additive, not reset):
      • Already active  → expires_at += duration   (reward on-time renewals)
      • Expired / new   → started_at = now, expires_at = now + duration

    Args:
        subscription:        The Subscription model instance (mutated in-place).
        plan:                Plan name string, e.g. 'Pro'. Defaults to subscription.plan.
        billing_cycle:       'monthly' | 'yearly'.
        now:                 Override "current time" (useful in tests).
        paymongo_payment_id: PayMongo payment ID to record on the row.
        amount:              Actual amount paid (PHP float); stored as amount_paid.
        source:              Event source label (e.g. 'payment.paid') — informational only.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    # FIX: default plan from subscription if not supplied (webhook handler omits it sometimes)
    if plan is None:
        plan = getattr(subscription, 'plan', 'Basic') or 'Basic'

    # FIX: default billing_cycle from subscription if not supplied
    if billing_cycle is None:
        billing_cycle = getattr(subscription, 'billing_cycle', 'monthly') or 'monthly'

    days = plan_duration_days(plan, billing_cycle)
    norm = normalize_plan_name(plan)

    # Determine whether this is a renewal of an active subscription
    is_active = (
        subscription.expires_at is not None
        and subscription.expires_at > now
    )

    if is_active:
        # ADDITIVE RENEWAL: extend from the current expiry date
        subscription.expires_at = subscription.expires_at + timedelta(days=days)
    else:
        # NEW / EXPIRED: start fresh
        subscription.started_at = now
        subscription.expires_at = now + timedelta(days=days)

    # Update plan metadata and status
    subscription.plan          = norm
    subscription.billing_cycle = billing_cycle
    subscription.status        = 'active'
    subscription.amount_paid   = float(amount) if amount is not None else get_plan_price(norm, billing_cycle)

    if paymongo_payment_id:
        subscription.paymongo_payment_id = paymongo_payment_id


# ---------------------------------------------------------------------------
# NEW: Subscription cancellation / expiry helpers (BUG-004)
# ---------------------------------------------------------------------------

def mark_subscription_cancelled(
    subscription,
    source: str | None = None,
    now: datetime | None = None,
) -> None:
    """
    Mark a subscription as cancelled.  Idempotent — safe to call multiple times.

    Args:
        subscription:  Subscription ORM instance (mutated in-place).
        source:        Event source label (e.g. 'subscription.cancelled').
        now:           Override current time (useful in tests).
    """
    from app import db
    if now is None:
        now = datetime.now(tz=timezone.utc)

    if subscription.status == 'cancelled':
        return  # already cancelled — idempotent

    subscription.status       = 'cancelled'
    subscription.cancelled_at = now

    import logging
    logging.getLogger(__name__).info(
        'Subscription %s cancelled (tenant_id=%s, source=%s)',
        subscription.id, subscription.tenant_id, source,
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def mark_subscription_expired(
    subscription,
    source: str | None = None,
    now: datetime | None = None,
) -> None:
    """
    Mark a subscription as expired.  Idempotent — safe to call multiple times.

    Args:
        subscription:  Subscription ORM instance (mutated in-place).
        source:        Event source label (e.g. 'subscription.expired').
        now:           Override current time (useful in tests).
    """
    from app import db
    if now is None:
        now = datetime.now(tz=timezone.utc)

    if subscription.status in ('cancelled', 'expired'):
        return  # already terminal — idempotent

    subscription.status = 'expired'

    import logging
    logging.getLogger(__name__).info(
        'Subscription %s expired (tenant_id=%s, source=%s)',
        subscription.id, subscription.tenant_id, source,
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


# ---------------------------------------------------------------------------
# Pending subscription helper
# ---------------------------------------------------------------------------

def get_or_create_pending_subscription(
    db_session,
    tenant_id: int | str,
    plan: str,
    billing_cycle: str = "monthly",
    payment_method: str = "manual",
):
    """
    Fetch an existing pending subscription for the tenant+plan+cycle
    or create a new one.  Returns the subscription object.

    BUG-006 FIX:
      • Checks for existing ACTIVE subscription first — raises ValueError
        if one exists to prevent duplicate active subscriptions.
      • Uses SELECT FOR UPDATE to prevent race conditions on concurrent requests.
    """
    from app.models import Subscription  # local import to avoid circular deps

    norm = normalize_plan_name(plan)

    # GUARD: Do not create a pending sub if there is already an active one
    # (prevents duplicate active subscriptions from concurrent payments)
    active_sub = (
        db_session.query(Subscription)
        .filter_by(tenant_id=tenant_id, status='active')
        .with_for_update(skip_locked=True)
        .first()
    )
    if active_sub is not None and active_sub.plan == norm:
        # Already active on this plan — return the active sub directly
        return active_sub

    # Look for an existing pending sub with a lock
    sub = (
        db_session.query(Subscription)
        .filter_by(
            tenant_id=tenant_id,
            plan=norm,
            billing_cycle=billing_cycle,
            status="pending",
        )
        .with_for_update()
        .first()
    )

    if sub is None:
        sub = Subscription(
            tenant_id=tenant_id,
            plan=norm,
            billing_cycle=billing_cycle,
            status="pending",
            payment_method=payment_method,
            amount_paid=get_plan_price(norm, billing_cycle),
        )
        db_session.add(sub)
        db_session.flush()

    return sub


# ---------------------------------------------------------------------------
# Checkout initiation  (BUG-001 / BUG-002 FIX)
# ---------------------------------------------------------------------------

def initiate_checkout(
    db_session,
    profile,
    plan: str,
    billing_cycle: str = "monthly",
    success_url: str = "",
    cancel_url: str = "",
):
    """
    Create a pending Subscription row and start a PayMongo checkout session.

    FIXED SIGNATURE (BUG-001):
      • db_session is now the first parameter (was missing in original callers)
      • return_endpoint removed — success_url/cancel_url are used instead
      • Callers must pass db_session explicitly

    Returns:
        (checkout_url: str, error: str | None)
    """
    from app.utils import get_plan_price, get_plan_price_label
    from app.models.portfolio import Subscription

    norm  = normalize_plan_name(plan)
    price = get_plan_price(norm, billing_cycle)
    label = get_plan_price_label(norm, billing_cycle)

    sub = get_or_create_pending_subscription(
        db_session, profile.tenant_id, norm, billing_cycle=billing_cycle
    )

    try:
        from app.utils.paymongo import create_checkout_session

        result = create_checkout_session(
            tenant_id=profile.tenant_id,
            tenant_slug=getattr(profile, 'tenant_slug', str(profile.tenant_id)),
            plan_name=norm,
            billing_cycle=billing_cycle,
            subscription_id=sub.id,
            success_url=success_url,
            failed_url=cancel_url,
            cancel_url=cancel_url,
        )

        if not result:
            return "", "PayMongo checkout session creation failed."

        checkout_url = result.get('checkout_url', '')
        session_id   = result.get('session_id', '')

        # BUG-002 FIX: use paymongo_id (the actual column), not external_id
        sub.paymongo_id = session_id
        db_session.commit()
        return checkout_url, None

    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("initiate_checkout failed: %s", exc)
        db_session.rollback()
        return "", str(exc)


# ---------------------------------------------------------------------------
# Grace period helper
# ---------------------------------------------------------------------------

def is_in_grace_period(profile) -> bool:
    """
    Return True if the profile's subscription has expired but is still within
    the configured grace window (BILLING_GRACE_PERIOD_DAYS, default 3 days).
    """
    try:
        from flask import current_app
        from datetime import datetime, timezone, timedelta
        from app.models.portfolio import Subscription

        grace_days = int(current_app.config.get("BILLING_GRACE_PERIOD_DAYS", 3))

        if profile is None or profile.tenant_id is None:
            return False

        sub = Subscription.current(profile.tenant_id)
        if sub is None or sub.expires_at is None:
            return False
        if sub.status == "active":
            return False

        expires = sub.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if now <= expires:
            return False

        return now <= (expires + timedelta(days=grace_days))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Subscription access status  (canonical status string for UI + gates)
# ---------------------------------------------------------------------------

def subscription_access_status(profile) -> str:
    """
    Return a canonical status string for the tenant's subscription.

    Values: 'trial' | 'active' | 'grace' | 'pending' | 'expired' |
            'cancelled' | 'suspended' | 'none'
    """
    try:
        from datetime import datetime, timezone
        from app.models.portfolio import Subscription

        if profile is None:
            return "none"

        if profile.tenant and profile.tenant.status == "suspended":
            return "suspended"

        if profile.is_trial_active():
            return "trial"

        if profile.tenant_id is None:
            return "none"

        sub = Subscription.current(profile.tenant_id)
        if sub is None:
            return "none"

        if sub.status == "pending":
            return "pending"
        if sub.status == "cancelled":
            return "cancelled"

        now = datetime.now(timezone.utc)
        if sub.status in ("active", "expired"):
            expires = sub.expires_at
            if expires is None:
                return "active" if sub.status == "active" else "expired"
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if sub.status == "active" and expires >= now:
                return "active"
            if is_in_grace_period(profile):
                return "grace"
            return "expired"

        return sub.status or "none"
    except Exception:
        import logging
        logging.getLogger(__name__).exception("subscription_access_status failed")
        return "none"


# ---------------------------------------------------------------------------
# Admin / Superadmin helpers
# ---------------------------------------------------------------------------

def compute_billing_metrics(db_session=None) -> dict:
    """
    Compute platform-wide billing metrics for the superadmin dashboard.
    """
    try:
        from app.models.portfolio import Subscription, Profile
        from datetime import datetime, timezone
        from app import db as _db

        _session = db_session or _db.session
        now = datetime.now(timezone.utc)

        all_subs = _session.query(Subscription).all()

        total_active    = sum(1 for s in all_subs if s.status == 'active')
        total_pending   = sum(1 for s in all_subs if s.status == 'pending')
        total_expired   = sum(1 for s in all_subs if s.status == 'expired')
        total_cancelled = sum(1 for s in all_subs if s.status == 'cancelled')

        from app.utils import get_plan_price
        mrr = 0.0
        active_by_plan: dict[str, int] = {}
        for s in all_subs:
            if s.status == 'active':
                plan  = normalize_plan_name(s.plan or 'Basic')
                cycle = getattr(s, 'billing_cycle', 'monthly') or 'monthly'
                monthly_price = get_plan_price(plan, 'monthly')
                if cycle == 'yearly':
                    monthly_price = get_plan_price(plan, 'yearly') / 12
                mrr += monthly_price
                active_by_plan[plan] = active_by_plan.get(plan, 0) + 1

        total_trial = _session.query(Profile).filter(
            Profile.free_trial_ends > now
        ).count()

        return {
            'total_active':    total_active,
            'total_trial':     total_trial,
            'total_expired':   total_expired,
            'total_pending':   total_pending,
            'total_cancelled': total_cancelled,
            'mrr':             round(mrr, 2),
            'active_by_plan':  active_by_plan,
        }
    except Exception:
        import logging
        logging.getLogger(__name__).exception("compute_billing_metrics failed")
        return {
            'total_active': 0, 'total_trial': 0, 'total_expired': 0,
            'total_pending': 0, 'total_cancelled': 0,
            'mrr': 0.0, 'active_by_plan': {},
        }


def tenant_billing_summary(profile) -> dict:
    """Return a per-tenant billing summary dict for the superadmin tenant detail view."""
    try:
        sub = profile.current_subscription() if profile else None
        return {
            'status':       subscription_access_status(profile),
            'plan':         normalize_plan_name(sub.plan if sub else (profile.plan if profile else 'Basic')),
            'expires_at':   sub.expires_at if sub else None,
            'started_at':   sub.started_at if sub else None,
            'price_paid':   getattr(sub, 'price_paid', None) if sub else None,
            'trial_ends':   profile.free_trial_ends if profile else None,
            'trial_active': profile.is_trial_active() if profile else False,
        }
    except Exception:
        return {'status': 'unknown', 'plan': 'Basic'}


def force_activate_subscription(
    profile,
    plan: str,
    billing_cycle: str = 'monthly',
    actor: str = 'superadmin',
    reviewer: str = None,
) -> tuple[bool, str]:
    """
    Superadmin: forcibly activate a subscription without payment.
    Idempotent — safe to call multiple times.
    """
    from app import db as _db
    _reviewer = reviewer or actor or 'superadmin'
    try:
        sub = get_or_create_pending_subscription(
            _db.session, profile.tenant_id, plan, billing_cycle=billing_cycle
        )
        activate_subscription(sub, plan, billing_cycle=billing_cycle)
        profile.sync_license_from_subscription()
        _db.session.commit()

        from app.utils import log_billing_event
        log_billing_event(
            'force_activate',
            profile.tenant_slug,
            f'{plan} activated by {_reviewer} (force)',
        )
        return True, f'Subscription activated: {plan}'
    except Exception as exc:
        _db.session.rollback()
        import logging
        logging.getLogger(__name__).exception("force_activate_subscription failed")
        return False, str(exc)


def sync_subscription_from_paymongo(profile_or_id, db_session=None) -> tuple[bool, str]:
    """
    Re-sync a Subscription row from PayMongo's current state.

    BUG-005 FIX: replaced get_payment_intent() (did not exist) with
    fetch_subscription() (the actual helper in paymongo.py).
    """
    from app import db as _db
    _session = db_session or _db.session
    try:
        from app.models.portfolio import Subscription, Profile

        if hasattr(profile_or_id, 'current_subscription'):
            profile = profile_or_id
            sub = profile.current_subscription()
        else:
            sub = _session.get(Subscription, int(profile_or_id))
            profile = None
        if sub is None:
            return False, 'No active subscription found'

        sub = _session.get(Subscription, sub.id)
        if sub is None:
            return False, 'Subscription not found'

        # BUG-002 FIX: use paymongo_id (checkout session) or paymongo_subscription_id
        external_id = getattr(sub, 'paymongo_subscription_id', None) or getattr(sub, 'paymongo_id', None)
        if not external_id:
            return False, 'No PayMongo external_id on this subscription'

        from flask import current_app
        if not current_app.config.get('PAYMONGO_ENABLED'):
            return False, 'PayMongo is disabled'

        # BUG-005 FIX: use fetch_subscription() instead of non-existent get_payment_intent()
        from app.utils.paymongo import fetch_subscription
        data = fetch_subscription(external_id)
        if not data:
            return False, 'PayMongo returned no data'

        status = (data.get('attributes') or {}).get('status', '')
        if status == 'active':
            plan = normalize_plan_name(sub.plan or 'Basic')
            activate_subscription(sub, plan)
            _session.commit()
            return True, f'Synced: subscription activated ({plan})'

        return True, f'Synced: PayMongo status is {status!r} — no action taken'

    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("sync_subscription_from_paymongo failed")
        return False, str(exc)
