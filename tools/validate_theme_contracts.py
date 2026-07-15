#!/usr/bin/env python3
"""Build-time validator for the five installed portfolio themes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "app/services/themes/contract.py"
spec = importlib.util.spec_from_file_location("theme_contract_standalone", path)
contract = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(contract)


def main() -> int:
    supported = ("default", "developer_pro", "blockform_brutal", "schematic_spec", "developer_journal")
    failures = contract.validate_installed_themes(ROOT / "themes", ROOT / "app/static", supported)
    if failures:
        for theme_id, errors in failures.items():
            for error in errors:
                print(f"{theme_id}: {error}", file=sys.stderr)
        return 1
    print(f"Theme contract validation passed for {len(supported)} installed themes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
