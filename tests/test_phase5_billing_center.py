"""Phase 5 billing-center contracts (stdlib-only, no Flask/database runtime)."""
from __future__ import annotations

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


money = _load("phase5_money", "app/services/billing/money.py")


class Phase5MoneyTests(unittest.TestCase):
    def test_minor_units_are_exact_and_round_half_up(self):
        self.assertEqual(money.decimal_to_minor(Decimal("10.005"), 2), 1001)
        self.assertEqual(money.minor_to_decimal(1001, 2), Decimal("10.01"))
        self.assertEqual(money.legacy_float_to_minor(10.005, 2), 1001)

    def test_binary_float_requires_explicit_legacy_path(self):
        with self.assertRaises(money.MoneyError):
            money.decimal_to_minor(10.005, 2)
        with self.assertRaises(money.MoneyError):
            money.Money(100, "US", 2)

    def test_reference_masking_never_returns_full_reference(self):
        raw = "provider_payment_123456789"
        masked = money.mask_reference(raw)
        self.assertNotEqual(masked, raw)
        self.assertTrue(masked.startswith("prov"))
        self.assertTrue(masked.endswith("6789"))


class Phase5SourceContracts(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_expand_migration_follows_phase4_and_never_guesses_currency(self):
        source = self.read("migrations/versions/0059_billing_center.py")
        self.assertIn('down_revision = "0058"', source)
        for table in (
            "billing_plan_versions", "invoice_lines", "invoice_status_events",
            "subscription_status_events", "billing_attempts", "financial_float_backups",
        ):
            self.assertIn(f'"{table}"', source)
        self.assertIn("does not guess currencies", source)
        self.assertIn("trg_invoices_protect_financials", source)

    def test_catalog_and_invoice_capture_historical_sold_plan(self):
        catalog = self.read("app/services/billing/plan_service.py")
        invoice = self.read("app/services/billing/invoice_service.py")
        self.assertIn("class CatalogSnapshot", catalog)
        self.assertIn("catalog_version", catalog)
        self.assertIn("provider_mappings", catalog)
        self.assertIn("effective_from", catalog)
        self.assertIn("plan_snapshot=catalog.to_dict()", invoice)
        self.assertIn("InvoiceLine(", invoice)
        self.assertIn("InvoiceStatusEvent(", invoice)

    def test_lifecycle_is_explicit_provider_adapted_and_idempotent(self):
        service = self.read("app/services/billing/lifecycle_service.py")
        self.assertIn("ALLOWED_TRANSITIONS", service)
        self.assertIn("PROVIDER_STATE_MAP", service)
        self.assertIn("def adapt_provider_state", service)
        self.assertIn("def transition_subscription", service)
        self.assertIn("idempotency_key=idempotency_key", service)
        webhook = self.read("app/webhooks/__init__.py")
        self.assertIn("transition_subscription(", webhook)
        self.assertIn("actor='dodo-webhook'", webhook)
        self.assertIn("actor='paymongo-webhook'", webhook)

    def test_coupon_and_manual_review_last_use_races_are_locked(self):
        discount_repo = self.read("app/repositories/discount_repository.py")
        discount = self.read("app/services/billing/discount_service.py")
        manual = self.read("app/services/billing/manual_billing.py")
        routes = self.read("app/superadmin/routes/billing.py")
        self.assertIn("def get_for_update", discount_repo)
        self.assertIn("with_for_update()", discount_repo)
        self.assertIn("with db.session.begin_nested()", discount)
        self.assertIn("except IntegrityError", discount)
        self.assertGreaterEqual(manual.count("with_for_update()"), 2)
        self.assertIn("A review reason is required", routes)

    def test_dunning_never_reuses_a_provider_charge_without_one_key(self):
        service = self.read("app/services/billing/dunning_service.py")
        model = self.read("app/models/billing_center.py")
        self.assertIn("uq_billing_attempt_idempotency", model)
        self.assertIn("filter_by(idempotency_key=idempotency_key)", service)
        self.assertIn("MAX_ATTEMPTS", service)
        self.assertIn("BACKOFF_DAYS", service)

    def test_tenant_download_is_server_generated_and_tenant_scoped(self):
        routes = self.read("app/admin/routes/billing.py")
        template = self.read("app/templates/admin/billing_center.html")
        self.assertIn("Invoice.query.filter_by(id=invoice_id, tenant_id=profile.tenant_id)", routes)
        self.assertIn("render_invoice_pdf(invoice)", routes)
        self.assertIn("private, no-store", routes)
        self.assertNotIn("|safe", template)
        self.assertNotIn("payment_reference", template)

    def test_superadmin_center_uses_ledger_and_masks_provider_references(self):
        service = self.read("app/services/billing/center_service.py")
        template = self.read("app/templates/superadmin/billing_transaction_detail.html")
        self.assertIn("build_ledger_analytics", service)
        self.assertIn("mask_reference(transaction.provider_transaction_id)", service)
        self.assertIn("original_currency", template)
        self.assertNotIn("tx.provider_transaction_id", template)
        self.assertNotIn("tx.provider_event_id", template)

    def test_provider_identity_and_original_currency_stay_separate(self):
        webhook = self.read("app/webhooks/__init__.py")
        ledger = self.read("app/models/ledger.py")
        for provider in ("dodo", "paymongo", "manual"):
            self.assertIn(provider, ledger)
            self.assertIn(provider, webhook if provider != "manual" else self.read("app/services/ledger/posting_service.py"))
        self.assertIn("original_amount_minor", ledger)
        self.assertIn("original_currency", ledger)
        self.assertIn("provider_original", self.read("app/services/ledger/analytics_service.py"))

    def test_legacy_billing_is_behind_read_only_rollback_flag(self):
        config = self.read("config.py")
        admin = self.read("app/admin/routes/billing.py")
        superadmin = self.read("app/superadmin/routes/billing.py")
        self.assertIn("BILLING_CENTER_ENABLED", config)
        self.assertIn("BILLING_LEGACY_READ_ONLY", config)
        for source in (admin, superadmin):
            self.assertIn("billing_legacy_overview", source)
            self.assertIn("legacy-read-only", source)

    def test_receipt_has_no_client_computed_totals(self):
        receipt = self.read("app/services/billing/receipt_service.py")
        self.assertIn("invoice.amount_subtotal", receipt)
        self.assertIn("invoice.amount_discount", receipt)
        self.assertIn("invoice.amount_tax", receipt)
        self.assertIn("invoice.amount_total", receipt)
        self.assertNotIn("eval(", receipt)
        self.assertNotIn("innerHTML", receipt)

    def test_new_billing_surfaces_use_shared_tokens_without_inline_css(self):
        css = self.read("app/static/css/billing-center-v1.css")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}|rgba?\(")
        self.assertIn("var(--font-display)", css)
        for path in (
            "app/templates/admin/billing_center.html",
            "app/templates/superadmin/billing_center.html",
            "app/templates/superadmin/billing_transaction_detail.html",
        ):
            source = self.read(path)
            self.assertNotIn(" style=", source)
            self.assertIn("billing-center-v1.css", source)


if __name__ == "__main__":
    unittest.main()
