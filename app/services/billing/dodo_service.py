"""Dodo Payments checkout and webhook helpers.

Uses hosted Checkout Sessions. The browser redirect is never treated as proof of
payment; subscription state is changed only by a verified webhook.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from flask import current_app

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DodoCheckoutResult:
    ok: bool
    checkout_url: str | None = None
    session_id: str | None = None
    error: str | None = None


def is_dodo_enabled() -> bool:
    """Require Render configuration plus the persistent Superadmin toggle."""
    env_enabled = bool(current_app.config.get("DODO_PAYMENTS_ENABLED", False))
    api_configured = bool(current_app.config.get("DODO_PAYMENTS_API_KEY"))
    if not (env_enabled and api_configured):
        return False
    try:
        from app.utils import is_dodo_payments_admin_enabled
        return is_dodo_payments_admin_enabled(default=True)
    except Exception:
        logger.exception("Could not read Dodo Payments admin toggle")
        return False


def _base_url() -> str:
    mode = str(current_app.config.get("DODO_PAYMENTS_MODE", "test")).lower()
    return "https://live.dodopayments.com" if mode == "live" else "https://test.dodopayments.com"


def product_id_for(plan: str, billing_cycle: str) -> str | None:
    """Resolve a Dodo product ID using the app's canonical plan aliases.

    MyPortfolioHub stores the Basic plan internally as ``starter`` while the
    Render/Dodo environment variables use ``BASIC``. Normalize that mismatch
    here so checkout never looks for a non-existent
    ``DODO_STARTER_*_PRODUCT_ID`` variable.
    """
    normalized_plan = str(plan or "").strip().lower().replace(" ", "_")
    normalized_cycle = str(billing_cycle or "").strip().lower()

    plan_env_aliases = {
        "starter": "BASIC",
        "basic": "BASIC",
        "pro": "PRO",
        "business": "ENTERPRISE",
        "enterprise": "ENTERPRISE",
    }
    cycle_env_aliases = {
        "month": "MONTHLY",
        "monthly": "MONTHLY",
        "year": "YEARLY",
        "annual": "YEARLY",
        "annually": "YEARLY",
        "yearly": "YEARLY",
    }

    env_plan = plan_env_aliases.get(normalized_plan, normalized_plan.upper())
    env_cycle = cycle_env_aliases.get(normalized_cycle, normalized_cycle.upper())
    key = f"DODO_{env_plan}_{env_cycle}_PRODUCT_ID"
    return current_app.config.get(key) or os.getenv(key)


def create_checkout_session(*, profile, subscription, plan: str, billing_cycle: str, return_url: str, cancel_url: str) -> DodoCheckoutResult:
    product_id = product_id_for(plan, billing_cycle)
    if not product_id:
        display_plan = {"starter": "Basic", "basic": "Basic", "pro": "Pro", "business": "Enterprise", "enterprise": "Enterprise"}.get(str(plan).lower(), str(plan).title())
        return DodoCheckoutResult(False, error=f"Dodo product is not configured for {display_plan} {billing_cycle}.")

    tenant = getattr(profile, "tenant", None)
    owner = getattr(tenant, "owner", None)
    email = getattr(owner, "email", None) or getattr(profile, "email", None)
    name = getattr(profile, "name", None) or getattr(owner, "username", None) or "Customer"

    metadata = {
        "tenant_id": str(profile.tenant_id),
        "tenant_slug": str(getattr(tenant, "slug", "")),
        "subscription_id": str(subscription.id),
        "plan_code": str(plan),
        "billing_cycle": str(billing_cycle),
    }
    payload: dict[str, Any] = {
        "product_cart": [{"product_id": product_id, "quantity": 1}],
        "return_url": return_url,
        "cancel_url": cancel_url,
        "metadata": metadata,
        "short_link": False,
    }
    if email:
        payload["customer"] = {"email": email, "name": name}

    try:
        response = requests.post(
            f"{_base_url()}/checkouts",
            headers={
                "Authorization": f"Bearer {current_app.config['DODO_PAYMENTS_API_KEY']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {"message": response.text[:500]}
        if not response.ok:
            logger.error("Dodo checkout failed status=%s response=%s", response.status_code, data)
            details = data.get("message") or data.get("error") or data.get("detail")
            if isinstance(details, dict):
                details = details.get("message") or str(details)
            return DodoCheckoutResult(False, error=str(details or f"Dodo checkout failed ({response.status_code})."))
        checkout_url = data.get("checkout_url")
        if not checkout_url:
            return DodoCheckoutResult(False, error="Dodo returned no checkout URL.")
        return DodoCheckoutResult(True, checkout_url=checkout_url, session_id=data.get("session_id"))
    except requests.RequestException as exc:
        logger.exception("Dodo checkout request failed")
        return DodoCheckoutResult(False, error="Payment provider is temporarily unavailable. Please try again.")


def parse_iso_datetime(value: Any):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None
