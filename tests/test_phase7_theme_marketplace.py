"""Phase 7 installed-theme marketplace contracts (stdlib-only)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
THEME_IDS = (
    "default", "developer_pro", "blockform_brutal",
    "schematic_spec", "developer_journal",
)


def _load_contract():
    path = ROOT / "app/services/themes/contract.py"
    spec = importlib.util.spec_from_file_location("phase7_theme_contract", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


contract = _load_contract()


def _fixture_cases():
    base = {
        "profile": {}, "projects": [], "skills": [], "services": [],
        "testimonials": [], "certificates": [], "experiences": [], "stats": {},
    }
    minimal = {**base, "profile": {"name": "Ada", "title": "Engineer"}}
    full = {
        **minimal,
        "projects": [{"title": "Launch", "description": "Measured delivery", "live_url": "https://example.test"}],
        "skills": [{"name": "Python", "level": 90}],
        "services": [{"name": "Product engineering", "description": "End-to-end delivery"}],
        "testimonials": [{"name": "Client", "message": "Dependable work"}],
        "certificates": [{"title": "Architecture", "issuer": "Example"}],
        "experiences": [{"role": "Lead engineer", "company": "Example"}],
        "stats": {"projects_count": 1},
    }
    hostile = {
        **base,
        "profile": {"name": '<script>alert("x")</script>', "bio": '" onmouseover="alert(1)'},
        "projects": [{"title": "<img src=x onerror=alert(1)>", "description": "javascript:alert(1)"}],
    }
    long_content = {
        **base,
        "profile": {"name": "Long-form owner", "bio": "a" * 100_000},
        "projects": [{"title": "Long case study", "description": "b" * 100_000}],
    }
    return {"empty": base, "minimal": minimal, "full": full, "hostile": hostile, "long": long_content}


class Phase7ThemeContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def manifests(self):
        for theme_id in THEME_IDS:
            theme_dir = ROOT / "themes" / theme_id
            yield theme_id, json.loads((theme_dir / "theme.json").read_text()), theme_dir

    def test_all_five_installed_manifests_validate(self):
        for theme_id, manifest, theme_dir in self.manifests():
            self.assertEqual(contract.validate_manifest(manifest, theme_dir, ROOT / "app/static"), [], theme_id)

    def test_existing_ids_and_contract_versions_are_stable(self):
        engine = self.read("app/theme_engine.py")
        for theme_id, manifest, _ in self.manifests():
            self.assertIn(f"'{theme_id}'", engine)
            self.assertEqual(manifest["compatibility"]["contract"], contract.CONTRACT_VERSION)
            self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_every_declared_asset_is_local_and_exists(self):
        for theme_id, manifest, _ in self.manifests():
            self.assertEqual(manifest["csp"]["remote_hosts"], [], theme_id)
            for asset in manifest["assets"]:
                self.assertFalse(asset["path"].startswith(("http://", "https://", "/")))
                self.assertTrue((ROOT / "app/static" / asset["path"]).is_file(), asset["path"])

    def test_templates_have_no_inline_executable_remote_assets_or_event_handlers(self):
        remote = re.compile(r"<(?:script|link)\b[^>]*(?:src|href)=[\"']https?://", re.I)
        inline_script = re.compile(r"<script\b(?![^>]*\bsrc=)([^>]*)>", re.I)
        for theme_id in THEME_IDS:
            source = self.read(f"themes/{theme_id}/templates/index.html")
            self.assertNotRegex(source, remote, theme_id)
            self.assertNotRegex(source, r"\son[a-z]+\s*=", theme_id)
            self.assertNotIn("| safe", source)
            self.assertNotIn("|safe", source)
            for match in inline_script.finditer(source):
                self.assertIn("application/json", match.group(1), theme_id)

    def test_all_themes_use_shared_runtime_customization_seo_and_legal_components(self):
        for theme_id in THEME_IDS:
            source = self.read(f"themes/{theme_id}/templates/index.html")
            for partial in (
                "_theme_runtime.html", "_theme_customization.html",
                "_portfolio_seo.html", "_theme_legal_links.html",
            ):
                self.assertIn(partial, source, f"{theme_id}: {partial}")

    def test_typed_tokens_accept_valid_values_and_reject_injection(self):
        schema = json.loads((ROOT / "themes/default/theme.json").read_text())["configurable_tokens"]
        clean = contract.sanitize_tokens({"accent": "#AABBCC"}, schema)
        self.assertEqual(clean, {"accent": "#aabbcc"})
        self.assertEqual(contract.render_customization_css(clean, schema), ":root{--accent:#aabbcc;}\n")
        for hostile in ("red;}", "url(javascript:alert(1))", "#fff", "expression(alert(1))"):
            with self.assertRaises(contract.ThemeContractError):
                contract.sanitize_tokens({"accent": hostile}, schema)
        with self.assertRaises(contract.ThemeContractError):
            contract.sanitize_tokens({"unknown": "#ffffff"}, schema)

    def test_empty_minimal_full_hostile_and_long_contracts_cover_every_theme(self):
        for theme_id in THEME_IDS:
            for case, payload in _fixture_cases().items():
                self.assertEqual(contract.validate_content_fixture(payload), [], f"{theme_id}/{case}")

    def test_malformed_content_shape_is_rejected(self):
        bad = {"profile": [], **{key: [] for key in contract.CONTENT_COLLECTIONS}}
        bad["projects"] = ["not-an-object"]
        errors = contract.validate_content_fixture(bad)
        self.assertIn("profile must be an object", errors)
        self.assertIn("projects entries must be objects", errors)

    def test_startup_fails_closed_on_contract_errors(self):
        engine = self.read("app/theme_engine.py")
        self.assertIn("validate_installed_themes", engine)
        self.assertIn("Installed theme contract validation failed", engine)
        self.assertLess(engine.index("validate_installed_themes"), engine.index("app.jinja_loader = self._build_loader(app)"))

    def test_preview_is_labeled_fixture_with_no_delivery_endpoint(self):
        data = self.read("app/public/services/theme_preview_data.py")
        routes = self.read("app/public/routes.py")
        self.assertIn("build_design_fixture_context", data)
        self.assertIn("Design fixture — not a customer portfolio", data)
        self.assertIn("contact_url=''", routes)
        self.assertIn("Design fixture preview", routes)
        self.assertNotIn("contact_url=url_for('public.contact')", routes)
        self.assertIn('live_url=""', data)
        self.assertIn('github_url=""', data)

    def test_selection_is_plan_validated_atomic_and_counts_only_changes(self):
        routes = self.read("app/admin/routes/profile_appearance.py")
        self.assertIn("engine.can_use_theme(profile, theme_id)", routes)
        self.assertIn("db.session.commit()", routes)
        self.assertIn("did not persist to the canonical profile row", routes)
        self.assertIn("if theme_id != previous_theme_id", routes)

    def test_customization_history_is_tenant_scoped_append_only_and_race_locked(self):
        service = self.read("app/services/themes/customization_service.py")
        model = self.read("app/models/theme_customization.py")
        migration = self.read("migrations/versions/0061_theme_customization_history.py")
        self.assertIn('down_revision = "0060"', migration)
        self.assertNotIn("INSERT INTO", migration.upper())
        self.assertIn("BEFORE UPDATE OR DELETE", migration)
        self.assertIn("before_update", model)
        self.assertIn("before_delete", model)
        self.assertIn("pg_advisory_xact_lock", service)
        self.assertGreaterEqual(service.count("tenant_id=int(tenant_id), theme_id=theme_id"), 5)

    def test_public_customization_css_requires_active_tenant_selected_theme(self):
        routes = self.read("app/public/routes.py")
        self.assertIn("Tenant.query.filter_by(slug=slug, status='active')", routes)
        self.assertIn("profile.selected_theme or 'default'", routes)
        self.assertIn("customization_css(tenant.id, requested_theme, draft=False)", routes)
        self.assertIn("X-Content-Type-Options", routes)

    def test_admin_customizer_is_authenticated_external_and_token_linted(self):
        routes = self.read("app/admin/routes/profile_appearance.py")
        picker = self.read("app/templates/admin/themes/index.html")
        customizer = self.read("app/templates/admin/themes/customize.html")
        css = self.read("app/static/css/theme-marketplace-v1.css")
        lint = self.read("tools/lint_design_tokens.py")
        self.assertGreaterEqual(routes.count("@admin_required"), 3)
        for source in (picker, customizer):
            self.assertNotIn("<style", source)
            self.assertNotIn(" style=", source)
        self.assertIn("theme-marketplace-v1.css", picker)
        self.assertIn("theme-marketplace-v1.css", customizer)
        self.assertIn("theme-marketplace-v1.css", lint)
        self.assertNotIn(":root", css)

    def test_no_fake_projects_picsum_tailwind_or_placeholder_resume(self):
        combined = "\n".join(
            self.read(f"themes/{theme_id}/templates/index.html")
            + self.read(f"app/static/themes/{theme_id}/theme.js")
            for theme_id in THEME_IDS
            if (ROOT / f"app/static/themes/{theme_id}/theme.js").exists()
        ).lower()
        self.assertNotIn("picsum", combined)
        self.assertNotIn("cdn.tailwind", combined)
        self.assertNotIn("placeholder resume", combined)
        self.assertNotIn("fake project", combined)

    def test_popularity_label_has_a_documented_minimum_threshold(self):
        source = self.read("app/superadmin/themes.py")
        self.assertIn("_POPULARITY_MIN_SELECTION_EVENTS = 25", source)
        self.assertIn(">= _POPULARITY_MIN_SELECTION_EVENTS", source)
        self.assertIn("selection-change events, not unique tenants", source)

    def test_unknown_theme_ids_are_rejected_before_filesystem_or_db_use(self):
        engine = self.read("app/theme_engine.py")
        route = self.read("app/admin/routes/profile_appearance.py")
        self.assertIn("SUPPORTED_THEME_ID_SET", engine)
        self.assertIn("is_supported_theme_id(theme_id)", route)
        self.assertIn("is_valid_theme_id(theme_id)", route)


if __name__ == "__main__":
    unittest.main()
