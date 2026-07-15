from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_source_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProductionReleaseCandidateTests(unittest.TestCase):
    def test_empty_database_is_detected_by_required_table_inventory(self):
        guard = _load_source_module("schema_guard_under_test", "app/schema_guard.py")
        self.assertIn("tenants", guard._CORE_REQUIRED)
        self.assertIn("alembic_version", guard._CORE_REQUIRED)
        self.assertIn("profile", guard._TENANT_REQUIRED)
        self.assertIn("alembic_version_tenant", guard._TENANT_REQUIRED)
        source = (ROOT / "app" / "__init__.py").read_text()
        self.assertIn("schema_state = inspect_schema_state(db)", source)
        self.assertIn("raise RuntimeError(message)", source)
        self.assertIn('if app.testing:', source)
        self.assertIn('app.config["SCHEMA_READY"] = True', source)

    def test_local_setup_is_versioned_and_powershell_friendly(self):
        source = (ROOT / "app" / "__init__.py").read_text()
        script = (ROOT / "setup-local.ps1").read_text()
        self.assertIn("upgrade_core_database()", source)
        self.assertIn("upgrade_tenant_database()", source)
        self.assertIn("python -m flask --app run.py setup-local", script)
        self.assertIn("db-status", script)
        init_db_section = source[source.index("@app.cli.command('init-db')"):source.index("@app.cli.command('create-superadmin')")]
        self.assertNotIn("db.create_all", init_db_section)
        ensure_section = source[source.index("@app.cli.command('ensure-default-tenant')"):source.index("@app.cli.command('check-contact-config')")]
        self.assertNotIn("years_experience=5", ensure_section)
        self.assertNotIn("yourusername", ensure_section)
        self.assertIn("seo_indexable=False", ensure_section)

    def test_csp_hashes_rendered_legacy_attributes_without_unsafe_inline(self):
        csp = _load_source_module("csp_hardening_under_test", "app/csp_hardening.py")
        markup = '<button onclick="save(&amp;quot;x&amp;quot;)" style="color:red">Save</button>'
        hashes = csp.rendered_attribute_hashes(markup)
        self.assertIn(csp._hash_source('save(&amp;quot;x&amp;quot;)'), hashes["script-src-attr"])
        self.assertIn(csp._hash_source("color:red"), hashes["style-src-attr"])
        self.assertIn(csp._hash_source("width: inherit;height: inherit;"), hashes["style-src-attr"])
        init = (ROOT / "app" / "__init__.py").read_text()
        policy = re.search(r"csp\s*=\s*\{(.*?)\n\}", init, re.S).group(1)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("code.iconify.design", policy)
        self.assertNotIn("api.iconify.design", policy)

    def test_every_first_party_icon_is_bundled_locally(self):
        used: set[tuple[str, str]] = set()
        pattern = re.compile(r"\b(lucide|mdi|logos|flat-color-icons):([a-z0-9-]+)\b")
        for root in (ROOT / "app" / "templates", ROOT / "app" / "static" / "js"):
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in {".html", ".js"}:
                    used.update(pattern.findall(path.read_text(errors="ignore")))

        bundle = (ROOT / "app" / "static" / "vendor" / "iconify" / "portfolio-icon-collections-2026.07.js").read_text()
        raw = re.search(r"const collections = (\[.*\]);\n", bundle, re.S).group(1)
        collections = json.loads(raw)
        available: set[tuple[str, str]] = set()
        for collection in collections:
            prefix = collection["prefix"]
            available.update((prefix, name) for name in collection.get("icons", {}))
            available.update((prefix, name) for name in collection.get("aliases", {}))
        compatibility = json.loads(
            re.search(r"const compatibilityIcons = (\{.*?\});\n", bundle, re.S).group(1)
        )
        available.update(
            (compatibility["prefix"], name) for name in compatibility["icons"]
        )
        self.assertTrue(used)
        self.assertEqual(set(), used - available)

    def test_dom_sink_inventory_is_reviewed_and_locked(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "dom_sink_gate.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(46, result["first_party_sink_count"])
        self.assertEqual(result["approved_inventory_sha256"], result["actual_inventory_sha256"])

    def test_docker_runtime_supplies_required_malware_scanner(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        render = (ROOT / "render.yaml").read_text()
        config = (ROOT / "config.py").read_text()
        self.assertIn("clamav", dockerfile)
        self.assertNotIn("&& freshclam", dockerfile)
        self.assertIn("UpdateLogFile", dockerfile)
        self.assertIn("/var/log/clamav", dockerfile)
        self.assertIn("runtime: docker", render)
        self.assertIn("plan: standard", render)
        self.assertIn("clamscan --no-summary", render)
        self.assertIn("shutil.which(scanner_executable)", config)
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text()
        self.assertIn('freshclam --quiet --user=appuser --datadir="$signature_dir"', entrypoint)
        self.assertIn('web_runtime="true"', entrypoint)
        self.assertIn("clamscan --no-summary /app/docker-entrypoint.sh", entrypoint)
        scanner = (ROOT / "app" / "services" / "media" / "malware_scan.py").read_text()
        self.assertIn("scanner_signatures_stale", scanner)

    def test_docker_entrypoint_has_valid_posix_shell_syntax(self):
        completed = subprocess.run(
            ["sh", "-n", str(ROOT / "docker-entrypoint.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_production_blueprints_have_defense_in_depth_guards(self):
        admin = (ROOT / "app" / "admin" / "blueprint.py").read_text()
        superadmin = (ROOT / "app" / "superadmin" / "blueprint.py").read_text()
        self.assertIn("@admin.before_request", admin)
        self.assertIn("def block_public_admin", admin)
        self.assertIn("@superadmin.before_request", superadmin)
        self.assertIn("def block_public_superadmin", superadmin)

    def test_release_builder_excludes_runtime_and_customer_data(self):
        source = (ROOT / "tools" / "build_release_archive.py").read_text()
        dockerignore = (ROOT / ".dockerignore").read_text()
        self.assertIn('(\"app\", \"static\", \"uploads\")', source)
        self.assertIn('"node_modules"', source)
        self.assertIn('"__pycache__"', source)
        self.assertRegex(dockerignore, r"(?m)^app/static/uploads/$")


if __name__ == "__main__":
    unittest.main()
