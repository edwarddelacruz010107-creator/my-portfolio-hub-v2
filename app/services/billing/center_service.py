"""Ledger-backed read models for tenant and superadmin billing centers."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import String, cast, or_

from app.extensions import db
from app.models import (
    BillingAttempt,
    DiscountCampaign,
    Invoice,
    LedgerBackfillItem,
    PaymentSubmission,
    PaymentTransaction,
    Subscription,
    Tenant,
)
from app.services.billing.money import mask_reference, minor_to_decimal, safe_payment_failure_message
from app.services.billing.plan_service import PlanService
from app.services.ledger.analytics_service import build_ledger_analytics


MAX_ROWS = 100


def _limit(value) -> int:
    return min(max(int(value or 50), 1), MAX_ROWS)


def transaction_original_amount(transaction) -> Decimal:
    return minor_to_decimal(int(transaction.original_amount_minor), int(transaction.currency_exponent))


def transaction_view(transaction) -> dict:
    return {
        "record": transaction,
        "original_amount": transaction_original_amount(transaction),
        "original_currency": transaction.original_currency,
        "provider_reference": mask_reference(transaction.provider_transaction_id),
        "event_reference": mask_reference(transaction.provider_event_id),
        "reporting_amount": transaction.usd_reporting_amount,
        "reporting_available": transaction.usd_reporting_amount is not None,
    }


def attempt_view(attempt) -> dict:
    return {
        "record": attempt,
        "original_amount": minor_to_decimal(int(attempt.original_amount_minor), int(attempt.currency_exponent)),
    }


def tenant_billing_center(tenant_id: int, *, limit: int = 50) -> dict:
    row_limit = _limit(limit)
    subscription = (
        Subscription.query.filter_by(tenant_id=tenant_id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .first()
    )
    transactions = (
        PaymentTransaction.query.filter_by(tenant_id=tenant_id)
        .order_by(PaymentTransaction.recorded_at.desc(), PaymentTransaction.id.desc())
        .limit(row_limit).all()
    )
    invoices = (
        Invoice.query.filter_by(tenant_id=tenant_id)
        .order_by(Invoice.issued_at.desc(), Invoice.id.desc())
        .limit(row_limit).all()
    )
    attempts = (
        BillingAttempt.query.filter_by(tenant_id=tenant_id)
        .order_by(BillingAttempt.created_at.desc(), BillingAttempt.id.desc())
        .limit(row_limit).all()
    )
    entitlements = {}
    catalog_version = None
    if subscription:
        try:
            snapshot = PlanService().snapshot(subscription.plan, subscription.billing_cycle or "monthly")
            entitlements = snapshot.entitlements
            catalog_version = snapshot.catalog_version
        except ValueError:
            pass
    return {
        "subscription": subscription,
        "entitlements": entitlements,
        "catalog_version": catalog_version,
        "transactions": [transaction_view(item) for item in transactions],
        "invoices": invoices,
        "attempts": [attempt_view(item) for item in attempts],
        "failure_message": safe_payment_failure_message(getattr(subscription, "status", None)),
        "definition": "Posted settlements, refunds, reversals, adjustments, and chargebacks from the immutable ledger.",
    }


def superadmin_billing_center(*, search: str = "", provider: str = "", status: str = "", limit: int = 50) -> dict:
    row_limit = _limit(limit)
    search = str(search or "").strip()[:120]
    provider = str(provider or "").strip().lower()[:30]
    status = str(status or "").strip().lower()[:30]

    transaction_query = PaymentTransaction.query
    if provider:
        transaction_query = transaction_query.filter(PaymentTransaction.provider == provider)
    if status:
        transaction_query = transaction_query.filter(PaymentTransaction.status == status)
    if search:
        pattern = f"%{search}%"
        transaction_query = transaction_query.filter(or_(
            PaymentTransaction.provider_transaction_id.ilike(pattern),
            PaymentTransaction.provider_event_id.ilike(pattern),
            cast(PaymentTransaction.tenant_id, String).ilike(pattern),
        ))
    transactions = transaction_query.order_by(
        PaymentTransaction.recorded_at.desc(), PaymentTransaction.id.desc()
    ).limit(row_limit).all()

    subscription_query = Subscription.query.join(Tenant, Tenant.id == Subscription.tenant_id)
    invoice_query = Invoice.query
    if search:
        pattern = f"%{search}%"
        subscription_query = subscription_query.filter(or_(
            Tenant.slug.ilike(pattern), Subscription.plan.ilike(pattern),
        ))
        invoice_query = invoice_query.filter(or_(
            Invoice.invoice_number.ilike(pattern), Invoice.payment_reference.ilike(pattern),
        ))
    submission_query = PaymentSubmission.query.join(Tenant, Tenant.id == PaymentSubmission.tenant_id)
    campaign_query = DiscountCampaign.query
    attempt_query = BillingAttempt.query
    refund_query = PaymentTransaction.query.filter(
        PaymentTransaction.accounting_type.in_(("refund", "reversal", "chargeback"))
    )
    if provider:
        attempt_query = attempt_query.filter(BillingAttempt.provider == provider)
        refund_query = refund_query.filter(PaymentTransaction.provider == provider)
    if search:
        pattern = f"%{search}%"
        submission_query = submission_query.filter(or_(
            Tenant.slug.ilike(pattern), PaymentSubmission.payment_reference.ilike(pattern),
            PaymentSubmission.status.ilike(pattern),
        ))
        campaign_query = campaign_query.filter(or_(
            DiscountCampaign.name.ilike(pattern), DiscountCampaign.code.ilike(pattern),
        ))
        attempt_query = attempt_query.filter(or_(
            BillingAttempt.idempotency_key.ilike(pattern),
            BillingAttempt.provider_reference.ilike(pattern),
            cast(BillingAttempt.tenant_id, String).ilike(pattern),
        ))
        refund_query = refund_query.filter(or_(
            PaymentTransaction.provider_transaction_id.ilike(pattern),
            PaymentTransaction.provider_event_id.ilike(pattern),
        ))
    subscriptions = subscription_query.order_by(Subscription.created_at.desc()).limit(row_limit).all()
    invoices = invoice_query.order_by(Invoice.issued_at.desc()).limit(row_limit).all()
    submissions = submission_query.order_by(PaymentSubmission.submitted_at.desc()).limit(row_limit).all()
    campaigns = campaign_query.order_by(DiscountCampaign.created_at.desc()).limit(row_limit).all()
    attempts = attempt_query.order_by(BillingAttempt.created_at.desc()).limit(row_limit).all()
    refunds = refund_query.order_by(PaymentTransaction.recorded_at.desc()).limit(row_limit).all()
    review_transactions = PaymentTransaction.query.filter_by(status="review_required").count()
    unreconciled_backfills = LedgerBackfillItem.query.filter_by(disposition="unreconciled").count()
    missing_invoice_links = (
        Invoice.query.outerjoin(PaymentTransaction, PaymentTransaction.invoice_id == Invoice.id)
        .filter(PaymentTransaction.id.is_(None), Invoice.status == "issued").count()
    )
    failed_attempts = BillingAttempt.query.filter(BillingAttempt.status.in_(("failed", "dead"))).count()

    return {
        "metrics": build_ledger_analytics(months=6),
        "transactions": [transaction_view(item) for item in transactions],
        "subscriptions": subscriptions,
        "invoices": invoices,
        "submissions": submissions,
        "campaigns": campaigns,
        "attempts": attempts,
        "refunds": [transaction_view(item) for item in refunds],
        "reconciliation": {
            "review_transactions": review_transactions,
            "unreconciled_backfills": unreconciled_backfills,
            "missing_invoice_links": missing_invoice_links,
            "failed_attempts": failed_attempts,
            "total": review_transactions + unreconciled_backfills + missing_invoice_links + failed_attempts,
        },
        "filters": {"search": search, "provider": provider, "status": status, "limit": row_limit},
    }
