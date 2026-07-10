"""Country and billing-currency helpers for tenant checkout.

The tenant chooses a country explicitly. Country selection is used only to
suggest a billing currency; authoritative plan prices remain stored in USD.
"""
from __future__ import annotations

DEFAULT_COUNTRY_CODE = "PH"

# Keep this list limited to currencies supported by the billing FX service.
# Philippines is intentionally first because it is the platform's primary
# market, while every value remains user-editable at checkout.
COUNTRIES: dict[str, dict[str, str]] = {
    "PH": {"name": "Philippines", "currency": "PHP", "flag": "🇵🇭"},
    "US": {"name": "United States", "currency": "USD", "flag": "🇺🇸"},
    "CA": {"name": "Canada", "currency": "CAD", "flag": "🇨🇦"},
    "GB": {"name": "United Kingdom", "currency": "GBP", "flag": "🇬🇧"},
    "AU": {"name": "Australia", "currency": "AUD", "flag": "🇦🇺"},
    "NZ": {"name": "New Zealand", "currency": "NZD", "flag": "🇳🇿"},
    "SG": {"name": "Singapore", "currency": "SGD", "flag": "🇸🇬"},
    "HK": {"name": "Hong Kong", "currency": "HKD", "flag": "🇭🇰"},
    "JP": {"name": "Japan", "currency": "JPY", "flag": "🇯🇵"},
    "CN": {"name": "China", "currency": "CNY", "flag": "🇨🇳"},
    "KR": {"name": "South Korea", "currency": "KRW", "flag": "🇰🇷"},
    "IN": {"name": "India", "currency": "INR", "flag": "🇮🇳"},
    "ID": {"name": "Indonesia", "currency": "IDR", "flag": "🇮🇩"},
    "MY": {"name": "Malaysia", "currency": "MYR", "flag": "🇲🇾"},
    "TH": {"name": "Thailand", "currency": "THB", "flag": "🇹🇭"},
    "CH": {"name": "Switzerland", "currency": "CHF", "flag": "🇨🇭"},
    "SE": {"name": "Sweden", "currency": "SEK", "flag": "🇸🇪"},
    "NO": {"name": "Norway", "currency": "NOK", "flag": "🇳🇴"},
    "DK": {"name": "Denmark", "currency": "DKK", "flag": "🇩🇰"},
    "DE": {"name": "Germany", "currency": "EUR", "flag": "🇩🇪"},
    "FR": {"name": "France", "currency": "EUR", "flag": "🇫🇷"},
    "ES": {"name": "Spain", "currency": "EUR", "flag": "🇪🇸"},
    "IT": {"name": "Italy", "currency": "EUR", "flag": "🇮🇹"},
    "NL": {"name": "Netherlands", "currency": "EUR", "flag": "🇳🇱"},
    "IE": {"name": "Ireland", "currency": "EUR", "flag": "🇮🇪"},
    "BE": {"name": "Belgium", "currency": "EUR", "flag": "🇧🇪"},
    "AT": {"name": "Austria", "currency": "EUR", "flag": "🇦🇹"},
    "PT": {"name": "Portugal", "currency": "EUR", "flag": "🇵🇹"},
    "FI": {"name": "Finland", "currency": "EUR", "flag": "🇫🇮"},
    "ZZ": {"name": "Other / International", "currency": "USD", "flag": "🌍"},
}


def normalize_country_code(value: str | None) -> str:
    code = (value or DEFAULT_COUNTRY_CODE).strip().upper()
    return code if code in COUNTRIES else DEFAULT_COUNTRY_CODE


def country_details(value: str | None) -> dict[str, str]:
    code = normalize_country_code(value)
    return {"code": code, **COUNTRIES[code]}


def currency_for_country(value: str | None) -> str:
    return country_details(value)["currency"]


def country_options() -> list[dict[str, str]]:
    first = country_details(DEFAULT_COUNTRY_CODE)
    rest = [
        {"code": code, **data}
        for code, data in sorted(COUNTRIES.items(), key=lambda item: item[1]["name"])
        if code != DEFAULT_COUNTRY_CODE
    ]
    return [first, *rest]
