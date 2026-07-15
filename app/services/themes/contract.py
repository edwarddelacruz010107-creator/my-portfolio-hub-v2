"""Versioned filesystem theme contract and typed token sanitizer."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping


CONTRACT_VERSION = "theme-contract-1.0"
MANIFEST_SCHEMA_VERSION = "1.0.0"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
THEME_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CSS_VARIABLE = re.compile(r"^--[a-z][a-z0-9-]{1,62}$")
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
LENGTH = re.compile(r"^(-?\d+(?:\.\d+)?)(px|rem|em|%)$")
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc\s*=)([^>]*)>", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
REMOTE_ASSET = re.compile(r"<(?:script|link)\b[^>]*(?:src|href)\s*=\s*[\"']https?://", re.IGNORECASE)

REQUIRED_SECTIONS = frozenset({
    "profile", "projects", "project_links", "skills", "services",
    "testimonials", "certificates", "experience", "contact", "reactions",
    "seo", "analytics_hooks", "mobile_navigation", "consent_privacy", "empty_states",
})
REQUIRED_KEYS = frozenset({
    "id", "name", "version", "manifest_schema_version", "template_entry_points",
    "assets", "supported_sections", "configurable_tokens", "csp",
    "screenshot_provenance", "compatibility", "migration_notes",
})
CONTENT_COLLECTIONS = (
    "projects", "skills", "services", "testimonials", "certificates", "experiences",
)


class ThemeContractError(ValueError):
    pass


def validate_content_fixture(payload: Mapping[str, Any]) -> list[str]:
    """Validate the stable content shape used by every installed theme.

    User-authored strings are deliberately allowed, including hostile-looking
    and very long values: output encoding belongs to the autoescaped template
    boundary. This validator rejects shape drift, not legitimate content.
    """
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return ["content fixture must be an object"]
    if not isinstance(payload.get("profile"), Mapping):
        errors.append("profile must be an object")
    for key in CONTENT_COLLECTIONS:
        value = payload.get(key)
        if not isinstance(value, (list, tuple)):
            errors.append(f"{key} must be a list")
            continue
        if any(not isinstance(item, Mapping) for item in value):
            errors.append(f"{key} entries must be objects")
    if "stats" in payload and not isinstance(payload.get("stats"), Mapping):
        errors.append("stats must be an object")
    return errors


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThemeContractError(f"invalid manifest: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ThemeContractError(f"manifest must be an object: {path}")
    return payload


def _safe_relative(value: Any) -> str | None:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith(("/", ".")) or ".." in raw.split("/"):
        return None
    return raw


def validate_manifest(manifest: Mapping[str, Any], theme_dir: Path, static_root: Path) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_KEYS - set(manifest)
    if missing:
        errors.append("missing keys: " + ", ".join(sorted(missing)))
    expected_id = theme_dir.name
    theme_id = str(manifest.get("id") or "")
    if theme_id != expected_id or not THEME_ID.fullmatch(theme_id):
        errors.append("id must match the installed directory")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest_schema_version must be {MANIFEST_SCHEMA_VERSION}")
    if not SEMVER.fullmatch(str(manifest.get("version") or "")):
        errors.append("version must be semantic x.y.z")

    entries = manifest.get("template_entry_points")
    portfolio_entry = entries.get("portfolio") if isinstance(entries, Mapping) else None
    safe_entry = _safe_relative(portfolio_entry)
    entry_path = theme_dir / "templates" / safe_entry if safe_entry else None
    if entry_path is None or not entry_path.is_file():
        errors.append("portfolio template entry point is missing or unsafe")
    else:
        source = entry_path.read_text(encoding="utf-8")
        for match in INLINE_SCRIPT.finditer(source):
            attrs = match.group(1).lower()
            if 'type="application/json"' not in attrs and "type='application/json'" not in attrs:
                errors.append("portfolio template contains inline executable script")
                break
        if INLINE_HANDLER.search(source):
            errors.append("portfolio template contains an inline event handler")
        if REMOTE_ASSET.search(source):
            errors.append("portfolio template loads a remote script or stylesheet")

    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        errors.append("assets must be a non-empty list")
    else:
        for index, asset in enumerate(assets):
            path = _safe_relative(asset.get("path") if isinstance(asset, Mapping) else None)
            if not path or not (static_root / path).is_file():
                errors.append(f"asset {index} is missing or unsafe")

    sections = manifest.get("supported_sections")
    if not isinstance(sections, Mapping) or not REQUIRED_SECTIONS.issubset(sections):
        errors.append("supported_sections does not declare the complete contract")
    else:
        required_true = REQUIRED_SECTIONS - {"reactions"}
        unavailable = sorted(key for key in required_true if sections.get(key) is not True)
        if unavailable:
            errors.append("required sections disabled: " + ", ".join(unavailable))

    csp = manifest.get("csp")
    if not isinstance(csp, Mapping):
        errors.append("csp must be an object")
    else:
        if csp.get("remote_hosts") != []:
            errors.append("remote asset hosts are not allowed")
        if csp.get("inline_scripts") is not False or csp.get("inline_event_handlers") is not False:
            errors.append("inline executable behavior must be disabled")

    provenance = manifest.get("screenshot_provenance")
    if not isinstance(provenance, Mapping) or provenance.get("content") != "labeled_design_fixture":
        errors.append("screenshot provenance must identify labeled design-fixture content")
    compatibility = manifest.get("compatibility")
    if not isinstance(compatibility, Mapping) or compatibility.get("contract") != CONTRACT_VERSION:
        errors.append(f"compatibility.contract must be {CONTRACT_VERSION}")
    if not isinstance(manifest.get("migration_notes"), list):
        errors.append("migration_notes must be a list")

    token_schema = manifest.get("configurable_tokens")
    if not isinstance(token_schema, Mapping):
        errors.append("configurable_tokens must be an object")
    else:
        for key, definition in token_schema.items():
            if not THEME_ID.fullmatch(str(key)) or not isinstance(definition, Mapping):
                errors.append(f"invalid configurable token definition: {key}")
                continue
            if not CSS_VARIABLE.fullmatch(str(definition.get("css_variable") or "")):
                errors.append(f"invalid CSS variable for token: {key}")
            if definition.get("type") not in {"color", "length", "enum"}:
                errors.append(f"unsupported token type: {key}")
            try:
                sanitize_tokens({str(key): definition.get("default")}, token_schema)
            except ThemeContractError as exc:
                errors.append(str(exc))
    return errors


def validate_installed_themes(themes_root: Path, static_root: Path, supported_ids: tuple[str, ...]) -> dict[str, list[str]]:
    failures: dict[str, list[str]] = {}
    for theme_id in supported_ids:
        theme_dir = themes_root / theme_id
        try:
            manifest = load_manifest(theme_dir / "theme.json")
            errors = validate_manifest(manifest, theme_dir, static_root)
        except ThemeContractError as exc:
            errors = [str(exc)]
        if errors:
            failures[theme_id] = errors
    return failures


def sanitize_tokens(values: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, str]:
    unknown = set(values) - set(schema)
    if unknown:
        raise ThemeContractError("unknown customization tokens: " + ", ".join(sorted(unknown)))
    clean: dict[str, str] = {}
    for key, value in values.items():
        definition = schema.get(key)
        if not isinstance(definition, Mapping):
            raise ThemeContractError(f"missing token schema: {key}")
        raw = str(value or "").strip()
        if raw == "":
            continue
        token_type = definition.get("type")
        if token_type == "color":
            if not HEX_COLOR.fullmatch(raw):
                raise ThemeContractError(f"{key} must be a six-digit hex color")
            clean[key] = raw.lower()
        elif token_type == "length":
            match = LENGTH.fullmatch(raw)
            if not match or match.group(2) != definition.get("unit"):
                raise ThemeContractError(f"{key} must use {definition.get('unit')}")
            number = float(match.group(1))
            if number < float(definition.get("min", number)) or number > float(definition.get("max", number)):
                raise ThemeContractError(f"{key} is outside its allowed range")
            clean[key] = f"{number:g}{match.group(2)}"
        elif token_type == "enum":
            options = [str(item) for item in definition.get("options", [])]
            if raw not in options:
                raise ThemeContractError(f"{key} must be an allowlisted value")
            clean[key] = raw
        else:
            raise ThemeContractError(f"unsupported token type: {key}")
    return clean


def render_customization_css(tokens: Mapping[str, Any], schema: Mapping[str, Any]) -> str:
    clean = sanitize_tokens(tokens, schema)
    declarations = [f"{schema[key]['css_variable']}:{value}" for key, value in sorted(clean.items())]
    return ":root{" + ";".join(declarations) + (";}\n" if declarations else "}\n")
