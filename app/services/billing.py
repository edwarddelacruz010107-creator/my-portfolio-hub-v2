# ─────────────────────────────────────────────────────────────────────────────
# app/services/billing.py  — Portfolio CMS v4.1 PATCHED
# ─────────────────────────────────────────────────────────────────────────────
# Patches applied in this file:
#   CRIT-02: create_checkout_session() called with wrong kwargs + unpacked as
#            tuple. Now called with correct keyword args and return dict unpacked
#            correctly.
#   CRIT-03: sub.external_id → sub.paymongo_id everywhere (attribute did not
#            exist on the Subscription model).
#   CRIT-04: Added mark_subscription_cancelled() and mark_subscription_expired()
#            which paymongo.py imports but the live billing.py never defined.
#   CRIT-06: get_or_create_pending_subscription() passed `profile` ORM object
#            where `tenant_id: int` was expected. Fixed to pass profile.tenant_id.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.utils import (
    BILLING_PLANS,
    normalize_plan_name,
    get_plan_price,
    get_plan_price_label,
)
import logging

logger = logging.getLogger(__name__)

try:
    from app.services.renewal_scheduler import on_subscription_renewed, on_subscription_activated
    _HAS_RENEWAL_HOOKS = True
except ImportError:
    _HAS_RENEWAL_HOOKS = False


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
    plan: str | None = None,
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
        plan:                Plan name string; defaults to subscription.plan if None.
        billing_cycle:       'monthly' | 'yearly'.
        now:                 Override "current time" (useful in tests).
        paymongo_payment_id: PayMongo payment ID to record on the row.
        amount:              Actual amount paid (PHP float); stored as amount_paid.
        source:              Event source label (e.g. 'payment.paid') — informational only.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    # Fallback: use existing plan if caller doesn't supply one
    if plan is None:
        plan = subscription.plan or "Basic"

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

    # Record PayMongo payment ID for idempotency + audit
    if paymongo_payment_id:
        subscription.paymongo_payment_id = paymongo_payment_id


# ---------------------------------------------------------------------------
# Subscription cancellation  (CRIT-04: these were missing from the live file)
# ---------------------------------------------------------------------------

def mark_subscription_cancelled(
    subscription,
    source: str | None = None,
) -> None:
    """
    Mark a subscription as cancelled.
    Called by paymongo.py webhook handlers on subscription.cancelled events.
    """
    import logging
    logger = logging.getLogger(__name__)

    now = datetime.now(tz=timezone.utc)
    subscription.status       = 'cancelled'
    subscription.cancelled_at = now
    subscription.last_webhook_at = now

    if source:
        logger.info(
            'mark_subscription_cancelled: tenant_id=%s plan=%s source=%s',
            subscription.tenant_id, subscription.plan, source,
        )

    # Persist profile expiry enforcement
    try:
        from app import db
        from app.models.portfolio import Profile
        profile = Profile.query.filter_by(tenant_id=subscription.tenant_id).first()
        if profile and hasattr(profile, 'enforce_expiry'):
            profile.enforce_expiry(commit=False)
        db.session.commit()
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).exception('mark_subscription_cancelled: commit failed: %s', exc)
        from app import db as _db
        _db.session.rollback()


