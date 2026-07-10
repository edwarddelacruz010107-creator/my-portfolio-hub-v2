#!/usr/bin/env python3
"""Test FreecurrencyAPI configuration without exposing the API key."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # Production can rely entirely on environment variables.
    load_dotenv = None


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    if load_dotenv is not None:
        load_dotenv(project_root / ".env", override=False)

    api_key = (os.getenv("FREECURRENCYAPI_KEY") or "").strip()
    if not api_key:
        print("ERROR: FREECURRENCYAPI_KEY is not configured.", file=sys.stderr)
        return 2

    targets = (os.getenv("CURRENCY_TEST_TARGETS") or "PHP,EUR,GBP,JPY").strip()
    response = requests.get(
        "https://api.freecurrencyapi.com/v1/latest",
        headers={"apikey": api_key, "Accept": "application/json"},
        params={"base_currency": "USD", "currencies": targets},
        timeout=10,
    )

    if response.status_code == 429:
        print("ERROR: API quota or per-minute rate limit reached (HTTP 429).", file=sys.stderr)
        return 3

    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"ERROR: FreecurrencyAPI request failed: {exc}", file=sys.stderr)
        return 4

    rates = payload.get("data") or {}
    if not rates:
        print("ERROR: API response did not contain rates.", file=sys.stderr)
        return 5

    print("FreecurrencyAPI connection OK")
    print("Base: USD")
    for code, rate in sorted(rates.items()):
        print(f"1 USD = {rate} {code}")
    meta = payload.get("meta") or {}
    if meta.get("last_updated_at"):
        print(f"Last updated: {meta['last_updated_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
