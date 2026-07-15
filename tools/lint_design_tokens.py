#!/usr/bin/env python3
"""Fail when new platform CSS bypasses the canonical token contract."""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = tuple(
    ROOT / path
    for path in (
        "app/static/css/design-system-reference.css",
        "app/static/css/components-v1.css",
        "app/static/css/auth-security.css",
        "app/static/css/component-system-reference.css",
        "app/static/css/billing-center-v1.css",
        "app/static/css/portfolio-intelligence-v1.css",
        "app/static/css/theme-marketplace-v1.css",
        "app/static/css/ai-center-v1.css",
        "app/static/css/founder-dashboard-v1.css",
    )
)
COLOR_LITERAL = re.compile(r"(?:#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\()")
DECLARATION = re.compile(r"^\s*([\w-]+)\s*:\s*([^;]+)", re.MULTILINE)
COLOR_PROPERTIES = {
    "color", "background", "background-color", "border", "border-color",
    "border-top", "border-right", "border-bottom", "border-left", "outline",
    "outline-color", "box-shadow", "fill", "stroke", "text-shadow",
}
SPACING_PROPERTIES = {
    "gap", "row-gap", "column-gap", "margin", "margin-top", "margin-right",
    "margin-bottom", "margin-left", "padding", "padding-top", "padding-right",
    "padding-bottom", "padding-left",
}


def lint(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    if re.search(r"(^|})\s*:root\b", text):
        failures.append("introduces a competing :root token layer")

    for match in DECLARATION.finditer(text):
        prop, value = match.groups()
        line = text.count("\n", 0, match.start()) + 1
        if prop in COLOR_PROPERTIES and COLOR_LITERAL.search(value):
            failures.append(f"line {line}: {prop} uses a raw color")
        if prop == "font-family" and "var(" not in value and value.strip() not in {"inherit"}:
            failures.append(f"line {line}: font-family bypasses a typography token")
        if prop in SPACING_PROPERTIES and re.search(r"\b\d+(?:\.\d+)?(?:px|rem|em)\b", value):
            failures.append(f"line {line}: {prop} uses raw spacing")
    return failures


def main(argv: list[str]) -> int:
    paths = tuple(Path(item).resolve() for item in argv) if argv else DEFAULT_PATHS
    errors = [(path, lint(path)) for path in paths]
    errors = [(path, failures) for path, failures in errors if failures]
    if errors:
        for path, failures in errors:
            for failure in failures:
                print(f"{path.relative_to(ROOT)}: {failure}", file=sys.stderr)
        return 1
    print(f"Design-token lint passed for {len(paths)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
