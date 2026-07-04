"""
app/public/services/pricing_service.py

Data provider for the public /pricing page and the superadmin Pricing CMS
(app/superadmin/routes/pricing_settings.py).

ARCHITECTURE — deliberately NOT a new `LandingPageSettings`/pricing model:
    Money amounts (price_monthly, price_yearly, duration_days) stay owned
    exclusively by app.utils.BILLING_PLANS + the billing/discount services
    that already consume it (PayMongo checkout, tenant billing views,
    superadmin discount manager). Duplicating price fields into a CMS
    table would create two sources of truth for the number that actually
    gets charged — that's a billing-correctness bug waiting to happen, not
    a feature.

    This service adds a MARKETING-ONLY presentation layer on top:
    badge text ("Most Popular"), CTA copy/links, a feature-list override,
    a description override, and which single plan is visually highlighted.
    Stored as one JSON blob per plan in PlatformSetting (widened to TEXT
    in migration 0044), using the same draft/publish key convention as
    app/superadmin/routes/landing_settings.py.

SECURITY:
    All returned data is plain dict/str/bool/list — never a raw model
    instance. CTA URLs are validated at write-time (see
    pricing_settings.py::_is_safe_cta_url) so this read path can trust
    what's stored, but templates must still rely on Jinja's default
    autoescaping (no `|safe` on any CMS-sourced string).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.models.core import PlatformSetting
from app.utils import (
    BILLING_PLANS,
    get_plan_price,
    get_plan_price_label,
    get_yearly_savings_label,
    get_yearly_discount_percent,
)

logger = logging.getLogger(__name__)

_PLAN_CONFIG_KEY_PREFIX = "pricing_plan_"
SECTION_KEYS = {
    "heading": "pricing_heading",
    "subtitle": "pricing_subtitle",
    "footnote": "pricing_footnote",
}
YEARLY_TOGGLE_KEY = "pricing_yearly_toggle_enabled"

_DEFAULT_SECTION = {
    "heading": "Simple, transparent pricing",
    "subtitle": (
        "Every plan includes PayMongo checkout billing and automated "
        "renewals. Upgrade or downgrade anytime."
    ),
    "footnote": (
        "Online checkout is currently unavailable — manual payment "
        "instructions will be provided after sign-up."
    ),
}

DEFAULT_PLAN_CONFIG = {
    "badge_text": "",
    "cta_text": "Get Started",
    "cta_url": "",  # empty → caller falls back to the signup route
    "description_override": "",
    "features_override": [],
    "highlighted": False,
}


def plan_config_key(plan: str, draft: bool = False) -> str:
    base = f"{_PLAN_CONFIG_KEY_PREFIX}{plan.strip().lower()}"
    return f"{base}_draft" if draft else base


def draft_key(published_key: str) -> str:
    return f"{published_key}_draft"


def get_plan_config(plan: str, draft_first: bool = False, fallback_to_published: bool = False) -> dict:
    """Return the marketing-override config for one plan, merged over safe
    defaults so callers never have to null-check individual fields."""
    if draft_first:
        raw = PlatformSetting.get_json(plan_config_key(plan, draft=True))
        if raw is None and fallback_to_published:
            raw = PlatformSetting.get_json(plan_config_key(plan, draft=False))
    else:
        raw = PlatformSetting.get_json(plan_config_key(plan, draft=False))

    config = dict(DEFAULT_PLAN_CONFIG)
    if isinstance(raw, dict):
        config.update({k: v for k, v in raw.items() if k in DEFAULT_PLAN_CONFIG})
    return config


def get_section_content(draft_first: bool = False, fallback_to_published: bool = False) -> dict:
    values = dict(_DEFAULT_SECTION)
    for field, published_key in SECTION_KEYS.items():
        if draft_first:
            v = PlatformSetting.get_string(draft_key(published_key), default="") or ""
            if not v and fallback_to_published:
                v = PlatformSetting.get_string(published_key, default="") or ""
        else:
            v = PlatformSetting.get_string(published_key, default="") or ""
        if v:
            values[field] = v
    return values


def get_yearly_toggle_enabled(draft_first: bool = False, fallback_to_published: bool = False) -> bool:
    if draft_first:
        v = PlatformSetting.get_bool(draft_key(YEARLY_TOGGLE_KEY))
        if v is None and fallback_to_published:
            v = PlatformSetting.get_bool(YEARLY_TOGGLE_KEY, default=False)
        return bool(v) if v is not None else False
    return bool(PlatformSetting.get_bool(YEARLY_TOGGLE_KEY, default=False))


def get_pricing_content(draft_first: bool = False, fallback_to_published: bool = True) -> dict:
    """
    Single entry point for both the public /pricing page and the
    superadmin live preview. Returns everything a template needs:

        {
            "section": {heading, subtitle, footnote},
            "yearly_toggle_enabled": bool,
            "plans": {
                "Basic": {..BILLING_PLANS["Basic"].., "cms": {..config..},
                          "price_monthly_label": "...", "price_yearly_label": "...",
                          "yearly_savings_label": "...", "yearly_discount_percent": 17},
                ...
            },
        }

    Never raises — falls back to code defaults on any storage error so a
    bad CMS write can never take down the public pricing page.
    """
    try:
        section = get_section_content(draft_first=draft_first, fallback_to_published=fallback_to_published)
        yearly_toggle_enabled = get_yearly_toggle_enabled(
            draft_first=draft_first, fallback_to_published=fallback_to_published
        )

        plans: dict[str, dict] = {}
        for plan_name, plan_data in BILLING_PLANS.items():
            cms = get_plan_config(
                plan_name, draft_first=draft_first, fallback_to_published=fallback_to_published
            )
            plans[plan_name] = {
                **plan_data,
                "cms": cms,
                # Effective display values: CMS override wins, else code default.
                "display_description": cms["description_override"] or plan_data["description"],
                "display_features": cms["features_override"] or plan_data["features"],
                "price_monthly_label": get_plan_price_label(plan_name, "monthly"),
                "price_yearly_label": get_plan_price_label(plan_name, "yearly"),
                "yearly_savings_label": get_yearly_savings_label(plan_name),
                "yearly_discount_percent": get_yearly_discount_percent(plan_name),
            }

        return {
            "section": section,
            "yearly_toggle_enabled": yearly_toggle_enabled,
            "plans": plans,
        }
    except Exception:
        logger.exception("get_pricing_content failed — falling back to code defaults")
        return {
            "section": dict(_DEFAULT_SECTION),
            "yearly_toggle_enabled": False,
            "plans": {
                name: {**data, "cms": dict(DEFAULT_PLAN_CONFIG),
                       "display_description": data["description"],
                       "display_features": data["features"],
                       "price_monthly_label": data.get("price_label", ""),
                       "price_yearly_label": "",
                       "yearly_savings_label": "",
                       "yearly_discount_percent": 0}
                for name, data in BILLING_PLANS.items()
            },
        }
