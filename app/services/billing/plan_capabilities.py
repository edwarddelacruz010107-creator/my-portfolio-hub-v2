"""
app/services/plan_capabilities.py — Enterprise Capability-Based Plan System (v6.0)

Replaces the flat PLAN_FEATURES dict in models/core.py with a structured
PlanCapability dataclass that enforces type contracts and provides runtime
helper methods used by upload enforcement, subscription middleware, and UI.

CAPABILITY MATRIX:
    ┌──────────────────────┬────────┬──────────┬───────────┬────────────┐
    │ Feature              │ Trial  │ Basic    │ Pro       │ Enterprise │
    ├──────────────────────┼────────┼──────────┼───────────┼────────────┤
    │ Portfolio Pages      │ 3      │ 10       │ Unlimited │ Unlimited  │
    │ Projects             │ 5      │ 25       │ Unlimited │ Unlimited  │
    │ Upload Storage       │ 10 MB  │ 100 MB   │ 1 GB      │ 5 GB       │
    │ Max Upload / File    │ 2 MB   │ 5 MB     │ 25 MB     │ 100 MB     │
    │ Custom Domain        │ ✗      │ ✓        │ ✓         │ ✓          │
    │ Custom SMTP          │ ✗      │ ✓        │ ✓         │ ✓          │
    │ Resend / MailerSend  │ ✗      │ ✗        │ ✓         │ ✓          │
    │ Team Members         │ 1      │ 2        │ 10        │ Unlimited  │
    │ AI Features          │ ✗      │ Limited  │ ✓         │ ✓          │
    │ Analytics            │ Basic  │ Standard │ Advanced  │ Enterprise │
    │ Branding Removal     │ ✗      │ ✗        │ ✓         │ ✓          │
    │ Daily Emails         │ 50     │ 500      │ 5,000     │ 50,000     │
    └──────────────────────┴────────┴──────────┴───────────┴────────────┘

Usage:
    from app.services.plan_capabilities import get_capabilities, check_upload

    caps = get_capabilities('Pro')
    ok, reason = caps.can_upload(file_bytes=3_000_000, current_used=500_000_000)

    # Upload guard (raises CapabilityError on violation):
    from app.services.plan_capabilities import enforce_upload
    enforce_upload(tenant, file_bytes)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

from app.system_plan import has_administrator_access

# ─── Sentinel for "unlimited" numeric caps ────────────────────────────────────
UNLIMITED = None

AnalyticsLevel = Literal['basic', 'standard', 'advanced', 'enterprise']
AiLevel = Literal['none', 'limited', 'full']


class CapabilityError(Exception):
    """Raised when a capability check fails (upload, page limit, etc.)."""


@dataclass(frozen=True)
class PlanCapability:
    """
    Immutable capability snapshot for a given plan tier.
    All numeric fields use None to represent "unlimited".
    """

    plan_name: str

    # ── Content limits ────────────────────────────────────────────────────────
    max_pages:    int | None        # portfolio pages
    max_projects: int | None        # project entries

    # ── Storage ───────────────────────────────────────────────────────────────
    storage_limit_bytes:    int | None  # total tenant storage quota
    max_upload_size_bytes:  int | None  # per-file maximum

    # ── Email features ────────────────────────────────────────────────────────
    can_use_custom_smtp:    bool
    can_use_resend:         bool
    can_use_mailersend:     bool
    daily_email_limit:      int | None

    # ── Platform features ─────────────────────────────────────────────────────
    can_use_custom_domain:  bool
    can_remove_branding:    bool
    can_use_ai_features:    AiLevel
    max_team_members:       int | None
    analytics_level:        AnalyticsLevel

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def storage_limit_mb(self) -> float | None:
        if self.storage_limit_bytes is None:
            return None
        return self.storage_limit_bytes / (1024 * 1024)

    @property
    def max_upload_size_mb(self) -> float | None:
        if self.max_upload_size_bytes is None:
            return None
        return self.max_upload_size_bytes / (1024 * 1024)

    def can_upload(
        self,
        file_bytes: int,
        current_used_bytes: int,
    ) -> tuple[bool, str]:
        """
        Return (ok, reason) for a proposed upload.
        Validates per-file size AND total quota headroom.
        """
        # 1. Per-file size check
        if self.max_upload_size_bytes is not None and file_bytes > self.max_upload_size_bytes:
            limit_mb = self.max_upload_size_bytes / (1024 * 1024)
            actual_mb = file_bytes / (1024 * 1024)
            return (
                False,
                f'File size {actual_mb:.1f} MB exceeds the {limit_mb:.0f} MB limit '
                f'for your {self.plan_name} plan.',
            )

        # 2. Total quota check
        if self.storage_limit_bytes is not None:
            remaining = self.storage_limit_bytes - current_used_bytes
            if file_bytes > remaining:
                used_mb  = current_used_bytes / (1024 * 1024)
                limit_mb = self.storage_limit_bytes / (1024 * 1024)
                return (
                    False,
                    f'Storage quota exceeded. Used {used_mb:.1f} MB of {limit_mb:.0f} MB. '
                    'Upgrade your plan or delete existing files to free space.',
                )

        return True, 'ok'

    def storage_usage_pct(self, used_bytes: int) -> float:
        """Return 0–100 percentage of quota used. Returns 0.0 for unlimited plans."""
        if self.storage_limit_bytes is None or self.storage_limit_bytes == 0:
            return 0.0
        return min(100.0, (used_bytes / self.storage_limit_bytes) * 100)

    def storage_warning(self, used_bytes: int) -> bool:
        """True when usage >= 90% of quota (soft warning threshold)."""
        return self.storage_usage_pct(used_bytes) >= 90.0

    def as_dict(self) -> dict:
        """Serialisable representation for JSON API and Jinja templates."""
        return {
            'plan_name':             self.plan_name,
            'max_pages':             self.max_pages,
            'max_projects':          self.max_projects,
            'storage_limit_bytes':   self.storage_limit_bytes,
            'storage_limit_mb':      self.storage_limit_mb,
            'max_upload_size_bytes': self.max_upload_size_bytes,
            'max_upload_size_mb':    self.max_upload_size_mb,
            'can_use_custom_smtp':   self.can_use_custom_smtp,
            'can_use_resend':        self.can_use_resend,
            'can_use_mailersend':    self.can_use_mailersend,
            'daily_email_limit':     self.daily_email_limit,
            'can_use_custom_domain': self.can_use_custom_domain,
            'can_remove_branding':   self.can_remove_branding,
            'can_use_ai_features':   self.can_use_ai_features,
            'max_team_members':      self.max_team_members,
            'analytics_level':       self.analytics_level,
        }


# ─── Plan Definitions ─────────────────────────────────────────────────────────

_MB  = 1024 * 1024
_GB  = 1024 * _MB

_CAPABILITIES: dict[str, PlanCapability] = {
    'Trial': PlanCapability(
        plan_name               = 'Trial',
        max_pages               = 3,
        max_projects            = 5,
        storage_limit_bytes     = 10 * _MB,
        max_upload_size_bytes   = 2 * _MB,
        can_use_custom_smtp     = False,
        can_use_resend          = False,
        can_use_mailersend      = False,
        daily_email_limit       = 50,
        can_use_custom_domain   = False,
        can_remove_branding     = False,
        can_use_ai_features     = 'none',
        max_team_members        = 1,
        analytics_level         = 'basic',
    ),
    'Basic': PlanCapability(
        plan_name               = 'Basic',
        max_pages               = 10,
        max_projects            = 25,
        storage_limit_bytes     = 100 * _MB,
        max_upload_size_bytes   = 5 * _MB,
        can_use_custom_smtp     = True,
        can_use_resend          = False,
        can_use_mailersend      = False,
        daily_email_limit       = 500,
        can_use_custom_domain   = True,
        can_remove_branding     = False,
        can_use_ai_features     = 'limited',
        max_team_members        = 2,
        analytics_level         = 'standard',
    ),
    'Pro': PlanCapability(
        plan_name               = 'Pro',
        max_pages               = UNLIMITED,
        max_projects            = UNLIMITED,
        storage_limit_bytes     = 1 * _GB,
        max_upload_size_bytes   = 25 * _MB,
        can_use_custom_smtp     = True,
        can_use_resend          = True,
        can_use_mailersend      = True,
        daily_email_limit       = 5_000,
        can_use_custom_domain   = True,
        can_remove_branding     = True,
        can_use_ai_features     = 'full',
        max_team_members        = 10,
        analytics_level         = 'advanced',
    ),
    'Enterprise': PlanCapability(
        plan_name               = 'Enterprise',
        max_pages               = UNLIMITED,
        max_projects            = UNLIMITED,
        storage_limit_bytes     = 5 * _GB,
        max_upload_size_bytes   = 100 * _MB,
        can_use_custom_smtp     = True,
        can_use_resend          = True,
        can_use_mailersend      = True,
        daily_email_limit       = 50_000,
        can_use_custom_domain   = True,
        can_remove_branding     = True,
        can_use_ai_features     = 'full',
        max_team_members        = UNLIMITED,
        analytics_level         = 'enterprise',
    ),
    'Administrator': PlanCapability(
        plan_name               = 'Administrator',
        max_pages               = UNLIMITED,
        max_projects            = UNLIMITED,
        storage_limit_bytes     = UNLIMITED,
        max_upload_size_bytes   = UNLIMITED,
        can_use_custom_smtp     = True,
        can_use_resend          = True,
        can_use_mailersend      = True,
        daily_email_limit       = UNLIMITED,
        can_use_custom_domain   = True,
        can_remove_branding     = True,
        can_use_ai_features     = 'full',
        max_team_members        = UNLIMITED,
        analytics_level         = 'enterprise',
    ),
}

# Map legacy / alias names → canonical keys
_ALIASES: dict[str, str] = {
    'trial':        'Trial',
    'free_trial':   'Trial',
    'basic':        'Basic',
    'starter':      'Basic',
    'pro':          'Pro',
    'professional': 'Pro',
    'business':     'Enterprise',
    'enterprise':   'Enterprise',
    'administrator':'Administrator',
    'admin':        'Administrator',
    'system':       'Administrator',
}


def get_capabilities(plan: str) -> PlanCapability:
    """
    Return PlanCapability for the given plan string.
    Unknown plans silently fall back to Trial (most restrictive).
    """
    normalised = (plan or '').strip()
    key = _ALIASES.get(normalised.lower(), normalised.title())
    cap = _CAPABILITIES.get(key)
    if cap is None:
        logger.warning('get_capabilities: unknown plan %r — defaulting to Trial', plan)
        cap = _CAPABILITIES['Trial']
    return cap


def get_tenant_capabilities(tenant) -> PlanCapability:
    """
    Convenience wrapper: accepts a Tenant ORM instance.
    Uses effective_plan() (subscription-aware).
    """
    if has_administrator_access(tenant):
        return get_capabilities('Administrator')
    plan = tenant.effective_plan() if callable(getattr(tenant, 'effective_plan', None)) else (tenant.plan or 'Trial')
    return get_capabilities(plan)


# ─── Upload enforcement helpers ───────────────────────────────────────────────

def enforce_upload(tenant, file_bytes: int) -> None:
    """
    Guard an upload attempt against the tenant's plan capabilities.
    Raises CapabilityError with a user-facing message on violation.

    Call this BEFORE writing any bytes to disk/storage.

    Args:
        tenant:     Tenant ORM instance (must have .storage_used_bytes attribute)
        file_bytes: Size of the incoming file in bytes
    """
    caps = get_tenant_capabilities(tenant)
    used = getattr(tenant, 'storage_used_bytes', 0) or 0
    ok, reason = caps.can_upload(file_bytes=file_bytes, current_used_bytes=used)
    if not ok:
        logger.warning(
            '[UPLOAD][capability] DENIED tenant_id=%s plan=%s file_bytes=%d used=%d reason=%s',
            getattr(tenant, 'id', '?'), caps.plan_name, file_bytes, used, reason,
        )
        raise CapabilityError(reason)


def check_page_limit(tenant, current_count: int) -> tuple[bool, str]:
    """Return (ok, reason) for adding a new portfolio page."""
    caps = get_tenant_capabilities(tenant)
    if caps.max_pages is None:
        return True, 'ok'
    if current_count >= caps.max_pages:
        return (
            False,
            f'You have reached the {caps.max_pages}-page limit for your {caps.plan_name} plan. '
            'Upgrade to add more pages.',
        )
    return True, 'ok'


def check_project_limit(tenant, current_count: int) -> tuple[bool, str]:
    """Return (ok, reason) for adding a new project."""
    caps = get_tenant_capabilities(tenant)
    if caps.max_projects is None:
        return True, 'ok'
    if current_count >= caps.max_projects:
        return (
            False,
            f'You have reached the {caps.max_projects}-project limit for your {caps.plan_name} plan. '
            'Upgrade to add more projects.',
        )
    return True, 'ok'


def check_email_provider_access(tenant, provider: str) -> tuple[bool, str]:
    """
    Guard access to premium email providers (resend, mailersend).
    SMTP is allowed for Basic+; resend/mailersend require Pro+.
    """
    caps = get_tenant_capabilities(tenant)
    if provider == 'smtp' and not caps.can_use_custom_smtp:
        return False, f'Custom SMTP is not available on the {caps.plan_name} plan.'
    if provider == 'resend' and not caps.can_use_resend:
        return False, f'Resend integration requires a Pro or Enterprise plan.'
    if provider == 'mailersend' and not caps.can_use_mailersend:
        return False, f'MailerSend integration requires a Pro or Enterprise plan.'
    return True, 'ok'


def all_plans_summary(include_system: bool = False) -> list[dict]:
    """Return capability dicts. System plans are hidden unless requested."""
    items = _CAPABILITIES.values() if include_system else (
        _CAPABILITIES[name] for name in ('Trial', 'Basic', 'Pro', 'Enterprise')
    )
    return [cap.as_dict() for cap in items]
