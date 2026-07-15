#!/usr/bin/env python3
"""Audit and lock the intentionally retained ``innerHTML`` inventory.

The digest covers path plus normalized surrounding source, so adding a sink or
changing a value source fails the release gate until the change is reviewed.
Pinned third-party vendor code is intentionally outside this first-party scan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPROVED_INVENTORY_SHA256 = "025b25510d90c873696f03ea871afbe000893212171fef2404c4083676bd2415"


def _classification(path: str, context: str) -> str:
    if path == "app/templates/admin/project_form.html":
        return "server_sanitized_rich_text_capture"
    if "innerHTML = '';" in context:
        return "clear_only"
    if path == "app/static/js/admin.js" and "el.innerHTML = html" in context:
        return "bounded_static_skeleton_builder"
    if re.search(r"(?:const\s+\w+\s*=|dataset\.originalHtml\s*=).*innerHTML", context):
        return "same_element_snapshot"
    if re.search(r"innerHTML\s*=\s*(?:orig|original|originalHTML|button\.dataset\.originalHtml)", context):
        return "same_element_restore"
    return "static_literal_ui_markup"


def inventory() -> list[dict]:
    findings: list[dict] = []
    for source_root in (ROOT / "app" / "templates", ROOT / "app" / "static" / "js"):
        for path in sorted(source_root.rglob("*")):
            if not path.is_file() or path.suffix not in {".html", ".js"}:
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"\binnerHTML\b", source):
                line = source.count("\n", 0, match.start()) + 1
                context = re.sub(
                    r"\s+",
                    " ",
                    source[max(0, match.start() - 100): min(len(source), match.end() + 220)],
                ).strip()
                relative = path.relative_to(ROOT).as_posix()
                findings.append({
                    "path": relative,
                    "line": line,
                    "classification": _classification(relative, context),
                    "context": context,
                })
    return findings


def evaluate() -> dict:
    findings = inventory()
    digest_input = "\n".join(
        f"{finding['path']}|{finding['context']}" for finding in findings
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
    failures: list[str] = []
    if digest != APPROVED_INVENTORY_SHA256:
        failures.append(
            "dom_sink_inventory_changed: review every changed source and update the approved digest"
        )
    rich_text_findings = [
        finding for finding in findings
        if finding["classification"] == "server_sanitized_rich_text_capture"
    ]
    sanitizer_source = (ROOT / "app" / "admin" / "routes" / "projects_uploads.py").read_text()
    if rich_text_findings and "sanitize_rich_text(form.description.data)" not in sanitizer_source:
        failures.append("rich_text_sink_missing_server_sanitizer")
    return {
        "schema": "portfolio.dom-sink-audit.v1",
        "passed": not failures,
        "approved_inventory_sha256": APPROVED_INVENTORY_SHA256,
        "actual_inventory_sha256": digest,
        "first_party_sink_count": len(findings),
        "failures": failures,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
