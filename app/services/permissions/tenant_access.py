"""
app/services/permissions/tenant_access.py
─────────────────────────────────────────
Tenant protection rules for the ADMINISTRATOR reserved system plan.

Rules:
  1. Default-portfolio tenant (slug == 'default') always receives the
     Administrator plan and can never be downgraded or suspended.
  2. Only the system (superadmin context) may assign Administrator.
  3. No tenant can self-upgrade to Administrator via any API or form.
  4. Administrator cannot be purchased, cancelled, or overwritten with a
     billing operation.
"""

from __future__ import annotations

import logging

from app.services.permissions.permission_registry import (
    PLAN_ADMINISTRATOR,
    is_administrator_plan,
)

logger = logging.getLogger(__name__)

# Slug of the default platform-owner portfolio tenant
DEFAULT_TENANT_SLUG = 'default'


# ── Predicates ────────────────────────────────────────────────────────────────

def is_default_tenant(tenant) -> bool:
    """True iff this is the platform-owner / default-portfolio tenant."""
    slug = getattr(tenant, 'slug', None) or getattr(tenant, 'tenant_slug', None)
    return slug == DEFAULT_TENANT_SLUG


def is_protected_tenant(tenant) -> bool:
    """
    True iff this tenant is protected from deletion / suspension / downgrade.
    Currently only the default tenant qualifies.
    """
    return is_default_tenant(tenant)


# ── Plan assignment guards ────────────────────────────────────────────────────

def can_assign_administrator(requesting_user=None) -> tuple[bool, str]:
    """
    Only the system itself (startup hook) or a superadmin may assign
    the Administrator plan.  Regular admin users are denied.
    """
    if requesting_user is None:
        # System/startup call — always allowed
        return True, 'ok'
    is_super = getattr(requesting_user, 'is_superadmin', False)
    if is_super:
        return True, 'ok'
    return False, 'Only superadmins may assign the Administrator plan.'


def validate_plan_change(
    tenant,
    new_plan: str,
    requesting_user=None,
) -> tuple[bool, str]:
    """
    Validate a proposed plan change.

    Returns (ok, reason).  Call before any service that modifies tenant.plan
    or creates/updates a Subscription.
    """
    current_plan = getattr(tenant, 'plan', '') or ''

    # 1. Nobody may downgrade the Administrator plan
    if is_administrator_plan(current_plan) and not is_administrator_plan(new_plan):
        logger.warning(
            '[TenantAccess] Blocked attempt to downgrade administrator plan '
            'for tenant_id=%s to %r', getattr(tenant, 'id', '?'), new_plan,
        )
        return False, 'The Administrator plan cannot be downgraded.'

    # 2. Nobody but the system / superadmin may assign Administrator
    if is_administrator_plan(new_plan):
        ok, reason = can_assign_administrator(requesting_user)
        if not ok:
            logger.warning(
                '[TenantAccess] Blocked unprivileged administrator plan assignment '
                'for tenant_id=%s', getattr(tenant, 'id', '?'),
            )
            return False, reason

    return True, 'ok'


def validate_tenant_deletion(tenant) -> tuple[bool, str]:
    """Guard against deleting protected tenants."""
    if is_protected_tenant(tenant):
        return False, 'The default portfolio tenant cannot be deleted.'
    return True, 'ok'


def validate_tenant_suspension(tenant) -> tuple[bool, str]:
    """Guard against suspending the platform owner."""
    if is_protected_tenant(tenant):
        return False, 'The default portfolio tenant cannot be suspended.'
    return True, 'ok'


# ── Bootstrap: ensure default tenant has Administrator plan ───────────────────

def ensure_administrator_plan(app=None) -> None:
    """
    Called at application startup (after db is ready).
    Finds the default tenant and ensures its plan is 'Administrator'.
    Safe to call repeatedly — no-op if already correct.
    """
    try:
        _ctx = None
        if app is not None:
            _ctx = app.app_context()
            _ctx.push()

        from app import db
        from app.models.core import Tenant

        tenant = Tenant.query.filter_by(slug=DEFAULT_TENANT_SLUG).first()
        if tenant is None:
            logger.info('[TenantAccess] Default tenant not found — skipping bootstrap.')
            return

        if tenant.plan != PLAN_ADMINISTRATOR:
            logger.info(
                '[TenantAccess] Bootstrapping default tenant plan: %r → %r',
                tenant.plan, PLAN_ADMINISTRATOR,
            )
            tenant.plan = PLAN_ADMINISTRATOR
            db.session.add(tenant)
            db.session.commit()
            logger.info('[TenantAccess] Default tenant plan set to Administrator.')
        else:
            logger.debug('[TenantAccess] Default tenant already on Administrator plan.')

    except Exception as exc:
        logger.error('[TenantAccess] ensure_administrator_plan failed: %s', exc)
    finally:
        if _ctx is not None:
            _ctx.pop()
