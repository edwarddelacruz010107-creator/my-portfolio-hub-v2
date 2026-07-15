from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Phase10ReleaseHardeningTests(unittest.TestCase):
    def test_release_source_gate_passes(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "release_gate.py")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_proxy_headers_are_trusted_only_by_explicit_hop_count(self):
        source = (ROOT / "app" / "__init__.py").read_text()
        config = (ROOT / "config.py").read_text()
        self.assertIn("TRUSTED_PROXY_HOPS", source)
        self.assertIn("if trusted_proxy_hops:", source)
        self.assertIn("TRUSTED_PROXY_HOPS', '0'", config)

    def test_client_ip_uses_verified_remote_address_only(self):
        source = (ROOT / "app" / "request_security.py").read_text()
        self.assertIn("request.remote_addr", source)
        self.assertNotIn("X-Forwarded-For", source)
        self.assertNotIn("CF-Connecting-IP", source)

    def test_csp_nonce_covers_every_executable_and_style_block(self):
        for path in (ROOT / "app" / "templates").rglob("*.html"):
            text = path.read_text()
            for marker in ("<script", "<style"):
                for fragment in text.split(marker)[1:]:
                    self.assertIn("nonce=", fragment.split(">", 1)[0], str(path))

    def test_sensitive_uploads_cross_quarantine_scanner_boundary(self):
        proof = (ROOT / "app" / "services" / "billing" / "private_proof_storage.py").read_text()
        scanner = (ROOT / "app" / "services" / "media" / "malware_scan.py").read_text()
        self.assertIn("require_clean_sensitive_upload(data)", proof)
        self.assertNotIn("shell=True", scanner)
        self.assertIn("tempfile.mkstemp", scanner)
        self.assertIn("unlink(missing_ok=True)", scanner)

    def test_correlation_header_rejects_unbounded_input(self):
        source = (ROOT / "app" / "request_security.py").read_text()
        self.assertIn("{8,128}", source)
        self.assertIn("_REQUEST_ID_RE.fullmatch", source)

    def test_database_compose_has_no_default_password(self):
        compose = (ROOT / "docker-compose.prod.yml").read_text()
        self.assertNotIn("POSTGRES_PASSWORD:-postgres", compose)
        self.assertIn("POSTGRES_PASSWORD must be set", compose)

    def test_sbom_is_cyclonedx_and_has_components(self):
        path = ROOT / "release_evidence" / "sbom.cdx.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["bomFormat"], "CycloneDX")
        self.assertEqual(data["specVersion"], "1.5")
        self.assertGreaterEqual(len(data["components"]), 10)

    def test_query_observer_logs_fingerprints_not_parameters(self):
        source = (ROOT / "app" / "observability.py").read_text()
        self.assertIn("fingerprint", source)
        self.assertNotIn("statement=", source)
        self.assertIn("_parameters", source)
