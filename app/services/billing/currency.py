"""Billing currency and FX conversion service.

USD is the immutable base currency for plan pricing. A superadmin may select a
presentation/payment currency; plan amounts are converted from the saved USD
base price using a cached exchange rate.

Providers:
* ``frankfurter`` (default, no API key, daily institutional reference rates)
* ``currencyapi`` (optional ``CURRENCYAPI_KEY``; update frequency depends on plan)

The server always recomputes payable amounts. Browser-submitted amounts are not
trusted.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_CURRENCY = "USD"
DEFAULT_DISPLAY_CURRENCY = "USD"
SETTINGS_KEY = "billing_currency_settings_v1"
FX_CACHE_KEY = "billing_fx_cache_v1"
PLAN_USD_KEY = "billing_plan_usd_prices_v1"
PLAN_CONFIG_KEY = "billing_plan_config_v1"

SUPPORTED_CURRENCIES: dict[str, dict[str, str]] = {
    "USD": {"name": "US Dollar", "symbol": "$"},
    "PHP": {"name": "Philippine Peso", "symbol": "₱"},
    "EUR": {"name": "Euro", "symbol": "€"},
    "GBP": {"name": "British Pound", "symbol": "£"},
    "JPY": {"name": "Japanese Yen", "symbol": "¥"},
    "AUD": {"name": "Australian Dollar", "symbol": "A$"},
    "CAD": {"name": "Canadian Dollar", "symbol": "C$"},
    "SGD": {"name": "Singapore Dollar", "symbol": "S$"},
    "HKD": {"name": "Hong Kong Dollar", "symbol": "HK$"},
    "NZD": {"name": "New Zealand Dollar", "symbol": "NZ$"},
}

_MEMORY_CACHE: dict[str, dict[str, Any]] = {}

DEFAULT_SETTINGS = {
    "base_currency": BASE_CURRENCY,
    "display_currency": DEFAULT_DISPLAY_CURRENCY,
    "provider": "frankfurter",
    "refresh_minutes": 60,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _setting_model():
    from app.models.portfolio import PlatformSetting
    return PlatformSetting


def _safe_currency(value: str | None) -> str:
    code = (value or DEFAULT_DISPLAY_CURRENCY).strip().upper()
    return code if code in SUPPORTED_CURRENCIES else DEFAULT_DISPLAY_CURRENCY


def get_currency_settings() -> dict[str, Any]:
    try:
        raw = _setting_model().get_json(SETTINGS_KEY, {}) or {}
    except Exception:
        raw = {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update({k: v for k, v in raw.items() if k in settings})
    settings["base_currency"] = BASE_CURRENCY
    settings["display_currency"] = _safe_currency(settings.get("display_currency"))
    settings["provider"] = str(settings.get("provider") or "frankfurter").lower()
    if settings["provider"] not in {"frankfurter", "currencyapi"}:
        settings["provider"] = "frankfurter"
    try:
        settings["refresh_minutes"] = max(5, min(1440, int(settings.get("refresh_minutes", 60))))
    except (TypeError, ValueError):
        settings["refresh_minutes"] = 60
    return settings


def save_currency_settings(*, display_currency: str, provider: str, refresh_minutes: int = 60) -> dict[str, Any]:
    settings = {
        "base_currency": BASE_CURRENCY,
        "display_currency": _safe_currency(display_currency),
        "provider": provider if provider in {"frankfurter", "currencyapi"} else "frankfurter",
        "refresh_minutes": max(5, min(1440, int(refresh_minutes or 60))),
    }
    _setting_model().set_json(SETTINGS_KEY, settings)
    return settings


def currency_symbol(code: str | None = None) -> str:
    code = _safe_currency(code or get_currency_settings()["display_currency"])
    return SUPPORTED_CURRENCIES[code]["symbol"]


def format_money(amount: float | Decimal | int | None, code: str | None = None, *, include_code: bool = False) -> str:
    code = _safe_currency(code or get_currency_settings()["display_currency"])
    symbol = currency_symbol(code)
    try:
        value = Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        value = Decimal("0.00")
    text = f"{symbol}{value:,.2f}"
    return f"{text} {code}" if include_code else text


def get_plan_config() -> dict[str, dict[str, Any]]:
    try:
        raw = _setting_model().get_json(PLAN_CONFIG_KEY, {}) or {}
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def save_plan_config(config: dict[str, dict[str, Any]]) -> None:
    clean: dict[str, dict[str, Any]] = {}
    for plan, values in config.items():
        if not isinstance(values, dict):
            continue
        clean[plan] = {
            "label": str(values.get("label") or plan)[:80],
            "duration_days": max(1, min(3650, int(values.get("duration_days") or 30))),
            "description": str(values.get("description") or "")[:500],
            "features": [str(x).strip()[:200] for x in (values.get("features") or []) if str(x).strip()][:30],
            "payment_link": str(values.get("payment_link") or "")[:500],
        }
    _setting_model().set_json(PLAN_CONFIG_KEY, clean)


def get_plan_usd_prices(defaults: dict[str, float] | None = None) -> dict[str, float]:
    defaults = defaults or {"Basic": 1.0, "Pro": 49.0, "Enterprise": 99.0}
    try:
        raw = _setting_model().get_json(PLAN_USD_KEY, {}) or {}
    except Exception:
        raw = {}
    result: dict[str, float] = {}
    for plan, fallback in defaults.items():
        try:
            result[plan] = max(0.0, round(float(raw.get(plan, fallback)), 2))
        except (TypeError, ValueError):
            result[plan] = float(fallback)
    return result


def save_plan_usd_prices(prices: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, round(float(v), 2)) for k, v in prices.items()}
    _setting_model().set_json(PLAN_USD_KEY, clean)
    return clean


def _read_cache(target: str) -> dict[str, Any] | None:
    cache = _MEMORY_CACHE.get(target)
    if not cache:
        try:
            cache = _setting_model().get_json(FX_CACHE_KEY, {}) or {}
        except Exception:
            return None
    if cache.get("base") != BASE_CURRENCY or cache.get("target") != target:
        return None
    try:
        rate = float(cache.get("rate"))
        fetched_at = datetime.fromisoformat(str(cache.get("fetched_at")))
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if rate <= 0:
        return None
    cache = dict(cache)
    cache["rate"] = rate
    cache["fetched_at_dt"] = fetched_at
    _MEMORY_CACHE[target] = {k: v for k, v in cache.items() if k != "fetched_at_dt"}
    return cache


def _write_cache(target: str, rate: float, provider: str, source_date: str | None = None) -> dict[str, Any]:
    payload = {
        "base": BASE_CURRENCY,
        "target": target,
        "rate": float(rate),
        "provider": provider,
        "source_date": source_date or "",
        "fetched_at": _utcnow().isoformat(),
    }
    _MEMORY_CACHE[target] = dict(payload)
    _setting_model().set_json(FX_CACHE_KEY, payload)
    return payload


def _fetch_frankfurter(target: str) -> tuple[float, str | None]:
    url = "https://api.frankfurter.dev/v1/latest"
    response = requests.get(url, params={"base": BASE_CURRENCY, "symbols": target}, timeout=8)
    response.raise_for_status()
    payload = response.json()
    rate = float((payload.get("rates") or {}).get(target))
    if rate <= 0:
        raise ValueError(f"No {target} rate in Frankfurter response")
    return rate, payload.get("date")


def _fetch_currencyapi(target: str) -> tuple[float, str | None]:
    api_key = (os.getenv("CURRENCYAPI_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("CURRENCYAPI_KEY is not configured")
    response = requests.get(
        "https://api.currencyapi.com/v3/latest",
        params={"apikey": api_key, "base_currency": BASE_CURRENCY, "currencies": target},
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    rate = float((((payload.get("data") or {}).get(target) or {}).get("value")))
    if rate <= 0:
        raise ValueError(f"No {target} rate in CurrencyAPI response")
    return rate, (payload.get("meta") or {}).get("last_updated_at")


def get_exchange_rate(*, force: bool = False, target: str | None = None) -> dict[str, Any]:
    settings = get_currency_settings()
    target = _safe_currency(target or settings["display_currency"])
    if target == BASE_CURRENCY:
        return {
            "base": BASE_CURRENCY, "target": target, "rate": 1.0,
            "provider": "fixed", "source_date": "", "fetched_at": _utcnow(),
            "stale": False, "available": True,
        }

    cache = _read_cache(target)
    max_age = timedelta(minutes=settings["refresh_minutes"])
    if cache and not force and (_utcnow() - cache["fetched_at_dt"]) <= max_age:
        return {
            **cache, "fetched_at": cache["fetched_at_dt"],
            "stale": False, "available": True,
        }

    provider = settings["provider"]
    try:
        if provider == "currencyapi":
            rate, source_date = _fetch_currencyapi(target)
        else:
            rate, source_date = _fetch_frankfurter(target)
        payload = _write_cache(target, rate, provider, source_date)
        return {
            **payload, "fetched_at": datetime.fromisoformat(payload["fetched_at"]),
            "stale": False, "available": True,
        }
    except Exception as exc:
        logger.warning("FX refresh failed provider=%s target=%s: %s", provider, target, exc)
        # If the premium provider is unavailable/misconfigured, fall back to
        # the no-key institutional reference service before giving up.
        if provider != "frankfurter":
            try:
                rate, source_date = _fetch_frankfurter(target)
                payload = _write_cache(target, rate, "frankfurter-fallback", source_date)
                return {
                    **payload, "fetched_at": datetime.fromisoformat(payload["fetched_at"]),
                    "stale": False, "available": True,
                }
            except Exception as fallback_exc:
                logger.warning("FX fallback failed target=%s: %s", target, fallback_exc)
        if cache:
            return {
                **cache, "fetched_at": cache["fetched_at_dt"],
                "stale": True, "available": True, "error": str(exc),
            }
        return {
            "base": BASE_CURRENCY, "target": target, "rate": 1.0,
            "provider": provider, "source_date": "", "fetched_at": None,
            "stale": True, "available": False, "error": str(exc),
        }


def convert_usd(amount_usd: float | Decimal | int, *, target: str | None = None, force: bool = False) -> float:
    fx = get_exchange_rate(force=force, target=target)
    amount = Decimal(str(amount_usd or 0)) * Decimal(str(fx["rate"]))
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def apply_currency_pricing(plans: dict[str, dict]) -> dict[str, dict]:
    """Mutate plan dictionaries with persisted USD prices + converted values."""
    settings = get_currency_settings()
    code = settings["display_currency"]
    fx = get_exchange_rate(target=code)
    symbol = currency_symbol(code)
    overrides = get_plan_config()
    for key, values in overrides.items():
        if key in plans and isinstance(values, dict):
            plans[key].update(values)
    defaults = {
        key: float(data.get("base_price_usd", data.get("price_monthly", data.get("price", 0))))
        for key, data in plans.items()
    }
    usd_prices = get_plan_usd_prices(defaults)
    from app.utils import get_yearly_discount
    for key, data in plans.items():
        usd_monthly = usd_prices.get(key, defaults.get(key, 0.0))
        monthly = round(usd_monthly * float(fx["rate"]), 2)
        yearly_usd = round(usd_monthly * 12 * get_yearly_discount(key), 2)
        yearly = round(yearly_usd * float(fx["rate"]), 2)
        data.update({
            "base_currency": BASE_CURRENCY,
            "base_price_usd": usd_monthly,
            "base_price_yearly_usd": yearly_usd,
            "currency_code": code,
            "currency_symbol": symbol,
            "fx_rate": float(fx["rate"]),
            "fx_provider": fx.get("provider"),
            "price": monthly,
            "price_monthly": monthly,
            "price_yearly": yearly,
            "price_label": f"{symbol}{monthly:,.2f}/mo",
            "price_usd_label": f"${usd_monthly:,.2f} USD/mo",
        })
    return plans


def currency_context(*, force: bool = False) -> dict[str, Any]:
    settings = get_currency_settings()
    fx = get_exchange_rate(force=force, target=settings["display_currency"])
    return {
        "settings": settings,
        "fx": fx,
        "base_currency": BASE_CURRENCY,
        "display_currency": settings["display_currency"],
        "symbol": currency_symbol(settings["display_currency"]),
        "supported": SUPPORTED_CURRENCIES,
    }
