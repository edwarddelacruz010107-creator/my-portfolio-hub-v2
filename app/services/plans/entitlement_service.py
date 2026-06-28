"""
app/services/plans/entitlement_service.py
──────────────────────────────────────────────────────────────────────────────
Entitlement Service — the single decision point for ALL feature access.

All route handlers, middleware, and templates must call this service instead
of performing inline plan comparisons.  This guarantees:

    1. Administrator bypass is always evaluated first.
    2. Plan hierarchy comparisons are always through plan_hierarchy.py.
    3. No feature access logic leaks into templates or routes.

API contract:
    EntitlementService.can_access_feature(obj, feature_key)  → bool
    EntitlementService.get_feature_context(obj)              → dict  (for templates)
    EntitlementService.check(obj, required_plan)             → EntitlementResult

EntitlementResult has attributes:
    .allowed       : bool
    .reason        : str    (empty when allowed)
    .plan          : str
    .is_admin      : bool
    .required_plan : str
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.plans.plan_hierarchy import (
    PLAN_ADMINISTRATOR,
    has_plan_access,
    is_administrator,
    get_effective_plan,
    get_plan_rank,
)

logger = logging.getLogger(__name__)


# ─── Feature Registry ──────────────────────────────────────────────────────────
# Maps feature_key → minimum plan required.
# Administrator ALWAYS bypasses these gates.
# To add a new gated feature: add one line here. No other code changes needed.

FEATURE_PLAN_REQUIREMENTS: dict[str, str] = {
    # Email providers
    "custom_smtp":          "Basic",
    "resend":               "Pro",
    "mailersend":           "Pro",
    # Content
    "unlimited_pages":      "Pro",
    "unlimited_projects":   "Pro",
    "unlimited_uploads":    "Enterprise",
    # Branding / appearance
    "remove_branding":      "Pro",
    "custom_domain":        "Basic",
    "premium_themes":       "Pro",
    "enterprise_themes":    "Enterprise",
    # Integrations
    "api_keys":             "Pro",
    "team_members":         "Basic",
    "unlimited_team":       "Enterprise",
    # Analytics
    "advanced_analytics":   "Pro",
    "enterprise_analytics": "Enterprise",
    # AI
    "ai_features":          "Pro",
}


# ─── Result Object ─────────────────────────────────────────────────────────────

@dataclass
class EntitlementResult:
    allowed:       bool
    plan:          str
    required_plan: str
    is_admin:      bool
    reason:        str = ""

    def as_dict(self) -> dict:
        return {
            "allowed":        self.allowed,
            "plan":           self.plan,
            "required_plan":  self.required_plan,
            "is_administrator": self.is_admin,
            "reason":         self.reason,
            "upgrade_required": not self.allowed and not self.is_admin,
        }


# ─── Service ───────────────────────────────────────────────────────────────────

class EntitlementService:
    """
    Central entitlement decision engine.
    All methods are @staticmethod — no instantiation needed.
    """

    @staticmethod
    def check(obj, required_plan: str) -> EntitlementResult:
        """
        Full entitlement check: resolve plan, apply administrator bypass,
        then evaluate rank comparison.

        Args:
            obj:           Tenant / Profile / str / dict / None
            required_plan: minimum plan required (e.g. 'Pro', 'Enterprise')

        Returns:
            EntitlementResult with .allowed, .plan, .reason, .is_admin
        """
        plan    = get_effective_plan(obj)
        _admin  = is_administrator(plan)

        if _admin:
            return EntitlementResult(
                allowed=True,
                plan=plan,
                required_plan=required_plan,
                is_admin=True,
                reason="administrator_bypass",
            )

        allowed = has_plan_access(plan, required_plan)
        reason  = "" if allowed else (
            f"This feature requires the {required_plan} plan or higher. "
            f"Your current plan is {plan}."
        )

        logger.debug(
            "[Entitlement] plan=%r required=%r allowed=%s",
            plan, required_plan, allowed,
        )

        return EntitlementResult(
            allowed=allowed,
            plan=plan,
            required_plan=required_plan,
            is_admin=False,
            reason=reason,
        )

    @staticmethod
    def can_access_feature(obj, feature_key: str) -> bool:
        """
        Check a named feature from the FEATURE_PLAN_REQUIREMENTS registry.
        Administrator always returns True.
        Unknown feature_keys default to unrestricted (True) for forward compatibility.

        Usage:
            if not EntitlementService.can_access_feature(profile, 'resend'):
                flash('Upgrade to Pro to use Resend.', 'warning')
        """
        plan = get_effective_plan(obj)
        if is_administrator(plan):
            return True
        required = FEATURE_PLAN_REQUIREMENTS.get(feature_key)
        if required is None:
            # Unknown feature — assume unrestricted (forward compat)
            return True
        return has_plan_access(plan, required)

    @staticmethod
    def get_feature_context(obj) -> dict:
        """
        Build a complete feature-gate context dict for Jinja templates.
        Passes this into template context so templates NEVER compute permissions.

        Usage in route:
            context['entitlements'] = EntitlementService.get_feature_context(profile)

        Usage in template:
            {% if entitlements.premium_themes %}
                ... show theme picker
            {% endif %}
            {% if not entitlements.is_administrator %}
                ... show upgrade banner
            {% endif %}
        """
        plan   = get_effective_plan(obj)
        _admin = is_administrator(plan)

        gates: dict[str, bool] = {}
        for key, required in FEATURE_PLAN_REQUIREMENTS.items():
            gates[key] = True if _admin else has_plan_access(plan, required)

        return {
            "plan":                plan,
            "access_level":        get_plan_rank(plan),
            "is_administrator":    _admin,
            "hide_upgrade_prompts": _admin,
            "quota_bypass":        _admin,
            "premium_unlocked":    _admin or get_plan_rank(plan) >= get_plan_rank("Pro"),
            **gates,
        }

    @staticmethod
    def api_response(obj, required_plan: str) -> dict:
        """
        Produce the standard API JSON response for an entitlement check.
        Matches the backend response standardization spec.

        {
            "plan": "Administrator",
            "access_level": 999,
            "can_access": true,
            "premium_unlocked": true,
            "upgrade_required": false,
            "quota_bypass": true
        }
        """
        result = EntitlementService.check(obj, required_plan)
        plan   = result.plan
        _admin = result.is_admin
        rank   = get_plan_rank(plan)

        return {
            "plan":             plan,
            "access_level":     rank,
            "can_access":       result.allowed,
            "premium_unlocked": _admin or rank >= get_plan_rank("Pro"),
            "upgrade_required": not result.allowed,
            "quota_bypass":     _admin,
            "is_administrator": _admin,
        }
