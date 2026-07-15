"""Versioned plan catalog and immutable sold-plan snapshots."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from flask import current_app, has_app_context

from app.models.core import get_plan_features, normalize_plan_name
from app.system_plan import has_administrator_access
from app.utils import BILLING_PLANS


CATALOG_SCHEMA_VERSION = "billing-catalog/v1"
VALID_CYCLES = frozenset({"monthly", "yearly"})


def _config(key: str, default=None):
    return current_app.config.get(key, default) if has_app_context() else default


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class CatalogSnapshot:
    plan_code: str
    display_name: str
    catalog_version: str
    billing_cycle: str
    amount: Decimal
    currency: str
    entitlements: dict
    provider_mappings: dict
    effective_from: str | None
    effective_to: str | None

    def to_dict(self) -> dict:
        return {
            "schema": CATALOG_SCHEMA_VERSION,
            "plan_code": self.plan_code,
            "display_name": self.display_name,
            "catalog_version": self.catalog_version,
            "billing_cycle": self.billing_cycle,
            "amount": format(self.amount, "f"),
            "currency": self.currency,
            "entitlements": self.entitlements,
            "provider_mappings": self.provider_mappings,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
        }


class PlanService:
    def normalize_plan(self, plan: str | None) -> str:
        return normalize_plan_name(plan)

    def features_for(self, plan: str | None) -> dict:
        return get_plan_features(plan or "starter")

    def has_feature(self, tenant, feature: str) -> bool:
        if tenant is None:
            return False
        if has_administrator_access(tenant):
            return True
        plan = tenant.effective_plan() if callable(getattr(tenant, "effective_plan", None)) else getattr(tenant, "plan", "starter")
        return bool(self.features_for(plan).get(feature, False))

    def snapshot(self, plan: str, billing_cycle: str) -> CatalogSnapshot:
        cycle = str(billing_cycle or "").strip().lower()
        if cycle not in VALID_CYCLES:
            raise ValueError("unsupported billing cycle")
        code = self.normalize_plan(plan)
        if code not in BILLING_PLANS:
            raise ValueError("plan is not available in the billing catalog")
        definition = BILLING_PLANS[code]
        price_key = "price_yearly" if cycle == "yearly" else "price_monthly"
        amount = Decimal(str(definition[price_key]))
        currency = str(definition.get("currency_code") or "").strip().upper()
        if len(currency) != 3:
            raise ValueError("catalog plan has no valid currency")

        provider_mappings = {}
        dodo_key = f"DODO_{code.upper()}_{cycle.upper()}_PRODUCT_ID"
        dodo_product = _config(dodo_key, "")
        if dodo_product:
            provider_mappings["dodo"] = {"product_id": dodo_product}
        provider_mappings["paymongo"] = {"pricing_mode": "dynamic_line_item"}
        provider_mappings["manual"] = {"pricing_mode": "reviewed_submission"}

        payload = {
            "schema": CATALOG_SCHEMA_VERSION,
            "plan_code": code,
            "billing_cycle": cycle,
            "amount": format(amount, "f"),
            "currency": currency,
            "entitlements": self.features_for(code),
            "provider_mappings": provider_mappings,
            "effective_from": definition.get("effective_from") or _config("BILLING_CATALOG_EFFECTIVE_FROM"),
            "effective_to": definition.get("effective_to"),
        }
        version = f"{CATALOG_SCHEMA_VERSION}:{hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]}"
        return CatalogSnapshot(
            plan_code=code,
            display_name=str(definition.get("label") or code),
            catalog_version=version,
            billing_cycle=cycle,
            amount=amount,
            currency=currency,
            entitlements=payload["entitlements"],
            provider_mappings=provider_mappings,
            effective_from=payload["effective_from"],
            effective_to=payload["effective_to"],
        )

    def all_snapshots(self, billing_cycle: str = "monthly") -> list[CatalogSnapshot]:
        return [self.snapshot(code, billing_cycle) for code in BILLING_PLANS]
