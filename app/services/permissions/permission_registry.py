"""
app/services/permissions/permission_registry.py
────────────────────────────────────────────────
Canonical plan registry and hierarchy resolver for my-portfolio-hub v6.7.

PLAN HIERARCHY (lowest → highest):
    FREE < BASIC < PRO < BUSINESS < ENTERPRISE < ADMINISTRATOR

ADMINISTRATOR is a RESERVED SYSTEM PLAN:
    • is_system_plan  = True   — internal / not user-facing
    • is_hidden       = True   — excluded from all public APIs
    • is_purchasable  = False  — cannot be checked out or purchased
    • is_internal     = True   — only assignable by the system itself
    • protection_level = 'root' — cannot be downgraded or deleted

Only the platform owner / default-portfolio tenant ever carries this plan.
All other tenants work within FREE–ENTERPRISE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet


# ─── Plan Metadata ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlanMeta:
    key: str                    # canonical key used in DB, e.g. "Administrator"
    label: str                  # display name
    rank: int                   # hierarchy rank; higher = more powerful
    is_system_plan: bool = False
    is_hidden: bool = False
    is_purchasable: bool = True
    is_internal: bool = False
    protection_level: str = 'none'   # 'none' | 'root'


# ─── Registry ─────────────────────────────────────────────────────────────────

_PLAN_REGISTRY: dict[str, PlanMeta] = {
    'Trial': PlanMeta(
        key='Trial',
        label='Trial',
        rank=0,
        is_purchasable=False,
    ),
    'Basic': PlanMeta(
        key='Basic',
        label='Basic',
        rank=10,
    ),
    'Pro': PlanMeta(
        key='Pro',
        label='Pro',
        rank=20,
    ),
    'Business': PlanMeta(
        key='Business',
        label='Business',
        rank=30,
    ),
    'Enterprise': PlanMeta(
        key='Enterprise',
        label='Enterprise',
        rank=40,
    ),
    # ── RESERVED SYSTEM PLAN ─────────────────────────────────────────────
    'Administrator': PlanMeta(
        key='Administrator',
        label='Administrator',
        rank=999,
        is_system_plan=True,
        is_hidden=True,
        is_purchasable=False,
        is_internal=True,
        protection_level='root',
    ),
}

# Canonical string constant
PLAN_ADMINISTRATOR = 'Administrator'

# Alias map (lower-case input → canonical key)
_ALIASES: dict[str, str] = {
    'trial':         'Trial',
    'free':          'Trial',
    'basic':         'Basic',
    'pro':           'Pro',
    'professional':  'Pro',
    'business':      'Business',
    'enterprise':    'Enterprise',
    'ent':           'Enterprise',
    'administrator': 'Administrator',
    'admin':         'Administrator',
    'root':          'Administrator',
    'system':        'Administrator',
}

# Plans visible in billing/upgrade UI and public API responses
PUBLIC_PLANS: list[PlanMeta] = [
    m for m in _PLAN_REGISTRY.values()
    if not m.is_hidden and m.is_purchasable
]

# Plans that may appear in the superadmin tenant list (all non-hidden)
TENANT_VISIBLE_PLANS: list[PlanMeta] = [
    m for m in _PLAN_REGISTRY.values()
    if not m.is_hidden
]


# ─── Public helpers ───────────────────────────────────────────────────────────

def resolve_plan(raw: str) -> PlanMeta:
    """
    Return the PlanMeta for any plan string (case-insensitive).
    Unknown values fall back to Basic.
    """
    if not raw:
        return _PLAN_REGISTRY['Basic']
    key = _ALIASES.get(raw.strip().lower(), raw.strip().title())
    return _PLAN_REGISTRY.get(key, _PLAN_REGISTRY['Basic'])


def plan_rank(raw: str) -> int:
    """Numeric rank for a plan string. Higher = more powerful."""
    return resolve_plan(raw).rank


def is_administrator_plan(raw: str) -> bool:
    """True iff the plan resolves to ADMINISTRATOR."""
    return resolve_plan(raw).key == PLAN_ADMINISTRATOR


def is_at_least(raw: str, minimum: str) -> bool:
    """True iff the given plan meets or exceeds the minimum tier."""
    return plan_rank(raw) >= plan_rank(minimum)


def is_hidden_plan(raw: str) -> bool:
    return resolve_plan(raw).is_hidden


def is_purchasable_plan(raw: str) -> bool:
    return resolve_plan(raw).is_purchasable


def is_system_plan(raw: str) -> bool:
    return resolve_plan(raw).is_system_plan


def public_plan_keys() -> list[str]:
    """Keys safe to expose in billing pages and public API responses."""
    return [m.key for m in PUBLIC_PLANS]


def normalize_plan_name(plan: str) -> str:
    """Return canonical plan key string (e.g. 'Pro', 'Enterprise')."""
    return resolve_plan(plan).key
