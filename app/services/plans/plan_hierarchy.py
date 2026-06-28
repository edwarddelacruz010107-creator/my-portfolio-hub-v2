"""
app/services/plans/plan_hierarchy.py
──────────────────────────────────────────────────────────────────────────────
CANONICAL PLAN HIERARCHY for Portfolio Hub v6.7+

Single source of truth for every plan ordering decision in the platform.
All plan comparison logic MUST route through this module — never compare
plan strings with == or 'in' lists directly.

HIERARCHY (ascending rank):
    trial (0) < basic (10) < pro (20) < business (30) < enterprise (40)
    < administrator (999)

ADMINISTRATOR is a reserved internal system plan:
    • Rank 999 — always the highest tier
    • Not purchasable, not visible in billing UI
    • Only assignable by the system / superadmin
    • Automatically inherits ALL current and future paid features
    • Suppresses all upsell prompts and upgrade CTAs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet


# ─── Plan Rank Constants ───────────────────────────────────────────────────────

PLAN_RANK: dict[str, int] = {
    "trial":         0,
    "free":          0,    # alias → trial
    "basic":         10,
    "pro":           20,
    "professional":  20,   # alias → pro
    "premium":       20,   # alias → pro
    "business":      30,
    "enterprise":    40,
    "agency":        40,   # alias → enterprise
    "ent":           40,   # alias → enterprise
    # ── RESERVED ──────────────────────────────────────────────────────────────
    "administrator": 999,
    "admin":         999,  # alias → administrator
    "root":          999,  # alias → administrator
    "system":        999,  # alias → administrator
}

# Canonical string constants — always use these, never raw string literals
PLAN_TRIAL         = "Trial"
PLAN_BASIC         = "Basic"
PLAN_PRO           = "Pro"
PLAN_BUSINESS      = "Business"
PLAN_ENTERPRISE    = "Enterprise"
PLAN_ADMINISTRATOR = "Administrator"

# Alias → canonical mapping (lower-case input)
_CANONICAL: dict[str, str] = {
    "trial":         PLAN_TRIAL,
    "free":          PLAN_TRIAL,
    "basic":         PLAN_BASIC,
    "pro":           PLAN_PRO,
    "professional":  PLAN_PRO,
    "premium":       PLAN_PRO,
    "business":      PLAN_BUSINESS,
    "enterprise":    PLAN_ENTERPRISE,
    "agency":        PLAN_ENTERPRISE,
    "ent":           PLAN_ENTERPRISE,
    "administrator": PLAN_ADMINISTRATOR,
    "admin":         PLAN_ADMINISTRATOR,
    "root":          PLAN_ADMINISTRATOR,
    "system":        PLAN_ADMINISTRATOR,
}

# Plans that may appear in the public billing / upgrade UI
PUBLIC_PLAN_KEYS: list[str] = [PLAN_BASIC, PLAN_PRO, PLAN_BUSINESS, PLAN_ENTERPRISE]

# Plans that superadmin may assign to tenants (excludes Administrator — use tenant_access)
ASSIGNABLE_PLAN_KEYS: list[str] = PUBLIC_PLAN_KEYS + [PLAN_TRIAL]


# ─── Core Helpers ─────────────────────────────────────────────────────────────

def normalize_plan(raw: str | None) -> str:
    """
    Normalize any plan string to its canonical form (e.g. 'pro' → 'Pro').
    Unknown values default to 'Trial' (most restrictive safe fallback).
    """
    if not raw:
        return PLAN_TRIAL
    key = (raw or "").strip().lower()
    return _CANONICAL.get(key, PLAN_TRIAL)


def get_plan_rank(raw: str | None) -> int:
    """
    Return the numeric rank for any plan string.
    Administrator always returns 999.  Unknown plans return 0.
    """
    key = (raw or "").strip().lower()
    return PLAN_RANK.get(key, 0)


def has_plan_access(current_plan: str | None, required_plan: str | None) -> bool:
    """
    Core access resolver.  True iff current_plan's rank >= required_plan's rank.

    Administrator (rank 999) always passes regardless of required_plan.
    None / unknown required_plan → unrestricted (True).

    Usage:
        has_plan_access("Administrator", "enterprise")  → True
        has_plan_access("Pro", "enterprise")            → False
        has_plan_access("Enterprise", "pro")            → True
    """
    if not required_plan:
        return True
    if is_administrator(current_plan):
        return True
    return get_plan_rank(current_plan) >= get_plan_rank(required_plan)


def is_administrator(raw: str | None) -> bool:
    """
    True iff the plan string resolves to the Administrator reserved plan.
    Case-insensitive.  Handles all known aliases ('admin', 'root', 'system').
    """
    if not raw:
        return False
    return (raw or "").strip().lower() in {"administrator", "admin", "root", "system"}


def is_paid_plan(raw: str | None) -> bool:
    """True iff the plan is above Trial / free tier (and not the system plan)."""
    return get_plan_rank(raw) > 0 and not is_administrator(raw)


def is_at_least(current_plan: str | None, minimum_plan: str) -> bool:
    """
    Semantic alias for has_plan_access — reads more naturally in guard code.

    if not is_at_least(tenant.plan, 'Pro'):
        abort(403)
    """
    return has_plan_access(current_plan, minimum_plan)


def get_effective_plan(obj) -> str:
    """
    Safely extract the effective plan from any tenant-like object.

    Supports:
        • Tenant ORM (core.py)  — has effective_plan() method
        • Profile ORM (tenant_data.py) — has effective_plan() method
        • Dict with 'plan' key
        • Plain string

    Falls back to Trial on any error.
    """
    if obj is None:
        return PLAN_TRIAL
    if isinstance(obj, str):
        return normalize_plan(obj)
    if isinstance(obj, dict):
        return normalize_plan(obj.get("plan"))
    if callable(getattr(obj, "effective_plan", None)):
        try:
            return normalize_plan(obj.effective_plan())
        except Exception:
            pass
    raw = getattr(obj, "plan", None)
    return normalize_plan(raw)
