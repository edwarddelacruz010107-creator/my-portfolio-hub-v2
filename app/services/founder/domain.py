"""Dependency-free Phase 9 filter, period, comparison, and privacy contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping, Any


FOUNDER_DASHBOARD_VERSION = "founder-dashboard-2026.07-v1"
LIFECYCLE_DEFINITION_VERSION = "tenant-lifecycle-2026.07-v1"
PORTFOLIO_READ_MODEL_VERSION = "portfolio-operations-2026.07-v1"
OPERATIONS_READ_MODEL_VERSION = "platform-operations-2026.07-v1"
PRIVACY_THRESHOLD = 5
RANGE_DAYS = (7, 30, 90, 365)
COMPARISONS = frozenset({"none", "previous"})
PAYMENT_PROVIDERS = frozenset({"all", "dodo", "paymongo", "manual"})
AI_PROVIDERS = frozenset({
    "all", "openai", "anthropic", "gemini", "groq", "openrouter", "ollama", "azure_openai",
})
PLANS = frozenset({"all", "trial", "starter", "pro", "business", "enterprise", "administrator"})
PLAN_ALIASES = {"basic": "starter", "premium": "pro", "admin": "administrator"}


@dataclass(frozen=True)
class FounderFilters:
    days: int = 30
    comparison: str = "previous"
    payment_provider: str = "all"
    ai_provider: str = "all"
    plan: str = "all"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "FounderFilters":
        try:
            days = int(str(values.get("range") or 30))
        except (TypeError, ValueError):
            days = 30
        if days not in RANGE_DAYS:
            days = 30
        comparison = str(values.get("compare") or "previous").strip().lower()
        payment_provider = str(values.get("payment_provider") or "all").strip().lower()
        ai_provider = str(values.get("ai_provider") or "all").strip().lower()
        plan = str(values.get("plan") or "all").strip().lower()
        plan = PLAN_ALIASES.get(plan, plan)
        return cls(
            days=days,
            comparison=comparison if comparison in COMPARISONS else "previous",
            payment_provider=payment_provider if payment_provider in PAYMENT_PROVIDERS else "all",
            ai_provider=ai_provider if ai_provider in AI_PROVIDERS else "all",
            plan=plan if plan in PLANS else "all",
        )

    def periods(self, *, as_of: datetime | None = None) -> dict[str, datetime | None]:
        end_at = as_of or datetime.now(timezone.utc)
        if end_at.tzinfo is None:
            raise ValueError("founder dashboard as_of must be timezone-aware")
        end_at = end_at.astimezone(timezone.utc)
        start_at = end_at - timedelta(days=self.days)
        comparison_end = start_at if self.comparison == "previous" else None
        comparison_start = (
            comparison_end - timedelta(days=self.days) if comparison_end is not None else None
        )
        return {
            "start_at": start_at,
            "end_at": end_at,
            "comparison_start_at": comparison_start,
            "comparison_end_at": comparison_end,
        }

    def cache_fragment(self) -> str:
        return ":".join((
            str(self.days), self.comparison, self.payment_provider, self.ai_provider, self.plan,
        ))


def safe_rate(numerator: int, denominator: int, *, threshold: int = PRIVACY_THRESHOLD) -> dict[str, Any]:
    if denominator < threshold:
        return {
            "available": False,
            "value": None,
            "reason": f"Requires at least {threshold} eligible records",
            "numerator": int(numerator),
            "denominator": int(denominator),
        }
    value = (Decimal(int(numerator)) * Decimal("100") / Decimal(int(denominator))).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return {
        "available": True,
        "value": value,
        "reason": "",
        "numerator": int(numerator),
        "denominator": int(denominator),
    }


def comparison_change(current, previous) -> dict[str, Any]:
    if previous is None or Decimal(str(previous)) == 0:
        return {"available": False, "percent": None, "reason": "No non-zero comparison baseline"}
    percent = (
        (Decimal(str(current)) - Decimal(str(previous)))
        / abs(Decimal(str(previous)))
        * Decimal("100")
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return {"available": True, "percent": percent, "reason": ""}
