"""Phase 1 design-system source guards runnable without Flask packages."""
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class Phase1DesignSystemTests(unittest.TestCase):
    def test_self_hosted_fonts_are_complete_and_licensed(self):
        css = read("app/static/css/design-system.css")
        expected = {
            "syne-latin-400-normal.woff2", "syne-latin-500-normal.woff2",
            "syne-latin-600-normal.woff2", "syne-latin-700-normal.woff2",
            "syne-latin-800-normal.woff2", "dm-sans-latin-300-normal.woff2",
            "dm-sans-latin-300-italic.woff2", "dm-sans-latin-400-normal.woff2",
            "dm-sans-latin-400-italic.woff2", "dm-sans-latin-500-normal.woff2",
            "dm-sans-latin-500-italic.woff2", "dm-sans-latin-600-normal.woff2",
            "dm-sans-latin-700-normal.woff2", "jetbrains-mono-latin-400-normal.woff2",
            "jetbrains-mono-latin-500-normal.woff2", "jetbrains-mono-latin-700-normal.woff2",
        }
        for filename in expected:
            asset = ROOT / "app/static/fonts" / filename
            self.assertTrue(asset.is_file(), filename)
            self.assertGreater(asset.stat().st_size, 1_000, filename)
            self.assertIn(f"../fonts/{filename}", css)
        for license_name in ("SYNE-OFL.txt", "DM-SANS-OFL.txt", "JETBRAINS-MONO-OFL.txt"):
            self.assertIn("SIL OPEN FONT LICENSE", read(f"app/static/fonts/licenses/{license_name}"))

    def test_canonical_tokens_cover_both_themes_and_behavior_states(self):
        css = read("app/static/css/design-system.css")
        dark, light = css.split('html[data-theme="light"]', 1)
        tokens = (
            "--color-canvas", "--color-surface-1", "--color-text-primary",
            "--color-text-secondary", "--color-text-disabled", "--color-brand",
            "--color-focus-ring", "--color-selected", "--color-disabled-bg",
            "--color-disabled-border", "--color-positive", "--color-warning",
            "--color-negative", "--color-info", "--color-chart-1",
            "--color-chart-5", "--color-provider-paymongo",
            "--color-provider-dodo", "--color-provider-manual",
        )
        for token in tokens:
            self.assertIn(token, dark, token)
            self.assertIn(token, light, token)
        self.assertIn("--font-display: 'Syne'", css)
        self.assertIn("--font-body:    'DM Sans'", css)
        self.assertIn("--font-mono:    'JetBrains Mono'", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn(":focus-visible", css)

    def test_platform_shells_load_design_system_exactly_once(self):
        shells = (
            "app/templates/base.html",
            "app/templates/admin/base.html",
            "app/templates/superadmin/base.html",
            "app/public/templates/public/_base.html",
        )
        for path in shells:
            source = read(path)
            self.assertEqual(source.count("css/design-system.css"), 1, path)
            self.assertIn("js/theme-bootstrap.js", source, path)
            self.assertIn("data-theme-storage=", source, path)

    def test_platform_templates_do_not_request_google_fonts(self):
        template_roots = (ROOT / "app/templates", ROOT / "app/public/templates")
        offenders = []
        for base in template_roots:
            for path in base.rglob("*.html"):
                if "fonts.googleapis.com" in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_legacy_typography_delegates_to_supported_families(self):
        for path in ("app/static/css/landing.css", "app/static/css/style.css"):
            source = read(path)
            self.assertIn("'Syne'", source, path)
            self.assertIn("'DM Sans'", source, path)
            for unsupported in ("'Space Grotesk'", "'Inter'", "'Oswald'"):
                self.assertNotIn(unsupported, source, path)
        public = read("app/static/css/public-design-system.css")
        self.assertIn("--ph-bg: var(--color-canvas)", public)
        self.assertNotIn('html[data-theme="light"]', public)
        main_root = read("app/static/css/main.css").split("}", 1)[0]
        self.assertNotRegex(main_root, r"#[0-9a-fA-F]{3,8}|rgba?\(")

    def test_theme_behavior_contract_is_loaded_by_every_supported_theme(self):
        themes = ("default", "developer_journal", "developer_pro", "blockform_brutal", "schematic_spec")
        contract = read("app/static/css/theme-contract.css")
        runtime = read("app/templates/partials/_theme_runtime.html")
        self.assertEqual(runtime.count("css/theme-contract.css"), 1)
        for theme in themes:
            source = read(f"themes/{theme}/templates/index.html")
            self.assertEqual(source.count("_theme_runtime.html"), 1, theme)
        for behavior in ("focus-visible", "--mph-touch-target", "prefers-reduced-motion", "forced-colors"):
            self.assertIn(behavior, contract)

    def test_reference_route_is_protected_and_synthetic(self):
        route = read("app/superadmin/routes/design_system.py")
        template = read("app/templates/superadmin/design_system_reference.html")
        self.assertIn("@superadmin_required", route)
        self.assertIn("from app.superadmin.routes import design_system", read("app/superadmin/routes/__init__.py"))
        self.assertNotRegex(template, re.compile(r"<script\b|\sstyle=", re.IGNORECASE))
        self.assertIn("Synthetic labels", template)
        for false_metric in ("MRR", "revenue", "customers", "conversion"):
            self.assertNotIn(false_metric, template)

    def test_token_lint_passes_pilot_platform_css(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/lint_design_tokens.py")],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_external_theme_scripts_have_no_inline_behavior_regression(self):
        public = read("app/public/templates/public/_base.html")
        for forbidden in ("localStorage.getItem('phPublicTheme')", "navLinks.classList.toggle"):
            self.assertNotIn(forbidden, public)
        self.assertIn("js/public-shell.js", public)
        bootstrap = read("app/static/js/theme-bootstrap.js")
        self.assertIn("candidate === 'light' || candidate === 'dark'", bootstrap)
        self.assertIn("prefers-color-scheme: light", bootstrap)


if __name__ == "__main__":
    unittest.main()
