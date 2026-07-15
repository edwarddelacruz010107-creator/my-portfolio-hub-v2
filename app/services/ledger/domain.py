"""Dependency-free ledger value objects and exact-money aggregation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable, Mapping


PROVIDERS = frozenset({"dodo", "paymongo", "manual"})
ACCOUNTING_TYPES = frozenset({"settlement", "refund", "reversal", "adjustment", "chargeback"})
POSTING_STATUSES = frozenset({"posted", "review_required"})
ENVIRONMENTS = frozenset({"live", "test"})
SAFE_METADATA_KEYS = frozenset({
    "billing_cycle", "plan_code", "source_type", "source_id", "invoice_number",
    "country_code", "coupon_code", "reason_code", "provider_status",
})
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def exact_decimal(value, *, name: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{name} must not be a binary float")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not a valid decimal") from exc


def aware_utc(value: datetime, *, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def major_from_minor(amount_minor: int, exponent: int) -> Decimal:
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise TypeError("amount_minor must be an integer")
    if not isinstance(exponent, int) or not 0 <= exponent <= 6:
        raise ValueError("currency_exponent must be between 0 and 6")
    return Decimal(amount_minor).scaleb(-exponent)


def sanitize_metadata(values: Mapping[str, object] | None) -> dict[str, str]:
    if not values:
        return {}
    safe: dict[str, str] = {}
    for key, value in values.items():
        if key not in SAFE_METADATA_KEYS or value is None:
            continue
        rendered = str(value)
        safe[key] = rendered[:160]
    return safe


@dataclass(frozen=True)
class LedgerPosting:
    tenant_id: int
    provider: str
    provider_event_id: str
    provider_transaction_id: str
    event_type: str
    accounting_type: str
    original_amount_minor: int
    original_currency: str
    currency_exponent: int
    occurred_at: datetime
    received_at: datetime
    subscription_id: int | None = None
    provider_account: str = "default"
    provider_environment: str = "live"
    usd_reporting_amount: Decimal | None = None
    fx_rate: Decimal | None = None
    fx_rate_source: str | None = None
    fx_effective_at: datetime | None = None
    settled_at: datetime | None = None
    reversal_of_id: str | None = None
    source_provider_transaction_id: str | None = None
    created_by: str | None = None
    approved_by: str | None = None
    safe_metadata: Mapping[str, object] = field(default_factory=dict)
    status: str | None = None

    def __post_init__(self):
        provider = str(self.provider).strip().lower()
        accounting_type = str(self.accounting_type).strip().lower()
        environment = str(self.provider_environment).strip().lower()
        currency = str(self.original_currency).strip().upper()
        if provider not in PROVIDERS:
            raise ValueError("unsupported payment provider")
        if accounting_type not in ACCOUNTING_TYPES:
            raise ValueError("unsupported accounting type")
        if environment not in ENVIRONMENTS:
            raise ValueError("provider environment must be live or test")
        if not CURRENCY_RE.fullmatch(currency):
            raise ValueError("original currency must be a three-letter code")
        if not isinstance(self.tenant_id, int) or self.tenant_id <= 0:
            raise ValueError("tenant_id must be a positive integer")
        if isinstance(self.original_amount_minor, bool) or not isinstance(self.original_amount_minor, int):
            raise TypeError("original_amount_minor must be an integer")
        major_from_minor(self.original_amount_minor, self.currency_exponent)
        if accounting_type == "settlement" and self.original_amount_minor <= 0:
            raise ValueError("settlement amount must be positive")
        if accounting_type in {"refund", "reversal", "chargeback"} and self.original_amount_minor >= 0:
            raise ValueError(f"{accounting_type} amount must be negative")
        for name, identifier in (
            ("provider_event_id", self.provider_event_id),
            ("provider_transaction_id", self.provider_transaction_id),
            ("event_type", self.event_type),
        ):
            if not str(identifier or "").strip():
                raise ValueError(f"{name} is required")
        if self.source_provider_transaction_id is not None and not str(self.source_provider_transaction_id).strip():
            raise ValueError("source_provider_transaction_id cannot be blank")

        occurred = aware_utc(self.occurred_at, name="occurred_at")
        received = aware_utc(self.received_at, name="received_at")
        settled = aware_utc(self.settled_at, name="settled_at") if self.settled_at else None
        fx_effective = aware_utc(self.fx_effective_at, name="fx_effective_at") if self.fx_effective_at else None
        usd_amount = exact_decimal(self.usd_reporting_amount, name="usd_reporting_amount") if self.usd_reporting_amount is not None else None
        fx_rate = exact_decimal(self.fx_rate, name="fx_rate") if self.fx_rate is not None else None
        if fx_rate is not None and fx_rate <= 0:
            raise ValueError("fx_rate must be positive")
        if currency == "USD" and usd_amount is None:
            usd_amount = major_from_minor(self.original_amount_minor, self.currency_exponent)
        if usd_amount is not None:
            if accounting_type == "settlement" and usd_amount <= 0:
                raise ValueError("settlement USD amount must be positive")
            if accounting_type in {"refund", "reversal", "chargeback"} and usd_amount >= 0:
                raise ValueError(f"{accounting_type} USD amount must be negative")
            if accounting_type == "adjustment" and usd_amount == 0:
                raise ValueError("adjustment USD amount must be non-zero")
        fx_complete = currency == "USD" or all((usd_amount is not None, fx_rate is not None, self.fx_rate_source, fx_effective))
        computed_status = "posted" if fx_complete else "review_required"
        if self.status is not None and self.status not in POSTING_STATUSES:
            raise ValueError("unsupported posting status")
        if self.status == "posted" and not fx_complete:
            raise ValueError("non-USD posted rows require reproducible FX evidence")
        status = self.status or computed_status

        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "accounting_type", accounting_type)
        object.__setattr__(self, "provider_environment", environment)
        object.__setattr__(self, "original_currency", currency)
        object.__setattr__(self, "occurred_at", occurred)
        object.__setattr__(self, "received_at", received)
        object.__setattr__(self, "settled_at", settled)
        object.__setattr__(self, "fx_effective_at", fx_effective)
        object.__setattr__(self, "usd_reporting_amount", usd_amount)
        object.__setattr__(self, "fx_rate", fx_rate)
        object.__setattr__(self, "safe_metadata", sanitize_metadata(self.safe_metadata))
        object.__setattr__(self, "status", status)

    @property
    def idempotency_key(self) -> tuple[str, str, str, str]:
        return self.provider, self.provider_environment, self.provider_event_id, self.accounting_type


def monthly_recurring_amount(amount: Decimal | int | str, billing_cycle: str) -> Decimal:
    """Normalize a recurring settlement to one month using exact decimals."""
    value = exact_decimal(amount, name="amount")
    cycle = str(billing_cycle or "monthly").strip().lower()
    return value / Decimal("12") if cycle in {"yearly", "annual", "annually", "year"} else value


@dataclass(frozen=True)
class LedgerAggregate:
    gross_usd: Decimal
    net_usd: Decimal
    provider_usd: Mapping[str, Decimal]
    source_coverage: Mapping[str, int]


def aggregate_postings(
    postings: Iterable[LedgerPosting],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    tenant_id: int | None = None,
) -> LedgerAggregate:
    start_utc = aware_utc(start, name="start") if start else None
    end_utc = aware_utc(end, name="end") if end else None
    provider_totals = {provider: Decimal("0") for provider in sorted(PROVIDERS)}
    coverage = {"posted": 0, "review_required": 0}
    gross = Decimal("0")
    net = Decimal("0")
    for posting in postings:
        if tenant_id is not None and posting.tenant_id != tenant_id:
            continue
        if start_utc and posting.occurred_at < start_utc:
            continue
        if end_utc and posting.occurred_at >= end_utc:
            continue
        coverage[posting.status] += 1
        if posting.status != "posted" or posting.usd_reporting_amount is None:
            continue
        amount = posting.usd_reporting_amount
        provider_totals[posting.provider] += amount
        net += amount
        if posting.accounting_type in {"settlement", "adjustment"} and amount > 0:
            gross += amount
    return LedgerAggregate(gross, net, provider_totals, coverage)
