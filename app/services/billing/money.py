"""Exact-money primitives shared by billing, invoices, and migration code."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class MoneyError(ValueError):
    pass


def normalize_currency(value: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise MoneyError("currency must be a three-letter ISO code")
    return code


def decimal_to_minor(value, exponent: int = 2) -> int:
    if isinstance(value, float):
        raise MoneyError("binary floats require the reviewed legacy conversion path")
    exponent = int(exponent)
    if not 0 <= exponent <= 6:
        raise MoneyError("currency exponent must be between 0 and 6")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MoneyError("invalid money amount") from exc
    if not amount.is_finite():
        raise MoneyError("money amount must be finite")
    quantum = Decimal(1).scaleb(-exponent)
    rounded = amount.quantize(quantum, rounding=ROUND_HALF_UP)
    return int(rounded.scaleb(exponent))


def legacy_float_to_minor(value: float, exponent: int = 2) -> int:
    """Reviewed legacy rule: decimal string conversion, half-up to exponent."""
    if not isinstance(value, (float, int)):
        raise MoneyError("legacy conversion accepts only numeric source values")
    return decimal_to_minor(Decimal(str(value)), exponent)


def minor_to_decimal(minor: int, exponent: int) -> Decimal:
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise MoneyError("minor amount must be an integer")
    exponent = int(exponent)
    if not 0 <= exponent <= 6:
        raise MoneyError("currency exponent must be between 0 and 6")
    return Decimal(minor).scaleb(-exponent)


@dataclass(frozen=True)
class Money:
    minor: int
    currency: str
    exponent: int = 2

    def __post_init__(self):
        object.__setattr__(self, "currency", normalize_currency(self.currency))
        if not 0 <= int(self.exponent) <= 6:
            raise MoneyError("currency exponent must be between 0 and 6")
        if isinstance(self.minor, bool) or not isinstance(self.minor, int):
            raise MoneyError("minor amount must be an integer")

    @property
    def decimal(self) -> Decimal:
        return minor_to_decimal(self.minor, self.exponent)

    def to_dict(self) -> dict:
        return {"minor": self.minor, "currency": self.currency, "exponent": self.exponent}


def mask_reference(value: str | None, visible: int = 4) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unavailable"
    visible = max(2, min(int(visible), 8))
    if len(raw) <= visible * 2:
        return "•" * max(len(raw) - visible, 2) + raw[-visible:]
    return f"{raw[:visible]}…{raw[-visible:]}"


def safe_payment_failure_message(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"failed", "past_due", "payment_failed"}:
        return "The payment was not completed. Check your payment method, then retry from Billing."
    if normalized in {"cancelled", "canceled"}:
        return "The payment was cancelled and no new charge was recorded. You can start again from Billing."
    return "The payment is still being confirmed. Refresh Billing later or contact support if it does not update."
