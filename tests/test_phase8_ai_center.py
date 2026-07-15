"""Phase 8 deterministic contracts; no network or provider credentials used."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def load_domain():
    path = ROOT / "app/services/ai/domain.py"
    spec = importlib.util.spec_from_file_location("phase8_ai_domain", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DOMAIN = load_domain()


class Phase8DomainTests(unittest.TestCase):
    def test_provider_registry_is_complete_and_versioned(self):
        self.assertEqual(
            set(DOMAIN.PROVIDER_CATALOG),
            {"openai", "anthropic", "gemini", "groq", "openrouter", "ollama", "azure_openai"},
        )
        self.assertIn("2026.07", DOMAIN.CONTROL_PLANE_VERSION)
        self.assertIn("microunits", DOMAIN.PRICING_SNAPSHOT_VERSION)

    def test_cost_math_uses_exact_integer_microunits(self):
        self.assertEqual(DOMAIN.calculate_cost_microunits(1_000_000, 0, 5_000_000, 0), 5_000_000)
        self.assertEqual(DOMAIN.calculate_cost_microunits(1, 0, 1, 0), 1)
        with self.assertRaises(DOMAIN.AIContractError):
            DOMAIN.calculate_cost_microunits(-1, 0, 1, 1)

    def test_request_is_typed_normalized_and_bounded(self):
        request = DOMAIN.AIRequest(
            operation=" TEXT ", feature_key="Portfolio.Summary", input_text="hello", max_output_units=20
        )
        self.assertEqual(request.operation, "text")
        self.assertEqual(request.feature_key, "portfolio.summary")
        with self.assertRaises(DOMAIN.AIContractError):
            DOMAIN.AIRequest(operation="tool", feature_key="x.y", input_text="hello", max_output_units=20)

    def test_endpoint_allowlist_rejects_ssrf_and_credentials(self):
        self.assertEqual(
            DOMAIN.validate_provider_endpoint("openai", "https://api.openai.com/v1"),
            "https://api.openai.com/v1",
        )
        for bad in (
            "http://169.254.169.254/latest/meta-data",
            "https://key@example.com/v1",
            "https://api.openai.com/v1?token=secret",
        ):
            with self.assertRaises(DOMAIN.AIContractError):
                DOMAIN.validate_provider_endpoint("openai", bad)

    def test_ollama_and_azure_have_narrow_endpoint_rules(self):
        self.assertEqual(
            DOMAIN.validate_provider_endpoint("ollama", "http://localhost:11434/v1"),
            "http://localhost:11434/v1",
        )
        self.assertEqual(
            DOMAIN.validate_provider_endpoint(
                "azure_openai", "https://portfolio.openai.azure.com/openai/v1"
            ),
            "https://portfolio.openai.azure.com/openai/v1",
        )
        with self.assertRaises(DOMAIN.AIContractError):
            DOMAIN.validate_provider_endpoint("ollama", "http://10.0.0.5:11434/v1")

    def test_secret_redaction_is_recursive(self):
        redacted = DOMAIN.redact_sensitive({
            "api_key": "sk-this-is-a-secret-value",
            "nested": ["Bearer abcdefghijklmnop", {"password": "secret"}],
        })
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["nested"][0], "Bearer [REDACTED]")
        self.assertEqual(redacted["nested"][1]["password"], "[REDACTED]")

    def test_prompt_variables_are_exact_and_injection_resistant(self):
        self.assertEqual(DOMAIN.prompt_variables("Summarize {portfolio_text}"), ("portfolio_text",))
        self.assertEqual(
            DOMAIN.render_prompt("Hello {name}", {"name": "Ada"}, ["name"]), "Hello Ada"
        )
        for unsafe in ("{user.name}", "{name!r}", "{name:>20}"):
            with self.assertRaises(DOMAIN.AIContractError):
                DOMAIN.prompt_variables(unsafe)

    def test_structured_schema_rejects_external_references(self):
        with self.assertRaises(DOMAIN.AIContractError):
            DOMAIN.validate_json_schema({"type": "object", "$ref": "https://example.test/schema"})


class Phase8AdapterFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_app = types.ModuleType("app")
        fake_services = types.ModuleType("app.services")
        fake_ai = types.ModuleType("app.services.ai")
        cls.previous = {name: sys.modules.get(name) for name in (
            "app", "app.services", "app.services.ai", "app.services.ai.domain", "phase8_ai_adapters"
        )}
        sys.modules["app"] = fake_app
        sys.modules["app.services"] = fake_services
        sys.modules["app.services.ai"] = fake_ai
        sys.modules["app.services.ai.domain"] = DOMAIN
        path = ROOT / "app/services/ai/adapters.py"
        spec = importlib.util.spec_from_file_location("phase8_ai_adapters", path)
        cls.adapters = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.adapters
        spec.loader.exec_module(cls.adapters)

    @classmethod
    def tearDownClass(cls):
        for name, previous in cls.previous.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def fixture(self, name: str):
        return json.loads(read(f"tests/fixtures/ai/{name}.json"))

    def request(self):
        return DOMAIN.AIRequest(
            operation="text", feature_key="portfolio.summary", input_text="fixture", max_output_units=20
        )

    def test_openai_responses_fixture(self):
        adapter = self.adapters.OpenAIResponsesAdapter(
            provider_code="openai", base_url="https://api.openai.com/v1", api_key="test", timeout_seconds=1
        )
        with patch.object(self.adapters, "_post_json", return_value=(self.fixture("openai_responses_success"), {})):
            result = adapter.execute("gpt-fixture", self.request())
        self.assertEqual((result.text, result.input_units, result.output_units), ("Recorded OpenAI response", 12, 4))
        self.assertTrue(result.usage_complete)

    def test_openai_chat_fixture(self):
        adapter = self.adapters.OpenAIChatAdapter(
            provider_code="groq", base_url="https://api.groq.com/openai/v1", api_key="test", timeout_seconds=1
        )
        with patch.object(self.adapters, "_post_json", return_value=(self.fixture("openai_chat_success"), {})):
            result = adapter.execute("fixture", self.request())
        self.assertEqual(result.text, "Recorded chat response")
        self.assertTrue(result.usage_complete)

    def test_anthropic_fixture(self):
        adapter = self.adapters.AnthropicMessagesAdapter(
            provider_code="anthropic", base_url="https://api.anthropic.com", api_key="test", timeout_seconds=1
        )
        with patch.object(self.adapters, "_post_json", return_value=(self.fixture("anthropic_messages_success"), {})):
            result = adapter.execute("fixture", self.request())
        self.assertEqual(result.provider_request_id, "msg_fixture_01")
        self.assertTrue(result.usage_complete)

    def test_gemini_fixture(self):
        adapter = self.adapters.GeminiGenerateAdapter(
            provider_code="gemini", base_url="https://generativelanguage.googleapis.com/v1beta", api_key="test", timeout_seconds=1
        )
        with patch.object(self.adapters, "_post_json", return_value=(self.fixture("gemini_generate_success"), {})):
            result = adapter.execute("fixture", self.request())
        self.assertEqual((result.input_units, result.output_units), (9, 6))
        self.assertTrue(result.usage_complete)


class Phase8ArchitectureTests(unittest.TestCase):
    def test_schema_is_append_only_encrypted_and_honest_about_unknown_usage(self):
        model = read("app/models/ai_center.py")
        migration = read("migrations/versions/0062_ai_control_plane.py")
        self.assertIn('db.Column("credential_ciphertext"', model)
        self.assertIn('db.Column("request_ciphertext"', model)
        self.assertIn('db.Column("response_ciphertext"', model)
        self.assertNotIn('sa.Column("api_key"', migration)
        self.assertIn("reject_ai_append_only_mutation", migration)
        self.assertIn('sa.Column("input_units", sa.Integer(), nullable=True)', migration)
        self.assertIn('sa.Column("cost_microunits", sa.BigInteger(), nullable=True)', migration)
        self.assertNotIn("op.bulk_insert", migration)

    def test_one_service_boundary_owns_policy_budget_and_usage(self):
        service = read("app/services/ai/ai_service.py")
        adapters = read("app/services/ai/adapters.py")
        self.assertIn("class AIService", service)
        self.assertIn("pg_advisory_xact_lock", service)
        self.assertIn("idempotency_key", service)
        self.assertIn("reserved_cost_microunits", service)
        for forbidden in ("AIFeaturePolicy", "AIUsageRequest", "db.session", "calculate_cost_microunits"):
            self.assertNotIn(forbidden, adapters)

    def test_uncertain_provider_outcomes_are_not_automatically_replayed(self):
        adapters = read("app/services/ai/adapters.py")
        service = read("app/services/ai/ai_service.py")
        self.assertIn("retryable=exc.code == 429", adapters)
        self.assertIn("retryable=False", adapters)
        self.assertIn("uncertain provider outcome", service)
        self.assertIn("MAX_ATTEMPTS = 3", service)

    def test_routes_are_superadmin_only_and_rate_limit_live_test(self):
        route = read("app/superadmin/routes/ai_center.py")
        self.assertEqual(route.count("@superadmin_required"), 7)
        self.assertIn('@limiter.limit("5 per minute")', route)
        self.assertIn('billing_ack', route)
        self.assertNotIn("AIProviderConfig.query", route)

    def test_admin_receives_masked_view_models_only(self):
        center = read("app/services/ai/center_service.py")
        template = read("app/templates/superadmin/ai_center.html")
        self.assertIn('"masked_credential"', center)
        self.assertNotIn("_credential_ciphertext", template)
        self.assertNotIn("api_key }}", template)
        self.assertIn("Leave blank to keep it", template)

    def test_ui_is_external_accessible_and_never_claims_mock_output(self):
        template = read("app/templates/superadmin/ai_center.html")
        lint = read("tools/lint_design_tokens.py")
        self.assertNotIn("<style", template)
        self.assertNotIn(" style=", template)
        self.assertNotIn("<script", template)
        self.assertIn("ai-center-v1.css", template)
        self.assertIn("ai-center-v1.css", lint)
        self.assertIn("No mock response is substituted", template)
        self.assertIn("Billing warning", template)

    def test_knowledge_base_is_explicitly_unavailable_without_fake_schema(self):
        template = read("app/templates/superadmin/ai_center.html")
        migration = read("migrations/versions/0062_ai_control_plane.py").lower()
        self.assertIn("Unavailable by design", template)
        self.assertNotIn("knowledge_base", migration)
        self.assertNotIn("embedding_chunk", migration)

    def test_worker_and_retention_commands_are_registered(self):
        app = read("app/__init__.py")
        command = read("app/commands/ai.py")
        self.assertIn("register_ai_commands", app)
        self.assertIn('"ai-run-jobs"', command)
        self.assertIn('"ai-purge-payloads"', command)

    def test_pricing_snapshot_and_request_evidence_are_immutable(self):
        service = read("app/services/ai/ai_service.py")
        self.assertIn("model_pricing_version", service)
        self.assertIn("provider_request_hash", service)
        self.assertIn("cost_microunits=actual_cost", service)
        self.assertIn("if result.usage_complete else None", service)


if __name__ == "__main__":
    unittest.main()
