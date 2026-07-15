"""Approved, versioned platform financial and subscription definitions."""
from __future__ import annotations


DEFINITION_VERSION = "finance-v1.0.0"
REPORTING_TIME_ZONE = "UTC"

METRIC_DEFINITIONS = {
    "cash_revenue_gross_usd": {
        "version": DEFINITION_VERSION,
        "definition": "Sum of posted positive settlement and adjustment USD reporting amounts by occurred_at.",
    },
    "cash_revenue_net_usd": {
        "version": DEFINITION_VERSION,
        "definition": "Sum of all posted settlement, refund, reversal, chargeback, and adjustment USD reporting amounts.",
    },
    "mrr_usd": {
        "version": DEFINITION_VERSION,
        "definition": "Latest posted recurring settlement per currently active non-trial subscription; yearly amount divided by 12.",
    },
    "arr_usd": {
        "version": DEFINITION_VERSION,
        "definition": "MRR multiplied by 12; it is recurring run rate, not cash revenue.",
    },
    "active_subscription": {
        "version": DEFINITION_VERSION,
        "definition": "A non-administrator subscription in active status whose start is not future and expiry is not past at the UTC observation time.",
    },
    "trial": {
        "version": DEFINITION_VERSION,
        "definition": "A tenant in explicit trial state before its stored UTC trial end; trials are excluded from paid MRR.",
    },
    "churn_rate": {
        "version": DEFINITION_VERSION,
        "definition": "Subscriptions cancelled or expired during the interval divided by active subscriptions at interval start; unavailable when the denominator is unknown.",
    },
    "manual_approval": {
        "version": DEFINITION_VERSION,
        "definition": "One approved payment submission may create at most one settlement posting keyed by its submission ID.",
    },
    "refund": {
        "version": DEFINITION_VERSION,
        "definition": "A negative posted row linked to the original settlement; the original row is never edited.",
    },
}
