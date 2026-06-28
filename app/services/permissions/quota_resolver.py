"""
app/services/permissions/quota_resolver.py
──────────────────────────────────────────
Centralized quota resolution for my-portfolio-hub v6.7.

Administrator plan tenants bypass ALL quota checks.
All other tenants go through the standard PlanCapability matrix.

Usage:
    from app.services.permissions.quota_resolver import QuotaResolver

    ok, reason = QuotaResolver.can_upload(tenant, file_bytes=5_000_000)
    ok, reason = QuotaResolver.can_add_page(tenant, current_count=8)
    ok, reason = QuotaResolver.can_use_provider(tenant, 'mailersend')
    QuotaResolver.enforce_upload(tenant, file_bytes)   # raises CapabilityError
"""

from __future__ import annotations

import logging
from typing import Tuple

from app.services.permissions.permission_registry import is_administrator_plan

logger = logging.getLogger(__name__)

_UNLIMITED_OK: Tuple[bool, str] = (True, 'administrator_bypass')


class QuotaResolver:
    """Static namespace for all quota / capability checks."""

    # ── Upload ─────────────────────────────────────────────────────────────

    @staticmethod
    def can_upload(tenant, file_bytes: int) -> Tuple[bool, str]:
        plan = _get_plan(tenant)
        if is_administrator_plan(plan):
            return _UNLIMITED_OK
        from app.services.plan_capabilities import get_tenant_capabilities
        caps = get_tenant_capabilities(tenant)
        used = getattr(tenant, 'storage_used_bytes', 0) or 0
        return caps.can_upload(file_bytes=file_bytes, current_used_bytes=used)

    @staticmethod
    def enforce_upload(tenant, file_bytes: int) -> None:
        ok, reason = QuotaResolver.can_upload(tenant, file_bytes)
        if not ok:
            from app.services.plan_capabilities import CapabilityError
            logger.warning(
                '[QuotaResolver] upload DENIED tenant_id=%s plan=%s file_bytes=%d reason=%s',
                getattr(tenant, 'id', '?'), _get_plan(tenant), file_bytes, reason,
            )
            raise CapabilityError(reason)

    # ── Pages ──────────────────────────────────────────────────────────────

    @staticmethod
    def can_add_page(tenant, current_count: int) -> Tuple[bool, str]:
        if is_administrator_plan(_get_plan(tenant)):
            return _UNLIMITED_OK
        from app.services.plan_capabilities import get_tenant_capabilities
        caps = get_tenant_capabilities(tenant)
        if caps.max_pages is None:
            return True, 'ok'
        if current_count >= caps.max_pages:
            return (
                False,
                f'You have reached the {caps.max_pages}-page limit for your '
                f'{caps.plan_name} plan. Upgrade to add more pages.',
            )
        return True, 'ok'

    # ── Projects ───────────────────────────────────────────────────────────

    @staticmethod
    def can_add_project(tenant, current_count: int) -> Tuple[bool, str]:
        if is_administrator_plan(_get_plan(tenant)):
            return _UNLIMITED_OK
        from app.services.plan_capabilities import get_tenant_capabilities
        caps = get_tenant_capabilities(tenant)
        if caps.max_projects is None:
            return True, 'ok'
        if current_count >= caps.max_projects:
            return (
                False,
                f'You have reached the {caps.max_projects}-project limit for your '
                f'{caps.plan_name} plan. Upgrade to add more projects.',
            )
        return True, 'ok'

    # ── Email Providers ────────────────────────────────────────────────────

    @staticmethod
    def can_use_provider(tenant, provider: str) -> Tuple[bool, str]:
        if is_administrator_plan(_get_plan(tenant)):
            return _UNLIMITED_OK
        from app.services.plan_capabilities import get_tenant_capabilities
        caps = get_tenant_capabilities(tenant)
        if provider == 'smtp' and not caps.can_use_custom_smtp:
            return False, f'Custom SMTP is not available on the {caps.plan_name} plan.'
        if provider == 'resend' and not caps.can_use_resend:
            return False, 'Resend integration requires a Pro or Enterprise plan.'
        if provider == 'mailersend' and not caps.can_use_mailersend:
            return False, 'MailerSend integration requires a Pro or Enterprise plan.'
        return True, 'ok'

    # ── Generic feature gate ───────────────────────────────────────────────

    @staticmethod
    def has_feature(tenant, capability_attr: str) -> bool:
        """
        Check a boolean attribute on PlanCapability by name.
        Administrator always returns True.
        """
        if is_administrator_plan(_get_plan(tenant)):
            return True
        from app.services.plan_capabilities import get_tenant_capabilities
        caps = get_tenant_capabilities(tenant)
        return bool(getattr(caps, capability_attr, False))

    # ── Team members ───────────────────────────────────────────────────────

    @staticmethod
    def can_add_team_member(tenant, current_count: int) -> Tuple[bool, str]:
        if is_administrator_plan(_get_plan(tenant)):
            return _UNLIMITED_OK
        from app.services.plan_capabilities import get_tenant_capabilities
        caps = get_tenant_capabilities(tenant)
        if caps.max_team_members is None:
            return True, 'ok'
        if current_count >= caps.max_team_members:
            return (
                False,
                f'Your {caps.plan_name} plan supports up to {caps.max_team_members} '
                'team members. Upgrade for more.',
            )
        return True, 'ok'

    # ── Daily email limit ──────────────────────────────────────────────────

    @staticmethod
    def daily_email_limit(tenant) -> int | None:
        """None = unlimited."""
        if is_administrator_plan(_get_plan(tenant)):
            return None
        from app.services.plan_capabilities import get_tenant_capabilities
        return get_tenant_capabilities(tenant).daily_email_limit


# ─── Internal helper ──────────────────────────────────────────────────────────

def _get_plan(tenant) -> str:
    if callable(getattr(tenant, 'effective_plan', None)):
        return tenant.effective_plan()
    return getattr(tenant, 'plan', 'Basic') or 'Basic'
