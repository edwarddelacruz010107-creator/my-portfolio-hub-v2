"""Phase 2 component-system source guards runnable without Flask/Jinja."""
from pathlib import Path
import json
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class Phase2ComponentSystemTests(unittest.TestCase):
    def test_required_component_contract_is_declared(self):
        macros = read("app/templates/components/ui.html")
        expected = {
            "button", "icon_button", "input", "textarea", "select", "choice",
            "switch", "search_input", "currency_input", "file_upload",
            "password_input", "otp_input", "field_errors", "alert", "toast",
            "inline_status", "progress", "skeleton", "empty_state", "card",
            "stat_card", "data_table", "responsive_list", "tabs", "accordion",
            "pagination", "dialog", "confirmation_dialog", "drawer", "dropdown",
            "command_palette", "topbar", "sidebar", "breadcrumbs", "account_menu",
            "notification_bell", "mobile_nav_toggle", "badge", "provider_mark",
            "money", "timestamp", "trend_value", "chart_shell",
        }
        declared = set(re.findall(r"{% macro\s+([a-zA-Z0-9_]+)\(", macros))
        self.assertEqual(expected - declared, set())

    def test_macro_contract_does_not_bypass_escaping_or_csp(self):
        macros = read("app/templates/components/ui.html")
        for forbidden in ("|safe", "Markup(", "<script", " style=", "onclick=", "onchange=", "oninput="):
            self.assertNotIn(forbidden, macros)
        self.assertIn('aria-invalid="true"', macros)
        self.assertIn('aria-describedby=', macros)
        self.assertIn("<dialog", macros)
        self.assertIn("aria-labelledby", macros)

    def test_external_controller_uses_safe_dom_and_accessible_state(self):
        script = read("app/static/js/components-v1.js")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "document.write"):
            self.assertNotIn(forbidden, script)
        for required in (
            "textContent =", "allowedVariants", "showModal", "returnFocus",
            "event.key === 'Escape'", "event.key !== 'Tab'", "ArrowDown",
            "ArrowRight", "aria-expanded", "aria-selected", "aria-checked",
        ):
            self.assertIn(required, script)

    def test_platform_bases_load_component_assets_once(self):
        for path in (
            "app/templates/base.html", "app/templates/admin/base.html",
            "app/templates/superadmin/base.html", "app/public/templates/public/_base.html",
        ):
            source = read(path)
            self.assertEqual(source.count("css/components-v1.css"), 1, path)
            self.assertEqual(source.count("js/components-v1.js"), 1, path)

    def test_auth_pilot_preserves_contract_without_inline_implementation(self):
        twofa = read("app/templates/auth/2fa_verify.html")
        oauth = read("app/templates/auth/oauth_account_setup.html")
        for source, path in ((twofa, "2FA"), (oauth, "OAuth setup")):
            self.assertNotRegex(source, r"<script(?:\s[^>]*)?>\s*[^<{\s]", path)
            self.assertNotRegex(source, r"\son(?:click|change|input|submit|keydown)=", path)
            self.assertNotIn(" style=", source, path)
            self.assertIn("components/ui.html", source, path)
            self.assertIn("js/components-v1.js", source, path)
        self.assertIn("url_for('auth.verify_2fa')", twofa)
        self.assertIn("'code'", twofa)
        self.assertIn("'backup_code'", twofa)
        self.assertIn("form.hidden_tag()", twofa)

    def test_shell_flash_and_confirmation_use_shared_macros(self):
        for path in ("app/templates/admin/base.html", "app/templates/superadmin/base.html"):
            source = read(path)
            self.assertIn("ui.toast(", source, path)
            self.assertIn("ui.confirmation_dialog(", source, path)
        admin = read("app/templates/admin/base.html")
        self.assertNotIn("refreshNotifications =", admin)
        self.assertIn("[data-notification-bell]", read("app/static/js/dashboard-shell.js"))

    def test_component_gallery_is_protected_synthetic_and_inline_free(self):
        route = read("app/superadmin/routes/design_system.py")
        gallery = read("app/templates/superadmin/component_system_reference.html")
        self.assertIn("@superadmin.route('/component-system')", route)
        protected_segment = route.split("def component_system_reference", 1)[0].rsplit("@superadmin.route", 1)[1]
        self.assertIn("@superadmin_required", protected_segment)
        self.assertIn("components/ui.html", gallery)
        self.assertNotRegex(gallery, re.compile(r"<script\b|\sstyle=|\son\w+=", re.IGNORECASE))
        self.assertIn("Synthetic values", gallery)
        for fake_fact in ("MRR", "ARR", "$12", "99.9%", "customers"):
            self.assertNotIn(fake_fact, gallery)

    def test_token_lint_covers_all_new_component_css(self):
        linter = read("tools/lint_design_tokens.py")
        for stylesheet in (
            "components-v1.css", "auth-security.css", "component-system-reference.css",
        ):
            self.assertIn(stylesheet, linter)
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/lint_design_tokens.py")],
            cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_js_test_dependency_is_pinned_and_locked(self):
        package = json.loads(read("package.json"))
        self.assertRegex(package["devDependencies"]["jsdom"], r"^\d+\.\d+\.\d+$")
        lock = json.loads(read("package-lock.json"))
        self.assertEqual(lock["packages"][""]["devDependencies"]["jsdom"], package["devDependencies"]["jsdom"])

    def test_deprecation_registry_and_usage_contract_are_documented(self):
        docs = read("UI_COMPONENT_SYSTEM.md") + read("COMPONENT_DEPRECATIONS.md")
        for required in ("zero", "rollback", "malicious", "focus return", "CSRF", "fixed-scale"):
            self.assertIn(required, docs)


if __name__ == "__main__":
    unittest.main()
