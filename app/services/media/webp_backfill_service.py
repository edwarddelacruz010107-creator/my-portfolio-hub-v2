"""
app/services/media/webp_backfill_service.py

Maintenance helpers for converting existing local upload records to WebP.

This is for already-uploaded JPG/PNG/WebP files referenced by tenant profile,
projects, testimonials, certificates and badges. New uploads are handled by
app.utils.save_image() automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from flask import current_app

logger = logging.getLogger(__name__)


@dataclass
class WebPBackfillItem:
    model: str
    field: str
    object_id: Any
    folder: str
    old_filename: str
    new_filename: str | None = None
    status: str = "pending"
    message: str = ""
    original_size: int = 0
    final_size: int = 0

    @property
    def bytes_saved(self) -> int:
        return max(0, self.original_size - self.final_size)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bytes_saved"] = self.bytes_saved
        return data


def _upload_root() -> Path:
    upload_folder = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
    return Path(upload_folder).resolve()


def _safe_upload_path(folder: str, filename: str) -> Path | None:
    if not filename or "://" in filename or filename.startswith("/"):
        return None
    root = _upload_root()
    path = (root / folder / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        logger.warning("Blocked unsafe upload path folder=%s filename=%s", folder, filename)
        return None
    return path


def _convert_one_file(filename: str, folder: str, *, quality: int, dry_run: bool) -> tuple[str | None, str, int, int]:
    """Return (new_filename, status, original_size, final_size)."""
    path = _safe_upload_path(folder, filename)
    if path is None:
        return None, "skipped_external_or_unsafe", 0, 0
    if not path.exists():
        return None, "missing_file", 0, 0

    ext = path.suffix.lower().lstrip(".")
    if ext == "webp":
        return filename, "already_webp", path.stat().st_size, path.stat().st_size
    if ext not in {"jpg", "jpeg", "png"}:
        return filename, "skipped_unsupported", path.stat().st_size, path.stat().st_size

    raw = path.read_bytes()
    original_size = len(raw)

    from app.services.media.image_optimizer import optimize_image_bytes_to_webp

    optimized = optimize_image_bytes_to_webp(
        raw,
        ext,
        quality=quality,
        max_dimension=int(current_app.config.get("UPLOAD_IMAGE_MAX_DIMENSION", 2048)),
        preserve_animation=True,
        force=True,
    )
    new_filename = f"{path.stem}.webp"
    new_path = path.with_name(new_filename)

    # Avoid accidental overwrite if an unrelated converted file already exists.
    if new_path.exists() and new_path.resolve() != path.resolve():
        new_filename = f"{path.stem}-webp.webp"
        new_path = path.with_name(new_filename)

    if dry_run:
        return new_filename, "would_convert", original_size, optimized.final_size

    tmp_path = path.with_suffix(path.suffix + ".tmp-webp")
    tmp_path.write_bytes(optimized.data)
    os.replace(tmp_path, new_path)
    try:
        path.unlink(missing_ok=True)
    except TypeError:  # Python <3.8 compatibility fallback
        if path.exists():
            path.unlink()

    return new_filename, "converted", original_size, optimized.final_size


def _record_item(obj, field: str, folder: str) -> WebPBackfillItem | None:
    filename = getattr(obj, field, None)
    if not filename:
        return None
    return WebPBackfillItem(
        model=obj.__class__.__name__,
        field=field,
        object_id=getattr(obj, "id", None),
        folder=folder,
        old_filename=filename,
    )


def _iter_media_records(tenant_slug: str | None = None):
    from app.models.portfolio import Profile, Project, Testimonial, Certificate

    profile_query = Profile.query
    project_query = Project.query
    testimonial_query = Testimonial.query
    certificate_query = Certificate.query

    if tenant_slug:
        profile_query = profile_query.filter(Profile.tenant_slug == tenant_slug)
        project_query = project_query.filter(Project.tenant_slug == tenant_slug)
        testimonial_query = testimonial_query.filter(Testimonial.tenant_slug == tenant_slug)
        certificate_query = certificate_query.filter(Certificate.tenant_slug == tenant_slug)

    for profile in profile_query.all():
        item = _record_item(profile, "profile_image", "profiles")
        if item:
            yield profile, item

    for project in project_query.all():
        item = _record_item(project, "image", "projects")
        if item:
            yield project, item

    for testimonial in testimonial_query.all():
        item = _record_item(testimonial, "author_avatar", "profiles")
        if item:
            yield testimonial, item

    for certificate in certificate_query.all():
        item = _record_item(certificate, "image_path", "certificates")
        if item:
            yield certificate, item
        item = _record_item(certificate, "badge_path", "certificates")
        if item:
            yield certificate, item


def convert_existing_uploads_to_webp(
    *,
    tenant_slug: str | None = None,
    dry_run: bool = True,
    quality: int | None = None,
    commit_every: int = 50,
) -> dict[str, Any]:
    """
    Convert existing local JPG/PNG uploads to WebP and update DB references.

    Set ``dry_run=False`` to apply changes.  The function intentionally skips
    external URLs, missing files, GIFs, PDFs, and files that are already WebP.
    """
    from app import db

    quality = int(quality or current_app.config.get("UPLOAD_WEBP_QUALITY", 82))
    items: list[WebPBackfillItem] = []
    changed = 0
    total_original = 0
    total_final = 0

    for obj, item in _iter_media_records(tenant_slug):
        try:
            new_filename, status, original_size, final_size = _convert_one_file(
                item.old_filename,
                item.folder,
                quality=quality,
                dry_run=dry_run,
            )
            item.status = status
            item.new_filename = new_filename
            item.original_size = original_size
            item.final_size = final_size
            total_original += original_size
            total_final += final_size

            if status == "converted" and new_filename:
                setattr(obj, item.field, new_filename)
                changed += 1
                if commit_every and changed % commit_every == 0:
                    db.session.commit()
            elif status == "would_convert":
                changed += 1
        except Exception as exc:
            item.status = "error"
            item.message = str(exc)
            logger.exception("WebP backfill failed for %s.%s id=%s", item.model, item.field, item.object_id)
        items.append(item)

    if not dry_run:
        db.session.commit()

    return {
        "dry_run": dry_run,
        "tenant_slug": tenant_slug,
        "changed": changed,
        "total_items": len(items),
        "original_bytes": total_original,
        "final_bytes": total_final,
        "bytes_saved": max(0, total_original - total_final),
        "items": [i.to_dict() for i in items],
    }
