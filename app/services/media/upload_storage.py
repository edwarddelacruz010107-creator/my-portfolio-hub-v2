"""Persistent upload storage helpers.

This module is the single place that knows how local portfolio uploads are
stored and served. It fixes a production-only class of bugs where database rows
still contain filenames after a redeploy, but templates only look in one upload
path (usually ``app/static/uploads``). Hosts such as Render rebuild the app tree
on deploy, so production should use a mounted disk (``UPLOAD_FOLDER``) or
object storage. These helpers support both the configured upload root and legacy
fallback roots while never allowing path traversal.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from flask import current_app, url_for

PUBLIC_UPLOAD_FOLDERS: frozenset[str] = frozenset({
    "profiles",
    "projects",
    "avatars",
    "billing",
    "certificates",
    "landing",
    "themes",
})
# Backward-compatible export for existing public-media call sites. Private
# proof storage deliberately lives in a separate module and is never added.
ALLOWED_UPLOAD_FOLDERS = PUBLIC_UPLOAD_FOLDERS

_MISSING_VALUES = {"", "none", "null", "undefined"}


def is_remote_url(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().startswith(("http://", "https://", "data:"))


def normalize_upload_reference(value: str | None, subfolder: str) -> tuple[str, str] | None:
    """Return ``(folder, filename)`` for a safe local upload reference.

    Accepts plain filenames, ``folder/filename``, ``/uploads/folder/filename``
    and old ``/static/uploads/folder/filename`` references.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.lower() in _MISSING_VALUES:
        return None
    if any(ch in raw for ch in ("\x00", "\r", "\n")):
        return None
    if is_remote_url(raw):
        return None

    normalized = raw.replace("\\", "/")
    if normalized.startswith("/static/uploads/"):
        normalized = normalized[len("/static/uploads/"):]
    elif normalized.startswith("static/uploads/"):
        normalized = normalized[len("static/uploads/"):]
    elif normalized.startswith("/uploads/"):
        normalized = normalized[len("/uploads/"):]
    elif normalized.startswith("uploads/"):
        normalized = normalized[len("uploads/"):]
    else:
        if normalized.startswith("/") or ".." in normalized:
            return None
        prefix = f"{subfolder}/"
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
        normalized = f"{subfolder}/{normalized}"

    parts = normalized.split("/", 1)
    if len(parts) != 2:
        return None
    folder, filename = parts[0].strip(), parts[1].strip()
    if folder not in ALLOWED_UPLOAD_FOLDERS:
        return None
    if not filename or filename.startswith("/") or ".." in filename or "\\" in filename:
        return None
    return folder, filename


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        try:
            resolved = p.resolve()
        except Exception:
            resolved = Path(os.path.abspath(str(p)))
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    return out


def candidate_upload_roots() -> list[Path]:
    """Return upload roots to search, in priority order.

    1. Configured ``UPLOAD_FOLDER`` (persistent disk or app/static/uploads)
    2. ``app/static/uploads`` legacy/default tree
    3. ``<repo>/storage/uploads`` legacy storage tree
    4. ``<repo>/uploads`` legacy root-level tree
    5. ``/var/data/uploads`` common Render persistent disk path, if present
    """
    roots: list[Path] = []

    configured = current_app.config.get("UPLOAD_FOLDER")
    if configured:
        roots.append(Path(configured))

    if current_app.static_folder:
        roots.append(Path(current_app.static_folder) / "uploads")

    try:
        app_root = Path(current_app.root_path).resolve().parent
        roots.extend([
            app_root / "storage" / "uploads",
            app_root / "uploads",
        ])
    except Exception:
        pass

    var_data = Path("/var/data/uploads")
    if var_data.exists() or Path("/var/data").exists():
        roots.append(var_data)

    return _unique_paths(roots)


def primary_upload_root() -> Path:
    configured = current_app.config.get("UPLOAD_FOLDER")
    if configured:
        return Path(configured).resolve()
    if current_app.static_folder:
        return (Path(current_app.static_folder) / "uploads").resolve()
    return Path("uploads").resolve()


def ensure_upload_folder(subfolder: str) -> Path:
    if subfolder not in ALLOWED_UPLOAD_FOLDERS:
        raise ValueError(f"Invalid upload folder: {subfolder}")
    root = primary_upload_root()
    directory = (root / subfolder).resolve()
    try:
        directory.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Invalid upload destination") from exc
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_upload_file(folder: str, filename: str) -> Path | None:
    if folder not in ALLOWED_UPLOAD_FOLDERS or not filename or ".." in filename or filename.startswith("/"):
        return None
    safe_filename = filename.replace("\\", "/").strip()
    if "/" in safe_filename or not safe_filename:
        return None

    for root in candidate_upload_roots():
        directory = (root / folder).resolve()
        target = (directory / safe_filename).resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            continue
        if target.is_file():
            return target
    return None


def upload_exists(value: str | None, subfolder: str) -> bool:
    if is_remote_url(value):
        return True
    normalized = normalize_upload_reference(value, subfolder)
    if not normalized:
        return False
    folder, filename = normalized
    return resolve_upload_file(folder, filename) is not None


def upload_size(value: str | None, subfolder: str) -> int | None:
    if is_remote_url(value):
        return None
    normalized = normalize_upload_reference(value, subfolder)
    if not normalized:
        return None
    folder, filename = normalized
    target = resolve_upload_file(folder, filename)
    if not target:
        return None
    try:
        return target.stat().st_size
    except OSError:
        return None


def build_upload_url(value: str | None, subfolder: str, *, require_exists: bool | None = None) -> str:
    """Return a public URL for an uploaded file.

    By default local URLs are returned only when the file exists in one of the
    known upload roots. The existence check searches the configured persistent
    root and legacy static roots, so path migrations do not hide valid files.
    """
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if raw.lower() in _MISSING_VALUES:
        return ""
    if any(ch in raw for ch in ("\x00", "\r", "\n")):
        return ""
    if raw.startswith(("http://", "https://", "data:")):
        return raw

    normalized = normalize_upload_reference(raw, subfolder)
    if not normalized:
        return ""
    folder, filename = normalized

    public_base = (current_app.config.get("UPLOAD_PUBLIC_BASE_URL") or "").rstrip("/")
    if public_base:
        return f"{public_base}/{folder}/{filename}"

    if require_exists is None:
        require_exists = bool(current_app.config.get("UPLOAD_URL_REQUIRE_FILE_EXISTS", True))
    if require_exists and resolve_upload_file(folder, filename) is None:
        return ""

    try:
        return url_for("uploaded_media", subfolder=folder, filename=filename)
    except Exception:
        return f"/uploads/{folder}/{filename}"


def delete_upload_file(value: str | None, subfolder: str) -> None:
    """Delete a local uploaded file from all known local roots.

    Remote object-storage URLs are handled by the caller/provider integration.
    """
    if not value or is_remote_url(value):
        return
    normalized = normalize_upload_reference(value, subfolder)
    if not normalized:
        return
    folder, filename = normalized
    for root in candidate_upload_roots():
        directory = (root / folder).resolve()
        target = (directory / filename).resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            continue
        try:
            if target.is_file():
                target.unlink()
        except OSError:
            current_app.logger.warning("Could not delete upload file %s", target, exc_info=True)
