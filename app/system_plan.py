"""
Internal Administrator plan helpers.

This module keeps the protected default portfolio plan out of public pricing and
checkout while giving the system portfolio full platform capabilities.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ADMINISTRATOR_PLAN_NAME = "Administrator"
ADMINISTRATOR_PLAN_SLUG = "administrator"
PUBLIC_PLAN_NAMES = ("Basic", "Pro", "Enterprise")
SYSTEM_TENANT_SLUGS = frozenset({"default", "administrator"})
SYSTEM_OWNER_EMAILS = frozenset({"delacruzedward735@gmail.com"})

ADMINISTRATOR_PLAN: dict[str, Any] = {
    "label": ADMINISTRATOR_PLAN_NAME,
    "slug": ADMINISTRATOR_PLAN_SLUG,
    "currency_symbol": "₱",
    "currency": "PHP",
    "price_monthly": 0.0,
    "price_yearly": 0.0,
    "duration_days": None,
    "price": 0.0,
    "price_label": "Internal only",
    "description": "Protected system portfolio with full platform access.",
    "is_active": True,
    "is_public": False,
    "is_system": True,
    "sort_order": 9999,
    "features": [
        "Full platform access",
        "All themes unlocked",
        "All premium features unlocked",
        "No trial expiration",
        "No checkout or payment required",
        "Unlimited projects and pages where supported",
    ],
}

_ADMIN_PLAN_ALIASES = frozenset({
    "admin",
    "administrator",
    "system",
    "system administrator",
    "system_admin",
    "system-admin",
})


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def is_administrator_plan(plan: Any) -> bool:
    return _norm(plan) in _ADMIN_PLAN_ALIASES


def is_system_tenant_slug(slug: Any) -> bool:
    return _norm(slug) in SYSTEM_TENANT_SLUGS


def _object_slug(obj: Any) -> str:
    for attr in ("slug", "tenant_slug"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    tenant = getattr(obj, "tenant", None)
    if tenant is not None:
        return _object_slug(tenant)
    return ""


def _object_email(obj: Any) -> str:
    for attr in ("email", "owner_email", "contact_email"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    tenant = getattr(obj, "tenant", None)
    if tenant is not None:
        return _object_email(tenant)
    return ""


def is_default_system_tenant(obj: Any) -> bool:
    """Return True only for the protected default portfolio identity.

    Slug is the primary runtime identifier.  The bootstrap owner email is used
    only as a fallback for old records that have not yet been normalized to the
    ``default``/``administrator`` slug, so a normal tenant cannot become a
    system tenant just by using the same contact email.
    """
    if obj is None:
        return False
    slug = _object_slug(obj)
    if is_system_tenant_slug(slug):
        return True
    # Email alone must never grant Administrator access. A normal tenant cannot
    # become the system portfolio just by using the owner email.
    return False


def has_administrator_access(obj: Any) -> bool:
    """Central bypass predicate for the protected system portfolio only.

    A normal tenant must not receive full access merely because an email or a
    tampered plan value says "Administrator". The protected bypass requires the
    stable default/administrator tenant slug.
    """
    if obj is None:
        return False
    return is_default_system_tenant(obj)


def public_billing_plans(plans: dict[str, dict]) -> dict[str, dict]:
    """Return only tenant-purchasable plans in display order."""
    return {name: plans[name] for name in PUBLIC_PLAN_NAMES if name in plans}


def system_billing_plans() -> dict[str, dict]:
    return {ADMINISTRATOR_PLAN_NAME: ADMINISTRATOR_PLAN.copy()}


def find_default_system_tenant():
    """Locate the default portfolio using stable identifiers, not display name."""
    from app.models.core import Tenant

    return Tenant.query.filter(Tenant.slug.in_(tuple(SYSTEM_TENANT_SLUGS))).first()


def ensure_default_tenant_administrator_plan(commit: bool = True) -> bool:
    """
    Idempotently repair the default portfolio to the internal Administrator plan.

    Only the tenant identified by slug ``default``/``administrator`` or the
    bootstrap owner email is changed. Normal tenants are never touched.
    """
    from app import db
    from app.models.core import Subscription
    from app.models.tenant_data import Profile

    tenant = find_default_system_tenant()
    if tenant is None:
        return False

    now = datetime.now(timezone.utc)
    changed = False

    def set_if(obj: Any, attr: str, value: Any) -> None:
        nonlocal changed
        if hasattr(obj, attr) and getattr(obj, attr) != value:
            setattr(obj, attr, value)
            changed = True

    set_if(tenant, "plan", ADMINISTRATOR_PLAN_NAME)
    set_if(tenant, "plan_name", ADMINISTRATOR_PLAN_SLUG)
    set_if(tenant, "status", "active")
    set_if(tenant, "subscription_state", "active")
    set_if(tenant, "trial_status", "active")
    set_if(tenant, "trial_ends_at", None)
    set_if(tenant, "grace_period_ends_at", None)
    set_if(tenant, "subscription_expires_at", None)
    if getattr(tenant, "subscription_started_at", None) is None:
        set_if(tenant, "subscription_started_at", now)
    db.session.add(tenant)

    profile = (
        Profile.query.filter_by(tenant_id=tenant.id).first()
        or Profile.query.filter_by(tenant_slug=getattr(tenant, "slug", None)).first()
    )
    if profile is not None:
        set_if(profile, "tenant_id", tenant.id)
        set_if(profile, "tenant_slug", tenant.slug)
        set_if(profile, "plan", ADMINISTRATOR_PLAN_NAME)
        set_if(profile, "monthly_rate", 0.0)
        set_if(profile, "free_trial_days", 0)
        set_if(profile, "free_trial_ends", None)
        set_if(profile, "is_available", True)
        db.session.add(profile)

    # The protected system portfolio should not be controlled by old Basic/Trial
    # subscription rows. Cancel only rows for this tenant; normal tenants remain unchanged.
    for sub in Subscription.query.filter_by(tenant_id=tenant.id).all():
        if sub.status not in {"cancelled", "expired"} or is_administrator_plan(sub.plan):
            set_if(sub, "status", "cancelled")
            set_if(sub, "cancelled_at", now)
            set_if(sub, "paymongo_checkout_url", None)
            set_if(sub, "payment_method", "system-protected")
            set_if(sub, "amount_paid", 0.0)
            db.session.add(sub)

    if changed and commit:
        db.session.commit()
    return changed
