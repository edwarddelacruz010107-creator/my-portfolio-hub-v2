"""Phase 9 deterministic founder-dashboard contracts without a Flask runtime."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_domain():
    path = ROOT / "app/services/founder/domain.py"
    spec = importlib.util.spec_from_file_location("phase9_founder_domain", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DOMAIN = load_domain()


class Phase9DomainTests(unittest.TestCase):
    def test_filters_are_allowlisted_and_periods_are_utc(self):
        filters = DOMAIN.FounderFilters.from_mapping({
            "range": "90",
            "compare": "previous",
            "payment_provider": "paymongo",
            "ai_provider": "anthropic",
            "plan": "basic",
        })
        self.assertEqual(filters.days, 90)
        self.assertEqual(filters.plan, "starter")
        periods = filters.periods(as_of=datetime(2026, 7, 15, 12, tzinfo=timezone.utc))
        self.assertEqual((periods["end_at"] - periods["start_at"]).days, 90)
        self.assertEqual((periods["comparison_end_at"] - periods["comparison_start_at"]).days, 90)
        self.assertEqual(periods["start_at"].tzinfo, timezone.utc)

    def test_unknown_filters_fail_closed_to_supported_defaults(self):
        filters = DOMAIN.FounderFilters.from_mapping({
            "range": "999", "compare": "future", "payment_provider": "evil",
            "ai_provider": "unknown", "plan": "root",
        })
        self.assertEqual(
            (filters.days, filters.comparison, filters.payment_provider, filters.ai_provider, filters.plan),
            (30, "previous", "all", "all", "all"),
        )

    def test_privacy_threshold_suppresses_small_rates(self):
        hidden = DOMAIN.safe_rate(1, 4)
        visible = DOMAIN.safe_rate(1, 5)
        self.assertFalse(hidden["available"])
        self.assertIsNone(hidden["value"])
        self.assertTrue(visible["available"])
        self.assertEqual(str(visible["value"]), "20.0")

    def test_comparison_does_not_invent_zero_baselines(self):
        self.assertFalse(DOMAIN.comparison_change(10, 0)["available"])
        self.assertEqual(str(DOMAIN.comparison_change(15, 10)["percent"]), "50.0")


class Phase9ArchitectureTests(unittest.TestCase):
    def test_route_delegates_to_one_assembler_without_domain_queries(self):
        route = read("app/superadmin/routes/core_auth.py")
        section = route[route.index("def dashboard():"):route.index("@superadmin.route('/founder/reauth")]
        self.assertIn("build_founder_dashboard(filters=filters)", section)
        self.assertNotIn(".query", section)
        self.assertNotIn("db.session", section)
        self.assertNotIn("build_superadmin_analytics", section)
        self.assertNotIn("heartbeat_state", section)

    def test_specific_capability_and_strong_reauth_guard_export(self):
        access = read("app/services/founder/access_control.py")
        route = read("app/superadmin/routes/core_auth.py")
        self.assertIn('FOUNDER_DASHBOARD_CAPABILITY = "platform.founder_dashboard.read"', access)
        self.assertIn('FOUNDER_EXPORT_CAPABILITY = "platform.founder_dashboard.export"', access)
        self.assertIn("STRONG_REAUTH_MAX_AGE_SECONDS = 600", access)
        self.assertIn("verify_password(password)", route)
        self.assertIn("verify_totp(totp_code)", route)
        self.assertIn("has_recent_strong_reauth", route)
        self.assertIn("methods=['POST']", route[route.index("/founder/export.csv"):])

    def test_export_is_bounded_aggregate_audited_and_no_store(self):
        route = read("app/superadmin/routes/core_auth.py")
        export = route[route.index("def founder_export():"):route.index("@superadmin.route('/dashboard')")]
        self.assertIn("Aggregate founder dashboard CSV export", export)
        self.assertIn("ActivityLog", export)
        self.assertIn("private, no-store", export)
        self.assertNotIn("query.all()", export)
        self.assertNotIn("tenant.email", export)
        self.assertNotIn("profile.email", export)

    def test_lifecycle_uses_versioned_occurrence_events_and_honest_coverage(self):
        source = read("app/services/founder/lifecycle_read_model.py")
        self.assertIn("LIFECYCLE_DEFINITION_VERSION", source)
        self.assertIn("SubscriptionStatusEvent.occurred_at", source)
        self.assertIn("row_number().over", source)
        self.assertIn("Legacy active subscriptions lack versioned activation evidence", source)
        self.assertIn("PRIVACY_THRESHOLD", read("app/services/founder/domain.py"))

    def test_finance_remains_owned_by_exact_ledger_domain(self):
        assembler = read("app/services/founder/dashboard_service.py")
        ledger = read("app/services/ledger/analytics_service.py")
        self.assertIn("build_founder_financial_read_model", assembler)
        self.assertIn("PaymentTransaction.occurred_at >= start_at", ledger)
        self.assertIn("Decimal", ledger)
        self.assertNotIn("amount_paid", assembler)
        self.assertNotIn("monthly_rate", assembler)

    def test_portfolio_and_system_gaps_are_unavailable_not_zero(self):
        portfolio = read("app/services/founder/portfolio_read_model.py")
        operations = read("app/services/founder/operations_read_model.py")
        template = read("app/templates/superadmin/dashboard.html")
        self.assertIn("stored only as cumulative counters", portfolio)
        self.assertIn("No versioned service-engagement event source exists", portfolio)
        self.assertIn('"cpu": {"available": False', operations)
        self.assertIn('"memory": {"available": False', operations)
        self.assertIn('"disk": {"available": False', operations)
        self.assertIn("Interval project engagement is unavailable", template)

    def test_ai_usage_preserves_unknown_cost_evidence(self):
        source = read("app/services/founder/ai_read_model.py")
        template = read("app/templates/superadmin/dashboard.html")
        self.assertIn("cost_microunits.is_(None)", source)
        self.assertIn("known_cost_microunits", source)
        self.assertIn("Cost unavailable", template)

    def test_cache_uses_source_watermark_and_late_recorded_timestamps(self):
        source = read("app/services/founder/dashboard_service.py")
        self.assertIn("_source_watermark", source)
        self.assertIn("PaymentTransaction.recorded_at", source)
        self.assertIn("SubscriptionStatusEvent.created_at", source)
        self.assertIn("AIUsageRequest.created_at", source)
        self.assertIn("timeout=CACHE_SECONDS", source)
        self.assertIn("ASSEMBLY_LATENCY_BUDGET_MS", source)

    def test_incident_audit_lists_are_bounded_and_link_to_owners(self):
        source = read("app/services/founder/operations_read_model.py")
        template = read("app/templates/superadmin/dashboard.html")
        self.assertIn('"incidents": incidents[:12]', source)
        self.assertIn('"audits": audits[:15]', source)
        for endpoint in ("superadmin.ai_center", "superadmin.billing_overview", "superadmin.logs"):
            self.assertIn(endpoint, source)
        self.assertNotIn("<form method=\"post\" action=\"{{ url_for('superadmin.ai_", template)

    def test_core_and_tenant_index_migrations_are_linear_and_data_free(self):
        core = read("migrations/versions/0063_founder_dashboard_indexes.py")
        tenant = read("migrations/tenant/versions/0002_founder_dashboard_indexes.py")
        self.assertIn('down_revision = "0062"', core)
        self.assertIn('down_revision = "0001_tenant_schema_baseline"', tenant)
        self.assertIn("ix_subscription_status_tenant_occurred", core)
        self.assertIn("ix_projects_tenant_status_updated", tenant)
        self.assertNotIn("bulk_insert", core + tenant)
        self.assertNotIn("op.execute", core + tenant)

    def test_dashboard_and_reauth_are_external_inline_free_and_token_linted(self):
        dashboard = read("app/templates/superadmin/dashboard.html")
        reauth = read("app/templates/superadmin/founder_reauth.html")
        lint = read("tools/lint_design_tokens.py")
        for template in (dashboard, reauth):
            self.assertNotIn("<style", template)
            self.assertNotIn("<script", template)
            self.assertNotIn("onclick=", template)
            self.assertNotIn(" style=", template)
            self.assertIn("founder-dashboard-v1.css", template)
        self.assertIn("founder-dashboard-v1.css", lint)


if __name__ == "__main__":
    unittest.main()
