"""Trial subscription history helpers.

Trials are authoritative on ``Tenant.subscription_state`` and the trial date
columns.  Older accounts therefore may not have a row in ``subscriptions``.
This module mirrors that lifecycle into a zero-value history row without
changing the tenant's entitlement state.
"""
from __future__ import annotations

from typing import Any

from app import db
from app.models import Subscription, Tenant
from app.utils.datetime_utils import ensure_utc_aware, utc_now


def _trial_status(tenant: Tenant) -> str:
    state = (getattr(tenant, "subscription_state", "") or "").strip().lower()
    ends_at = ensure_utc_aware(getattr(tenant, "trial_ends_at", None))
    if state == "trial" and (ends_at is None or ends_at > utc_now()):
        return "trial"
    return "expired"


def ensure_trial_subscription_record(
    tenant_or_profile: Any,
    *,
    commit: bool = False,
) -> tuple[Subscription | None, bool]:
    """Return the tenant's trial history row, creating it when needed.

    The created row uses ``status='trial'`` while the trial is live and
    ``status='expired'`` after it ends.  It is history-only: paid-subscription
    resolution intentionally ignores rows with the ``trial`` status.
    """
    tenant = tenant_or_profile
    if tenant is None:
        return None, False
    if not isinstance(tenant, Tenant):
        tenant = getattr(tenant_or_profile, "tenant", None)
    if tenant is None:
        tenant_id = getattr(tenant_or_profile, "tenant_id", None)
        tenant = db.session.get(Tenant, tenant_id) if tenant_id else None
    if tenant is None:
        return None, False

    started_at = getattr(tenant, "trial_started_at", None) or getattr(tenant, "created_at", None)
    expires_at = getattr(tenant, "trial_ends_at", None)
    if started_at is None and expires_at is None:
        return None, False

    existing = (
        Subscription.query
        .filter(Subscription.tenant_id == tenant.id)
        .filter(db.func.lower(Subscription.plan) == "trial")
        .order_by(Subscription.created_at.asc())
        .first()
    )
    desired_status = _trial_status(tenant)
    if existing is not None:
        changed = False
        if existing.started_at is None and started_at is not None:
            existing.started_at = started_at
            changed = True
        if existing.expires_at is None and expires_at is not None:
            existing.expires_at = expires_at
            changed = True
        if existing.status == "trial" and desired_status == "expired":
            existing.status = "expired"
            changed = True
        if changed:
            db.session.add(existing)
            if commit:
                db.session.commit()
            else:
                db.session.flush()
        return existing, False

    row = Subscription(
        tenant_id=tenant.id,
        plan="Trial",
        status=desired_status,
        billing_cycle="trial",
        amount_paid=0.0,
        payment_method="system_trial",
        started_at=started_at,
        expires_at=expires_at,
    )
    db.session.add(row)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return row, True


def ensure_profile_trial_history(profile: Any, *, commit: bool = True) -> Subscription | None:
    """Best-effort compatibility helper used by billing GET routes."""
    try:
        row, _ = ensure_trial_subscription_record(profile, commit=commit)
        return row
    except Exception:
        db.session.rollback()
        return None
