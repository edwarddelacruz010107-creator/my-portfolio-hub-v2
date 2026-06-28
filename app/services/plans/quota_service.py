"""
app/services/plans/quota_service.py
──────────────────────────────────────────────────────────────────────────────
Quota Service — centralized quota enforcement with Administrator bypass.

Administrator tenants bypass ALL quota checks unconditionally.
All other tenants go through the PlanCapability matrix.

Replaces / wraps:
    • app/services/permissions/quota_resolver.py   (QuotaResolver)
    • app/services/plan_capabilities.py            (enforce_upload etc.)

Both old modules are preserved for backwards compatibility.  New code should
import from here.

Public API:
    QuotaService.can_upload(tenant, file_bytes)         → (bool, str)
    QuotaService.enforce_upload(tenant, file_bytes)     → None or raises
    QuotaService.can_add_page(tenant, current_count)    → (bool, str)
    QuotaService.can_add_project(tenant, count)         → (bool, str)
    QuotaService.can_add_team_member(tenant, count)     → (bool, str)
    QuotaService.can_use_provider(tenant, provider)     → (bool, str)
    QuotaService.daily_email_limit(tenant)              → int | None
    QuotaService.quota_summary(tenant)                  → dict
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from app.services.plans.plan_hierarchy import is_administrator, get_effective_plan

logger = logging.getLogger(__name__)

_BYPASS: Tuple[bool, str] = (True, "administrator_bypass")


def _get_plan_caps(obj):
    """Load PlanCapability for the object, falling back to Trial on error."""
    try:
        from app.services.plan_capabilities import get_tenant_capabilities
        return get_tenant_capabilities(obj)
    except Exception as exc:
        logger.error("[QuotaService] Failed to load capabilities: %s", exc)
        from app.services.plan_capabilities import get_capabilities
        return get_capabilities("Trial")


class QuotaService:
    """
    Centralized quota enforcement authority.
    All quota checks MUST go through this class.
    """

    # ── Upload ──────────────────────────────────────────────────────────────

    @staticmethod
    def can_upload(tenant, file_bytes: int) -> Tuple[bool, str]:
        """
        Check whether a file upload is permitted.
        Administrator: unlimited, always allowed.
        Others: validated against PlanCapability storage + per-file limits.
        """
        plan = get_effective_plan(tenant)
        if is_administrator(plan):
            return _BYPASS
        caps = _get_plan_caps(tenant)
        used = getattr(tenant, "storage_used_bytes", 0) or 0
        return caps.can_upload(file_bytes=file_bytes, current_used_bytes=used)

    @staticmethod
    def enforce_upload(tenant, file_bytes: int) -> None:
        """
        Enforce upload quota.  Raises CapabilityError with user-facing message
        if denied.  Call this BEFORE writing any bytes to storage.
        """
        ok, reason = QuotaService.can_upload(tenant, file_bytes)
        if not ok:
            from app.services.plan_capabilities import CapabilityError
            plan = get_effective_plan(tenant)
            logger.warning(
                "[QuotaService] upload DENIED tenant=%s plan=%r bytes=%d reason=%s",
                getattr(tenant, "id", "?"), plan, file_bytes, reason,
            )
            raise CapabilityError(reason)

    # ── Pages ────────────────────────────────────────────────────────────────

    @staticmethod
    def can_add_page(tenant, current_count: int) -> Tuple[bool, str]:
        """Check whether a new portfolio page may be added."""
        if is_administrator(get_effective_plan(tenant)):
            return _BYPASS
        caps = _get_plan_caps(tenant)
        if caps.max_pages is None:
            return True, "ok"
        if current_count >= caps.max_pages:
            return (
                False,
                f"You have reached the {caps.max_pages}-page limit for your "
                f"{caps.plan_name} plan. Upgrade to add more pages.",
            )
        return True, "ok"

    # ── Projects ─────────────────────────────────────────────────────────────

    @staticmethod
    def can_add_project(tenant, current_count: int) -> Tuple[bool, str]:
        """Check whether a new project may be added."""
        if is_administrator(get_effective_plan(tenant)):
            return _BYPASS
        caps = _get_plan_caps(tenant)
        if caps.max_projects is None:
            return True, "ok"
        if current_count >= caps.max_projects:
            return (
                False,
                f"You have reached the {caps.max_projects}-project limit for your "
                f"{caps.plan_name} plan. Upgrade to add more projects.",
            )
        return True, "ok"

    # ── Team Members ─────────────────────────────────────────────────────────

    @staticmethod
    def can_add_team_member(tenant, current_count: int) -> Tuple[bool, str]:
        """Check whether a new team member may be added."""
        if is_administrator(get_effective_plan(tenant)):
            return _BYPASS
        caps = _get_plan_caps(tenant)
        if caps.max_team_members is None:
            return True, "ok"
        if current_count >= caps.max_team_members:
            return (
                False,
                f"Your {caps.plan_name} plan supports up to {caps.max_team_members} "
                "team members. Upgrade for more.",
            )
        return True, "ok"

    # ── Email Providers ──────────────────────────────────────────────────────

    @staticmethod
    def can_use_provider(tenant, provider: str) -> Tuple[bool, str]:
        """
        Check whether the tenant may use a given email provider.
        provider: 'smtp' | 'resend' | 'mailersend'
        """
        if is_administrator(get_effective_plan(tenant)):
            return _BYPASS
        caps = _get_plan_caps(tenant)
        if provider == "smtp" and not caps.can_use_custom_smtp:
            return False, f"Custom SMTP is not available on the {caps.plan_name} plan."
        if provider == "resend" and not caps.can_use_resend:
            return False, "Resend integration requires a Pro or Enterprise plan."
        if provider == "mailersend" and not caps.can_use_mailersend:
            return False, "MailerSend integration requires a Pro or Enterprise plan."
        return True, "ok"

    # ── Email Limits ─────────────────────────────────────────────────────────

    @staticmethod
    def daily_email_limit(tenant) -> Optional[int]:
        """
        Return the daily email send limit.  None = unlimited.
        Administrator always returns None (unlimited).
        """
        if is_administrator(get_effective_plan(tenant)):
            return None
        caps = _get_plan_caps(tenant)
        return caps.daily_email_limit

    # ── Summary ──────────────────────────────────────────────────────────────

    @staticmethod
    def quota_summary(tenant) -> dict:
        """
        Return a complete quota summary dict for template rendering / API.

        {
            "plan":                "Administrator",
            "is_administrator":    true,
            "quota_bypass":        true,
            "storage_used_bytes":  0,
            "storage_limit_bytes": null,
            "storage_used_mb":     0.0,
            "storage_limit_mb":    null,
            "unlimited":           true,
            "max_pages":           null,
            "max_projects":        null,
            "max_team_members":    null,
            "daily_email_limit":   null,
        }
        """
        plan   = get_effective_plan(tenant)
        _admin = is_administrator(plan)
        used   = getattr(tenant, "storage_used_bytes", 0) or 0

        if _admin:
            return {
                "plan":                plan,
                "is_administrator":    True,
                "quota_bypass":        True,
                "storage_used_bytes":  used,
                "storage_limit_bytes": None,
                "storage_used_mb":     round(used / (1024 * 1024), 2),
                "storage_limit_mb":    None,
                "unlimited":           True,
                "max_pages":           None,
                "max_projects":        None,
                "max_team_members":    None,
                "daily_email_limit":   None,
            }

        caps = _get_plan_caps(tenant)
        limit = caps.storage_limit_bytes

        return {
            "plan":                plan,
            "is_administrator":    False,
            "quota_bypass":        False,
            "storage_used_bytes":  used,
            "storage_limit_bytes": limit,
            "storage_used_mb":     round(used / (1024 * 1024), 2),
            "storage_limit_mb":    round(limit / (1024 * 1024), 2) if limit else None,
            "unlimited":           limit is None,
            "max_pages":           caps.max_pages,
            "max_projects":        caps.max_projects,
            "max_team_members":    caps.max_team_members,
            "daily_email_limit":   caps.daily_email_limit,
        }