def mark_subscription_expired(
    subscription,
    source: str | None = None,
) -> None:
    """
    Mark a subscription as expired.
    Called by paymongo.py webhook handlers on subscription.expired events.
    """
    import logging
    logger = logging.getLogger(__name__)

    now = datetime.now(tz=timezone.utc)
    subscription.status          = 'expired'
    subscription.last_webhook_at = now

    if source:
        logger.info(
            'mark_subscription_expired: tenant_id=%s plan=%s source=%s',
            subscription.tenant_id, subscription.plan, source,
        )

    try:
        from app import db
        from app.models.portfolio import Profile
        profile = Profile.query.filter_by(tenant_id=subscription.tenant_id).first()
        if profile and hasattr(profile, 'enforce_expiry'):
            profile.enforce_expiry(commit=False)
        db.session.commit()
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).exception('mark_subscription_expired: commit failed: %s', exc)
        from app import db as _db
        _db.session.rollback()


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
    Return the one-and-only pending (or active) subscription for this tenant,
    creating it only when none exists.

    CRIT-06 NOTE: This function expects `tenant_id` (int/str), NOT a Profile
    ORM object. Callers must pass profile.tenant_id explicitly.

    Priority:
      1. Existing 'active' subscription → update plan/cycle in-place (renewal)
      2. Existing 'pending' subscription (any plan/cycle) → update to new values
      3. None found → create fresh pending row
    """
    from app.models import Subscription  # local import to avoid circular deps

    # Normalise: accept int or str
    try:
        tenant_id = int(tenant_id)
    except (TypeError, ValueError):
        raise ValueError(f"get_or_create_pending_subscription: tenant_id must be int, got {type(tenant_id)}")

    norm = normalize_plan_name(plan)

    # 1. Active subscription — update plan/cycle for next renewal, return it
    active_sub = (
        db_session.query(Subscription)
        .filter_by(tenant_id=tenant_id, status="active")
        .order_by(Subscription.created_at.desc())
        .first()
    )
    if active_sub:
        active_sub.plan          = norm
        active_sub.billing_cycle = billing_cycle
        db_session.flush()
        return active_sub

    # 2. Any existing pending subscription — update and reuse (no new row)
    pending_sub = (
        db_session.query(Subscription)
        .filter_by(tenant_id=tenant_id, status="pending")
        .order_by(Subscription.created_at.desc())
        .first()
    )
    if pending_sub:
        pending_sub.plan           = norm
        pending_sub.billing_cycle  = billing_cycle
        pending_sub.payment_method = payment_method
        pending_sub.amount_paid    = get_plan_price(norm, billing_cycle)
        db_session.flush()
        return pending_sub

    # 3. No existing row — create fresh
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
# Checkout initiation  (CRIT-02 + CRIT-03 + CRIT-06 fix)
# ---------------------------------------------------------------------------

def initiate_checkout(
    db_session,
    profile,
    plan: str,
    billing_cycle: str = "monthly",
    success_url: str = "",
    cancel_url: str = "",
) -> tuple[str, str | None]:
    """
    Create a pending Subscription row and start a PayMongo checkout session.

    Returns:
        (checkout_url: str, error: str | None)

    FIXES:
      CRIT-06: Pass profile.tenant_id (int) to get_or_create_pending_subscription,
               not the profile ORM object.
      CRIT-02: Call create_checkout_session() with its actual keyword signature
               and unpack the returned dict (not as a tuple).
      CRIT-03: Store session ID in sub.paymongo_id (not sub.external_id).
    """
    import logging
    logger = logging.getLogger(__name__)

    from flask import current_app

    norm  = normalize_plan_name(plan)
    price = get_plan_price(norm, billing_cycle)

    # CRIT-06 FIX: pass tenant_id (int), not the profile object
    sub = get_or_create_pending_subscription(
        db_session, profile.tenant_id, norm, billing_cycle=billing_cycle
    )

    try:
        from app.utils.paymongo import create_checkout_session

        base_url = current_app.config.get('APP_BASE_URL', '').rstrip('/') or 'http://localhost:5000'
        tenant_slug = profile.tenant_slug or 'default'

        if not success_url:
            success_url = f'{base_url}/{tenant_slug}/billing/plans?status=success'
        if not cancel_url:
            cancel_url = f'{base_url}/{tenant_slug}/billing/plans?status=cancelled'
        failed_url = f'{base_url}/{tenant_slug}/billing/plans?status=failed'

        # CRIT-02 FIX: call with the actual keyword signature of create_checkout_session()
        result = create_checkout_session(
            tenant_id=profile.tenant_id,
            tenant_slug=tenant_slug,
            plan_name=norm,
            billing_cycle=billing_cycle,
            subscription_id=sub.id,
            success_url=success_url,
            failed_url=failed_url,
            cancel_url=cancel_url,
        )

        if not result:
            return "", "PayMongo checkout session creation failed. Check logs."

        # CRIT-02 FIX: result is a dict, not a tuple
        checkout_url = result.get('checkout_url', '')
        session_id   = result.get('session_id')

        # CRIT-03 FIX: write to sub.paymongo_id (the actual column), not sub.external_id
        if session_id:
            sub.paymongo_id = session_id

        db_session.commit()
        return checkout_url, None

    except Exception as exc:
        logger.exception("initiate_checkout failed: %s", exc)
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
        from app.models.portfolio import Subscription

        if profile is None:
            return "none"

        if profile.tenant and profile.tenant.status == "suspended":
            return "suspended"

        if profile.tenant_id is None:
            return "none"

        sub = Subscription.current(profile.tenant_id)

        if sub is None:
            return "none"

        if sub.status == "active":
            return "active"

        if sub.status == "pending":
            return "pending"

        if sub.status == "cancelled":
            return "cancelled"

        if is_in_grace_period(profile):
            return "grace"

        return "expired"

    except Exception:
        return "none"


# =============================================================================
# Superadmin / platform-wide helpers
# (Merged from patches/billing.py — were missing from live file, causing
#  ImportError in app/superadmin/__init__.py at startup)
# =============================================================================

def compute_billing_metrics(db_session=None) -> dict:
    """
    Compute platform-wide billing metrics for the superadmin dashboard.
    Returns safe zero-values on any error so the dashboard never crashes.
    """
    try:
        from app.models.portfolio import Subscription, Profile
        from app import db as _db
        from app.utils import get_plan_price, normalize_plan_name

        _session = db_session or _db.session
        now = datetime.now(timezone.utc)

        all_subs = _session.query(Subscription).all()

        total_active    = sum(1 for s in all_subs if s.status == 'active')
        total_pending   = sum(1 for s in all_subs if s.status == 'pending')
        total_expired   = sum(1 for s in all_subs if s.status == 'expired')
        total_cancelled = sum(1 for s in all_subs if s.status == 'cancelled')

        mrr: float = 0.0
        active_by_plan: dict = {}
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
        logger.exception('compute_billing_metrics failed')
        return {
            'total_active': 0, 'total_trial': 0, 'total_expired': 0,
            'total_pending': 0, 'total_cancelled': 0,
            'mrr': 0.0, 'active_by_plan': {},
        }


def tenant_billing_summary(profile) -> dict:
    """Return a per-tenant billing summary dict for the superadmin tenant detail view."""
    try:
        from app.utils import normalize_plan_name
        sub = profile.current_subscription() if profile else None
        return {
            'status':       subscription_access_status(profile),
            'plan':         normalize_plan_name(
                                sub.plan if sub else (profile.plan if profile else 'Basic')
                            ),
            'expires_at':   sub.expires_at  if sub else None,
            'started_at':   sub.started_at  if sub else None,
            'price_paid':   getattr(sub, 'price_paid', None) if sub else None,
            'trial_ends':   profile.free_trial_ends   if profile else None,
            'trial_active': profile.is_trial_active() if profile else False,
        }
    except Exception:
        logger.exception('tenant_billing_summary failed')
        return {'status': 'unknown', 'plan': 'Basic'}


def force_activate_subscription(
    profile,
    plan: str,
    billing_cycle: str = 'monthly',
    actor: str = 'superadmin',
    reviewer: str = None,
) -> tuple:
    """
    Superadmin: forcibly activate a subscription without payment.
    Idempotent — safe to call multiple times.
    """
    from app import db as _db
    from app.utils import normalize_plan_name

    _reviewer = reviewer or actor or 'superadmin'
    try:
        sub = get_or_create_pending_subscription(
            _db.session, profile.tenant_id, plan, billing_cycle=billing_cycle
        )
        activate_subscription(sub, normalize_plan_name(plan), billing_cycle=billing_cycle)
        profile.sync_license_from_subscription()
        _db.session.commit()

        try:
            from app.utils import log_billing_event
            log_billing_event(
                'force_activate',
                profile.tenant_slug,
                f'{plan} activated by {_reviewer} (force)',
            )
        except Exception:
            pass  # log_billing_event is optional; don't block activation

        return True, f'Subscription activated: {plan}'
    except Exception as exc:
        _db.session.rollback()
        logger.exception('force_activate_subscription failed')
        return False, str(exc)


def sync_subscription_from_paymongo(profile_or_id, db_session=None) -> tuple:
    """
    Re-sync a Subscription row from PayMongo's current state.
    Uses fetch_subscription() — the correct helper in paymongo.py.
    """
    from app import db as _db
    from app.utils import normalize_plan_name

    _session = db_session or _db.session
    try:
        from app.models.portfolio import Subscription, Profile

        if hasattr(profile_or_id, 'current_subscription'):
            sub = profile_or_id.current_subscription()
        else:
            sub = _session.get(Subscription, int(profile_or_id))

        if sub is None:
            return False, 'No active subscription found'

        external_id = (
            getattr(sub, 'paymongo_subscription_id', None)
            or getattr(sub, 'paymongo_id', None)
        )
        if not external_id:
            return False, 'No PayMongo external_id on this subscription'

        from flask import current_app
        if not current_app.config.get('PAYMONGO_ENABLED'):
            return False, 'PayMongo is disabled'

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
        logger.exception('sync_subscription_from_paymongo failed')
        return False, str(exc)