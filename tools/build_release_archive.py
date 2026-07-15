#!/usr/bin/env python3
"""Build a deterministic source release that excludes all runtime data."""

from __future__ import annotations

import argparse
import hashlib
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "portfolio-hub-production-release-candidate"
BLOCKED_PARTS = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
    "node_modules",
    "instance",
    "logs",
}
BLOCKED_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log"}


def _included(path: Path, output: Path) -> bool:
    relative = path.relative_to(ROOT)
    if path.resolve() == output.resolve() or any(part in BLOCKED_PARTS for part in relative.parts):
        return False
    if relative.parts[:3] == ("app", "static", "uploads"):
        return False
    if relative.parts[:2] == ("storage", "private_uploads"):
        return False
    if path.suffix.lower() in BLOCKED_SUFFIXES:
        return False
    name = path.name.lower()
    if name.startswith(".tmp_") or ".openai-download-" in name or name.endswith(".patched"):
        return False
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return False
    return path.is_file() and not path.is_symlink()


def _zip_info(name: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2026, 7, 15, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def build(output: Path) -> tuple[int, str]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [path for path in sorted(ROOT.rglob("*")) if _included(path, output)]
    manifest: list[str] = []
    with zipfile.ZipFile(output, "w", compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            manifest.append(f"{digest}  {relative}")
            mode = 0o755 if path.name == "docker-entrypoint.sh" else 0o644
            archive.writestr(_zip_info(f"{PREFIX}/{relative}", mode), payload)
        manifest_payload = ("\n".join(manifest) + "\n").encode("utf-8")
        archive.writestr(
            _zip_info(f"{PREFIX}/RELEASE_MANIFEST.sha256"),
            manifest_payload,
        )
    return len(files), hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count, digest = build(args.output)
    print(f"wrote {args.output.resolve()} ({count} files, sha256={digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
