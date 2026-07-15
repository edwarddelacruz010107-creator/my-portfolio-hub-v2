#!/usr/bin/env python3
"""Generate a deterministic CycloneDX inventory from pinned lock files."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release_evidence" / "sbom.cdx.json"


def component(name: str, version: str, ecosystem: str) -> dict:
    return {
        "type": "library",
        "name": name,
        "version": version,
        "purl": f"pkg:{ecosystem}/{name}@{version}",
    }


components: list[dict] = []
for raw in (ROOT / "requirements.lock.txt").read_text().splitlines():
    match = re.match(r"^([A-Za-z0-9_.-]+)==([^;\s]+)", raw.strip())
    if match:
        components.append(component(match.group(1).lower(), match.group(2), "pypi"))

lock = json.loads((ROOT / "package-lock.json").read_text())
for path, metadata in sorted(lock.get("packages", {}).items()):
    if path.startswith("node_modules/") and metadata.get("version"):
        components.append(component(path.removeprefix("node_modules/"), metadata["version"], "npm"))

components.sort(key=lambda item: item["purl"])
serial_material = "\n".join(item["purl"] for item in components).encode()
document = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "serialNumber": "urn:uuid:" + hashlib.sha256(serial_material).hexdigest()[:32],
    "version": 1,
    "metadata": {"component": {"type": "application", "name": "portfolio-hub"}},
    "components": components,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
print(f"wrote {OUT} ({len(components)} components)")

