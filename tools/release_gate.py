#!/usr/bin/env python3
"""Deterministic release source gate; emits machine-readable evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"


def _count(pattern: str, files: list[Path]) -> int:
    compiled = re.compile(pattern, re.I)
    return sum(len(compiled.findall(path.read_text(errors="ignore"))) for path in files)


def evaluate() -> dict:
    py_files = list((ROOT / "app").rglob("*.py"))
    templates = list(TEMPLATES.rglob("*.html"))
    js_files = list((ROOT / "app" / "static" / "js").rglob("*.js"))
    init = (ROOT / "app" / "__init__.py").read_text()
    compose = (ROOT / "docker-compose.prod.yml").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    entrypoint = (ROOT / "docker-entrypoint.sh").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()

    failures: list[str] = []
    warnings: list[str] = []
    raw_forwarded = [
        str(path.relative_to(ROOT)) for path in py_files
        if re.search(r"headers\.get\([\"'](?:X-Forwarded-For|CF-Connecting-IP)", path.read_text())
    ]
    if raw_forwarded:
        failures.append("raw_forwarding_header_consumers:" + ",".join(raw_forwarded))
    csp_source = re.search(r"csp\s*=\s*\{(.*?)\n\}", init, re.S).group(1)
    if "unsafe-inline" in csp_source:
        failures.append("csp_allows_unsafe_inline")
    missing_nonce = []
    for path in templates:
        for tag in re.findall(r"<(?:script|style)\b[^>]*>", path.read_text(), re.I):
            if "nonce=" not in tag:
                missing_nonce.append(str(path.relative_to(ROOT)))
                break
    if missing_nonce:
        failures.append("templates_missing_nonce:" + ",".join(sorted(missing_nonce)))
    if "POSTGRES_PASSWORD:-postgres" in compose:
        failures.append("compose_default_database_password")
    for path, markers in (
        (ROOT / "app" / "admin" / "blueprint.py", ("@admin.before_request", "def block_public_admin")),
        (ROOT / "app" / "superadmin" / "blueprint.py", ("@superadmin.before_request", "def block_public_superadmin")),
    ):
        source = path.read_text()
        if any(marker not in source for marker in markers):
            failures.append(f"missing_blueprint_auth_guard:{path.relative_to(ROOT)}")
    if "inspect_schema_state(db)" not in init or "raise RuntimeError(message)" not in init:
        failures.append("missing_production_schema_startup_guard")
    if "clamav" not in dockerfile or "freshclam --quiet" not in entrypoint:
        failures.append("production_image_missing_malware_scanner")
    if "&& freshclam" in dockerfile:
        failures.append("memory_heavy_signature_refresh_during_image_build")
    if not re.search(r"^app/static/uploads/$", dockerignore, re.M):
        failures.append("docker_image_does_not_exclude_runtime_uploads")
    if re.search(r"(?:eval\s*\(|new\s+Function\s*\(|document\.write\s*\()", "\n".join(p.read_text(errors="ignore") for p in js_files)):
        failures.append("dangerous_javascript_execution_sink")
    create_all_lines = [
        line.strip() for line in init.splitlines()
        if re.match(r"\s*db\.create_all\(", line)
    ]
    if len(create_all_lines) != 2 or "if app.testing:" not in init:
        failures.append("unversioned_schema_creation_outside_test_bootstrap")
    remote_assets = []
    for path in templates:
        for match in re.finditer(r'<(?:script|link)\b[^>]+(?:src|href)=["\']https?://', path.read_text(), re.I):
            remote_assets.append(str(path.relative_to(ROOT)))
    if remote_assets:
        failures.append("remote_executable_or_stylesheet_assets:" + ",".join(sorted(set(remote_assets))))
    for vendor_name in (
        "iconify-icon-1.0.7.min.js",
        "portfolio-icon-collections-2026.07.js",
    ):
        if not (ROOT / "app" / "static" / "vendor" / "iconify" / vendor_name).is_file():
            failures.append(f"missing_pinned_icon_asset:{vendor_name}")

    sys.path.insert(0, str(ROOT / "tools"))
    from dom_sink_gate import evaluate as evaluate_dom_sinks
    dom_audit = evaluate_dom_sinks()
    failures.extend(f"dom_audit:{failure}" for failure in dom_audit["failures"])

    handlers = _count(r"\son(?:click|change|input|submit|load|error|keyup|keydown|blur|focus)\s*=", templates)
    styles = _count(r"\sstyle\s*=", templates)
    inner_html = _count(r"\binnerHTML\b", js_files + templates)
    if handlers:
        warnings.append(f"legacy_inline_handlers={handlers}; owner=frontend; due=2026-08-15")
    if styles:
        warnings.append(f"legacy_style_attributes={styles}; owner=frontend; due=2026-08-15")
    if inner_html:
        warnings.append(f"html_rendering_sinks={inner_html}; locked_by_dom_sink_audit")

    return {
        "schema": "portfolio.release-gate.v1",
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "metrics": {
            "templates": len(templates),
            "legacy_inline_handlers": handlers,
            "legacy_style_attributes": styles,
            "html_rendering_sinks": inner_html,
            "dom_sink_inventory_sha256": dom_audit["actual_inventory_sha256"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
