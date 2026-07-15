"""Provider payload normalization without persistence or signature handling."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Protocol

from app.services.ledger.domain import LedgerPosting


def _timestamp(value, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return fallback


def _integer(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


@dataclass(frozen=True)
class FxSnapshot:
    usd_reporting_amount: Decimal
    rate: Decimal
    source: str
    effective_at: datetime


class ProviderAdapter(Protocol):
    provider: str

    def normalize(
        self, payload: Mapping[str, object], *, tenant_id: int, subscription_id: int | None,
        received_at: datetime, environment: str, fx: FxSnapshot | None = None,
    ) -> LedgerPosting | None: ...


class PayMongoAdapter:
    provider = "paymongo"

    def normalize(self, payload, *, tenant_id, subscription_id, received_at, environment, fx=None):
        data = payload.get("data") or {}
        attrs = data.get("attributes") or {}
        event_type = str(attrs.get("type") or payload.get("type") or "unknown")
        resource = attrs.get("data") or attrs.get("payment") or data
        resource_attrs = resource.get("attributes") or resource
        event_id = str(data.get("id") or payload.get("id") or "")
        refund_like = "refund" in event_type
        chargeback_like = "chargeback" in event_type or "dispute.lost" in event_type
        transaction_id = str(
            (resource.get("id") if isinstance(resource, Mapping) else None)
            or resource_attrs.get("refund_id")
            or resource_attrs.get("id")
            or event_id
        )
        source_transaction_id = str(resource_attrs.get("payment_id") or "") or None
        raw_amount = _integer(resource_attrs.get("amount") if isinstance(resource_attrs, Mapping) else None)
        if raw_amount is None:
            raw_amount = _integer(attrs.get("amount"))
        if raw_amount is None or not event_id or not transaction_id:
            return None
        currency = str(resource_attrs.get("currency") or attrs.get("currency") or "PHP").upper()
        accounting_type = "settlement"
        if refund_like:
            accounting_type, raw_amount = "refund", -abs(raw_amount)
        elif chargeback_like:
            accounting_type, raw_amount = "chargeback", -abs(raw_amount)
        elif event_type not in {"payment.paid", "checkout_session.payment.paid"}:
            return None
        occurred = _timestamp(resource_attrs.get("paid_at") or resource_attrs.get("created_at") or attrs.get("created_at"), received_at)
        metadata = resource_attrs.get("metadata") or attrs.get("metadata") or {}
        usd_amount = fx.usd_reporting_amount if fx else None
        if usd_amount is not None and accounting_type in {"refund", "chargeback"}:
            usd_amount = -abs(usd_amount)
        return LedgerPosting(
            tenant_id=tenant_id, subscription_id=subscription_id, provider=self.provider,
            provider_event_id=event_id, provider_transaction_id=transaction_id,
            event_type=event_type, accounting_type=accounting_type,
            original_amount_minor=raw_amount, original_currency=currency, currency_exponent=2,
            usd_reporting_amount=usd_amount,
            fx_rate=fx.rate if fx else None, fx_rate_source=fx.source if fx else None,
            fx_effective_at=fx.effective_at if fx else None,
            occurred_at=occurred, received_at=received_at, settled_at=occurred,
            provider_environment=environment,
            source_provider_transaction_id=source_transaction_id,
            safe_metadata={
                "billing_cycle": metadata.get("billing_cycle"),
                "plan_code": metadata.get("plan_name"),
                "provider_status": resource_attrs.get("status"),
            },
        )


class DodoAdapter:
    provider = "dodo"

    def normalize(self, payload, *, tenant_id, subscription_id, received_at, environment, fx=None):
        event_type = str(payload.get("type") or "unknown")
        data = payload.get("data") or {}
        obj = data.get("object") if isinstance(data, Mapping) and isinstance(data.get("object"), Mapping) else data
        event_id = str(payload.get("event_id") or payload.get("id") or "")
        refund_like = "refund" in event_type
        chargeback_like = "chargeback" in event_type or "dispute" in event_type
        transaction_id = str(
            (obj.get("refund_id") if refund_like else None)
            or (obj.get("chargeback_id") if chargeback_like else None)
            or obj.get("payment_id")
            or obj.get("id")
            or ""
        )
        source_transaction_id = str(obj.get("payment_id") or "") or None
        amount = _integer(obj.get("total_amount") or obj.get("recurring_pre_tax_amount") or obj.get("amount"))
        if amount is None or not event_id or not transaction_id:
            return None
        accounting_type = "settlement"
        if refund_like:
            accounting_type, amount = "refund", -abs(amount)
        elif chargeback_like:
            accounting_type, amount = "chargeback", -abs(amount)
        elif event_type not in {"payment.succeeded", "subscription.renewed", "checkout.session.completed"}:
            return None
        occurred = _timestamp(obj.get("paid_at") or obj.get("created_at"), received_at)
        metadata = obj.get("metadata") or {}
        usd_amount = fx.usd_reporting_amount if fx else None
        if usd_amount is not None and accounting_type in {"refund", "chargeback"}:
            usd_amount = -abs(usd_amount)
        return LedgerPosting(
            tenant_id=tenant_id, subscription_id=subscription_id, provider=self.provider,
            provider_event_id=event_id, provider_transaction_id=transaction_id,
            event_type=event_type, accounting_type=accounting_type,
            original_amount_minor=amount, original_currency=str(obj.get("currency") or "USD").upper(),
            currency_exponent=2, usd_reporting_amount=usd_amount,
            fx_rate=fx.rate if fx else None, fx_rate_source=fx.source if fx else None,
            fx_effective_at=fx.effective_at if fx else None,
            occurred_at=occurred, received_at=received_at, settled_at=occurred,
            provider_environment=environment,
            source_provider_transaction_id=source_transaction_id,
            safe_metadata={
                "billing_cycle": metadata.get("billing_cycle"),
                "plan_code": metadata.get("plan_code"),
                "provider_status": obj.get("status"),
            },
        )


ADAPTERS = {"paymongo": PayMongoAdapter(), "dodo": DodoAdapter()}
