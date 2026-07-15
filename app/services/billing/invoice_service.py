"""
app/services/billing/invoice_service.py — Invoice accounting record
(v7.7 — Invoice subsystem).

This is the ONLY place invoice rows are written. Called from the same
three activation call sites that call discount_checkout.apply_on_activation()
— manual approval (manual_billing.py), the PayMongo webhook
(webhooks/__init__.py), and superadmin resync (billing.py) — always AFTER
apply_on_activation() so the invoice reflects the final, discount-adjusted
amount, never list price recomputed separately.

Contract, matching apply_on_activation()'s own house rule: record_invoice()
never raises and never blocks activation. A failure to issue an invoice is
logged and swallowed — a missing invoice is a billing-ops follow-up, not a
reason to fail a payment that already succeeded.

IMMUTABILITY: this module intentionally exposes no "update invoice" path.
Once issued, amount_subtotal/amount_discount/amount_tax/amount_total are
frozen. The only state transition after issuance is void_invoice().
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from app.extensions import db
from app.models.core import Invoice
from app.models.billing_center import InvoiceLine, InvoiceStatusEvent
from app.services.billing.plan_service import PlanService
from app.utils import get_plan_price, normalize_plan_name

import logging

logger = logging.getLogger(__name__)


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    else:
        # Existing integrations still pass legacy floats.  Decimal(str(...))
        # is the documented dual-write conversion rule; all persisted invoice
        # amounts are then rounded half-up exactly once.
        amount = Decimal(str(value if value is not None else 0))
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _to_rate(value) -> Decimal:
    amount = value if isinstance(value, Decimal) else Decimal(str(value if value is not None else 0))
    return amount.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def _next_invoice_number() -> str:
    """
    Sequential invoice numbers (INV-<year>-<6-digit seq>), e.g. INV-2026-000123.

    Postgres: atomic via invoice_number_seq (migration 0040) — safe under
    concurrent activations (webhook + manual resync racing the same moment).

    SQLite (dev/test only): no native sequence object. Falls back to
    max(id)+1, which is NOT safe under concurrent writers — matches this
    codebase's existing convention of gating Postgres-only correctness
    guarantees behind db.engine.dialect.name (see discount_repository's
    get_for_update). Do not rely on this fallback's atomicity in prod;
    prod is Postgres, so it never takes this branch there.
    """
    from datetime import datetime, timezone
    year = datetime.now(timezone.utc).year

    if db.engine.dialect.name != 'sqlite':
        seq = db.session.execute(db.text("SELECT nextval('invoice_number_seq')")).scalar()
    else:
        last_id = db.session.query(db.func.max(Invoice.id)).scalar() or 0
        seq = last_id + 1

    return f"INV-{year}-{seq:06d}"


def record_invoice(
    *,
    tenant_id: int,
    subscription,
    plan: str,
    billing_cycle: str = 'monthly',
    payment_method: str = '',
    payment_provider: str = '',
    payment_reference: Optional[str] = None,
    redemption=None,
    tax_rate: Decimal = Decimal('0'),
    amount_subtotal_override: Decimal | float | int | None = None,
    amount_discount_override: Decimal | float | int | None = None,
    amount_total_override: Decimal | float | int | None = None,
    currency_override: str | None = None,
    original_amount_minor: int | None = None,
    original_currency: str | None = None,
    currency_exponent: int | None = None,
    actor: str = 'billing-system',
    commit: bool = False,
) -> Optional[Invoice]:
    """
    Issue an invoice for a completed activation.

    `redemption` is the DiscountRedemption object returned by
    discount_checkout.apply_on_activation() — pass it through directly, do
    not recompute the discount here. None means no coupon was used; the
    invoice is issued at full list price.

    Idempotent per (subscription_id, payment_reference): a webhook retry
    or a superadmin resync hitting the same subscription+reference returns
    the existing invoice instead of double-issuing. Mirrors
    discount_service.redeem_discount()'s idempotency guard.

    Never raises. Returns None (and logs) on any failure rather than
    blocking the activation that already succeeded.
    """
    try:
        sub_id = getattr(subscription, 'id', None)

        if sub_id is not None and payment_reference:
            existing = (
                db.session.query(Invoice)
                .filter_by(subscription_id=sub_id, payment_reference=payment_reference)
                .first()
            )
            if existing is not None:
                logger.info(
                    'record_invoice: idempotent hit, reusing invoice=%s for subscription=%s ref=%s',
                    existing.invoice_number, sub_id, payment_reference,
                )
                return existing

        norm_plan = normalize_plan_name(plan)
        catalog = PlanService().snapshot(norm_plan, billing_cycle)
        amount_subtotal = _to_decimal(
            amount_subtotal_override
            if amount_subtotal_override is not None
            else get_plan_price(norm_plan, billing_cycle)
        )

        if amount_discount_override is not None:
            amount_discount = _to_decimal(amount_discount_override)
            coupon_code = getattr(getattr(redemption, 'campaign', None), 'code', None) if redemption is not None else None
            discount_redemption_id = getattr(redemption, 'id', None) if redemption is not None else None
        elif redemption is not None:
            amount_discount = _to_decimal(redemption.amount_discounted)
            coupon_code = getattr(getattr(redemption, 'campaign', None), 'code', None)
            discount_redemption_id = getattr(redemption, 'id', None)
        else:
            amount_discount = Decimal('0')
            coupon_code = None
            discount_redemption_id = None

        tax_rate = _to_rate(tax_rate)
        if tax_rate < 0 or tax_rate > 1:
            raise ValueError('tax rate must be between zero and one')
        taxable_base = amount_subtotal - amount_discount
        amount_tax = (taxable_base * tax_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        amount_total = (
            _to_decimal(amount_total_override)
            if amount_total_override is not None
            else _to_decimal(taxable_base + amount_tax)
        )
        if min(amount_subtotal, amount_discount, amount_tax, amount_total) < 0:
            raise ValueError('invoice money values cannot be negative')
        if amount_total != _to_decimal(amount_subtotal - amount_discount + amount_tax):
            raise ValueError('invoice total does not reconcile to subtotal - discount + tax')

        original_fields = (original_amount_minor, original_currency, currency_exponent)
        if any(value is not None for value in original_fields) and not all(value is not None for value in original_fields):
            raise ValueError('original amount, currency, and exponent must be supplied together')
        if original_amount_minor is not None:
            original_amount_minor = int(original_amount_minor)
            currency_exponent = int(currency_exponent)
            original_currency = str(original_currency).strip().upper()
            if original_amount_minor < 0 or not 0 <= currency_exponent <= 6 or len(original_currency) != 3:
                raise ValueError('invalid original money evidence')

        # A failed optional invoice must roll back only its savepoint. Calling
        # session.rollback() here would erase the payment activation and ledger
        # posting being assembled by the caller in the same outer transaction.
        with db.session.begin_nested():
            invoice = Invoice(
                invoice_number=_next_invoice_number(),
                tenant_id=tenant_id,
                subscription_id=sub_id,
                discount_redemption_id=discount_redemption_id,
                plan=norm_plan,
                plan_version=catalog.catalog_version,
                plan_snapshot=catalog.to_dict(),
                billing_cycle=billing_cycle,
                amount_subtotal=amount_subtotal,
                amount_discount=amount_discount,
                tax_rate=tax_rate,
                amount_tax=amount_tax,
                amount_total=amount_total,
                original_amount_minor=original_amount_minor,
                original_currency=original_currency,
                currency_exponent=currency_exponent,
                coupon_code=coupon_code,
                currency=(currency_override or catalog.currency).upper(),
                payment_method=payment_method or '',
                payment_provider=payment_provider or '',
                payment_reference=payment_reference,
                status='issued',
            )
            db.session.add(invoice)
            db.session.flush()
            db.session.add(InvoiceLine(
                invoice_id=invoice.id,
                position=1,
                line_type='subscription',
                description=f'{catalog.display_name} — {billing_cycle}',
                quantity=Decimal('1.000000'),
                unit_amount=amount_subtotal,
                amount=amount_subtotal,
                tax_metadata={
                    'rate': format(tax_rate, 'f'),
                    'amount': format(amount_tax, 'f'),
                },
                discount_metadata={
                    'amount': format(amount_discount, 'f'),
                    'coupon_code': coupon_code,
                    'redemption_id': discount_redemption_id,
                },
            ))
            db.session.add(InvoiceStatusEvent(
                invoice_id=invoice.id,
                from_status=None,
                to_status='issued',
                actor=str(actor or 'billing-system')[:120],
                reason='Successful payment activation recorded',
                idempotency_key=f'issue:{invoice.invoice_number}',
            ))
            db.session.flush()

        logger.info(
            'record_invoice: issued %s tenant=%s subscription=%s total=%s',
            invoice.invoice_number, tenant_id, sub_id, amount_total,
        )

        if commit:
            db.session.commit()

        return invoice

    except Exception:
        logger.exception(
            'record_invoice: failed for tenant=%s subscription=%s — activation proceeds regardless',
            tenant_id, getattr(subscription, 'id', None),
        )
        return None


def void_invoice(invoice: Invoice, *, reason: str, actor: str = '', idempotency_key: str | None = None,
                 commit: bool = True) -> Invoice:
    """
    The only permitted state change on an issued invoice. Does not alter
    any financial column — sets status/voided_at/void_reason only. Use
    this for corrections instead of UPDATEing amount_total etc. directly;
    issue a fresh invoice for the corrected amount if one is needed.
    """
    from datetime import datetime, timezone

    reason = str(reason or '').strip()
    actor = str(actor or '').strip()
    if not reason or not actor:
        raise ValueError('void reason and actor are required')
    key = idempotency_key or f'void:{invoice.id}:{reason}'
    existing = InvoiceStatusEvent.query.filter_by(invoice_id=invoice.id, idempotency_key=key).first()
    if existing is not None:
        return invoice
    if invoice.status == 'void':
        raise ValueError('invoice is already void')
    previous = invoice.status
    invoice.status = 'void'
    invoice.voided_at = datetime.now(timezone.utc)
    invoice.void_reason = reason
    db.session.add(invoice)
    db.session.add(InvoiceStatusEvent(
        invoice_id=invoice.id,
        from_status=previous,
        to_status='void',
        actor=actor[:120],
        reason=reason,
        idempotency_key=key,
    ))
    if commit:
        db.session.commit()
    return invoice
