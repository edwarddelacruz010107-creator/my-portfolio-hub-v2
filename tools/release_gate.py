#!/usr/bin/env python3
"""Deterministic release source gate; emits machine-readable evidence."""

from __future__ import annotations

import argparse
import json
import re
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

    failures: list[str] = []
    warnings: list[str] = []
    raw_forwarded = [
        str(path.relative_to(ROOT)) for path in py_files
        if re.search(r"headers\.get\([\"'](?:X-Forwarded-For|CF-Connecting-IP)", path.read_text())
    ]
    if raw_forwarded:
        failures.append("raw_forwarding_header_consumers:" + ",".join(raw_forwarded))
    script_sources = re.search(r'"script-src"\s*:\s*\[(.*?)\]', init, re.S).group(1)
    style_sources = re.search(r'"style-src"\s*:\s*\[(.*?)\]', init, re.S).group(1)
    if "unsafe-inline" in script_sources:
        failures.append("script_src_allows_inline_blocks")
    if "unsafe-inline" in style_sources:
        failures.append("style_src_allows_inline_blocks")
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
    if re.search(r"(?:eval\s*\(|new\s+Function\s*\(|document\.write\s*\()", "\n".join(p.read_text(errors="ignore") for p in js_files)):
        failures.append("dangerous_javascript_execution_sink")

    handlers = _count(r"\son(?:click|change|input|submit|load|error|keyup|keydown|blur|focus)\s*=", templates)
    styles = _count(r"\sstyle\s*=", templates)
    inner_html = _count(r"\binnerHTML\b", js_files + templates)
    if handlers:
        warnings.append(f"legacy_inline_handlers={handlers}; owner=frontend; due=2026-08-15")
    if styles:
        warnings.append(f"legacy_style_attributes={styles}; owner=frontend; due=2026-08-15")
    if inner_html:
        warnings.append(f"html_rendering_sinks={inner_html}; audited_registry_required")

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
