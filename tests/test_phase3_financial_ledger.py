"""Phase 3 financial-ledger contract tests (stdlib-only execution)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


domain = _load("phase3_ledger_domain", "app/services/ledger/domain.py")

# Load adapters without importing Flask's app package. The adapter has a single
# domain dependency, which is provided under its production import name.
for package in ("app", "app.services", "app.services.ledger"):
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = []
        sys.modules[package] = module
sys.modules["app.services.ledger.domain"] = domain
adapters = _load("phase3_ledger_adapters", "app/services/ledger/adapters.py")

UTC_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def posting(provider: str, event: str, amount_minor: int = 100, **overrides):
    values = {
        "tenant_id": 1,
        "provider": provider,
        "provider_event_id": event,
        "provider_transaction_id": f"txn-{event}",
        "event_type": "payment.succeeded",
        "accounting_type": "settlement",
        "original_amount_minor": amount_minor,
        "original_currency": "USD",
        "currency_exponent": 2,
        "occurred_at": UTC_NOW,
        "received_at": UTC_NOW,
        "provider_environment": "live",
    }
    values.update(overrides)
    return domain.LedgerPosting(**values)


class ExactMoneyTests(unittest.TestCase):
    def test_three_one_dollar_sources_equal_exactly_three_dollars(self):
        rows = [posting("dodo", "d1"), posting("paymongo", "p1"), posting("manual", "m1")]
        result = domain.aggregate_postings(rows)
        self.assertEqual(result.gross_usd, Decimal("3.00"))
        self.assertEqual(result.net_usd, Decimal("3.00"))
        self.assertEqual(result.provider_usd, {
            "dodo": Decimal("1.00"),
            "manual": Decimal("1.00"),
            "paymongo": Decimal("1.00"),
        })

    def test_one_hundred_replays_do_not_change_total(self):
        deliveries = [posting("paymongo", "evt-replay") for _ in range(100)]
        unique = {row.idempotency_key: row for row in deliveries}
        self.assertEqual(len(unique), 1)
        self.assertEqual(domain.aggregate_postings(unique.values()).net_usd, Decimal("1.00"))

    def test_partial_refund_and_reversal_are_negative_linked_facts(self):
        original = posting("dodo", "settled", amount_minor=1000)
        refund = posting(
            "dodo", "partial-refund", amount_minor=-250,
            event_type="refund.succeeded", accounting_type="refund",
            reversal_of_id="original-id",
        )
        reversal = posting(
            "dodo", "reversal", amount_minor=-1000,
            event_type="ledger.reversal", accounting_type="reversal",
            reversal_of_id="original-id",
        )
        result = domain.aggregate_postings([original, refund, reversal])
        self.assertEqual(result.gross_usd, Decimal("10.00"))
        self.assertEqual(result.net_usd, Decimal("-2.50"))

    def test_interval_uses_provider_occurrence_not_delivery_order(self):
        late = posting(
            "manual", "late",
            occurred_at=UTC_NOW - timedelta(days=40),
            received_at=UTC_NOW,
        )
        recent = posting("manual", "recent", occurred_at=UTC_NOW - timedelta(days=2))
        result = domain.aggregate_postings(
            [recent, late], start=UTC_NOW - timedelta(days=30), end=UTC_NOW
        )
        self.assertEqual(result.net_usd, Decimal("1.00"))

    def test_non_usd_requires_reproducible_fx_or_review(self):
        review = posting(
            "paymongo", "php-review", original_currency="PHP",
            original_amount_minor=5800,
        )
        self.assertEqual(review.status, "review_required")
        self.assertIsNone(review.usd_reporting_amount)
        posted = posting(
            "paymongo", "php-posted", original_currency="PHP",
            original_amount_minor=5800,
            usd_reporting_amount=Decimal("1.00"),
            fx_rate=Decimal("58"),
            fx_rate_source="checkout_snapshot",
            fx_effective_at=UTC_NOW,
        )
        self.assertEqual(posted.status, "posted")
        self.assertEqual(domain.aggregate_postings([review, posted]).net_usd, Decimal("1.00"))

    def test_yearly_mrr_and_money_property_examples_are_exact(self):
        self.assertEqual(domain.monthly_recurring_amount(Decimal("120"), "yearly"), Decimal("10"))
        for exponent in range(7):
            for minor in (1, 7, 99, 101, 999999):
                value = domain.major_from_minor(minor, exponent)
                self.assertIsInstance(value, Decimal)
                self.assertEqual(value.scaleb(exponent), Decimal(minor))

    def test_binary_float_money_and_unsafe_metadata_are_rejected(self):
        with self.assertRaises(TypeError):
            posting("manual", "float", usd_reporting_amount=1.25)
        row = posting(
            "manual", "metadata",
            safe_metadata={"source_id": "42", "card_number": "4111111111111111", "secret": "x"},
        )
        self.assertEqual(row.safe_metadata, {"source_id": "42"})


class AdapterTests(unittest.TestCase):
    def test_paymongo_normalizes_provider_minor_units(self):
        payload = {
            "data": {
                "id": "evt-paymongo",
                "attributes": {
                    "type": "payment.paid",
                    "data": {
                        "id": "pay-1",
                        "attributes": {
                            "amount": 100,
                            "currency": "USD",
                            "paid_at": int(UTC_NOW.timestamp()),
                            "metadata": {"billing_cycle": "monthly", "plan_name": "pro"},
                        },
                    },
                },
            }
        }
        row = adapters.PayMongoAdapter().normalize(
            payload, tenant_id=1, subscription_id=2,
            received_at=UTC_NOW, environment="live",
        )
        self.assertEqual(row.provider_transaction_id, "pay-1")
        self.assertEqual(row.usd_reporting_amount, Decimal("1.00"))
        self.assertNotIn("card_number", row.safe_metadata)

    def test_dodo_refund_keeps_refund_and_original_payment_ids(self):
        payload = {
            "event_id": "evt-refund",
            "type": "refund.succeeded",
            "data": {
                "refund_id": "rf-1", "payment_id": "pay-1", "amount": 25,
                "currency": "USD", "created_at": UTC_NOW.isoformat(),
            },
        }
        row = adapters.DodoAdapter().normalize(
            payload, tenant_id=1, subscription_id=2,
            received_at=UTC_NOW, environment="live",
        )
        self.assertEqual(row.accounting_type, "refund")
        self.assertEqual(row.provider_transaction_id, "rf-1")
        self.assertEqual(row.source_provider_transaction_id, "pay-1")
        self.assertEqual(row.usd_reporting_amount, Decimal("-0.25"))


class SourceContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_database_is_final_idempotency_and_immutability_boundary(self):
        model = self.read("app/models/ledger.py")
        migration = self.read("migrations/versions/0057_add_append_only_payment_ledger.py")
        self.assertIn("uq_payment_transactions_provider_event_type", model)
        self.assertIn("uq_payment_transactions_provider_transaction_type", model)
        self.assertIn("before_update", model)
        self.assertIn("BEFORE UPDATE OR DELETE", migration)
        self.assertIn("reject_financial_ledger_mutation", migration)

    def test_all_three_producers_post_through_canonical_service(self):
        webhooks = self.read("app/webhooks/__init__.py")
        manual = self.read("app/services/billing/manual_billing.py")
        self.assertGreaterEqual(webhooks.count("post_provider_event("), 3)
        self.assertIn("post_manual_submission(", manual)
        self.assertIn("event_id_override=event_id", webhooks)

    def test_financial_displays_do_not_read_legacy_payment_floats(self):
        facade = self.read("app/services/analytics/dashboard_analytics_service.py")
        dashboard = self.read("app/superadmin/routes/core_auth.py")
        for forbidden in ("Subscription.amount_paid", "PaymentSubmission.amount_usd", "_plan_amount("):
            self.assertNotIn(forbidden, facade)
            self.assertNotIn(forbidden, dashboard)
        self.assertIn("build_ledger_analytics", facade)

    def test_live_mode_cache_review_and_backfill_contracts_are_explicit(self):
        analytics = self.read("app/services/ledger/analytics_service.py")
        posting_service = self.read("app/services/ledger/posting_service.py")
        backfill = self.read("app/services/ledger/backfill_service.py")
        self.assertIn('provider_environment == "live"', analytics)
        self.assertIn("LEDGER_CACHE_GENERATION_KEY", analytics)
        self.assertIn("financial_posting_review_required", posting_service)
        self.assertIn('disposition="unreconciled"', backfill)
        self.assertNotIn("/ 100.0", self.read("app/services/ledger/domain.py"))

    def test_state_ordering_reset_guard_and_definition_version_are_wired(self):
        webhook = self.read("app/webhooks/__init__.py")
        reset = self.read("app/services/billing/financial_reset_service.py")
        definitions = self.read("app/services/ledger/definitions.py")
        self.assertIn("provider_state_occurred_at", webhook)
        self.assertIn("out-of-order state event ignored", webhook)
        self.assertIn("Financial reset refused", reset)
        self.assertIn('DEFINITION_VERSION = "finance-v1.0.0"', definitions)


if __name__ == "__main__":
    unittest.main()
