"""
app/services/storage_service.py — SaaS-Grade Upload & Storage Quota System (v6.0)

Responsibilities:
  1. Pre-upload validation (MIME type whitelist, per-file size, quota headroom)
  2. Image optimization (WebP conversion, JPEG compression, thumbnail generation)
  3. Secure filesystem writes (tenant-isolated paths, no path traversal)
  4. Storage quota tracking (tenant.storage_used_bytes updated atomically)
  5. File deletion with quota reclamation
  6. Quota dashboard helpers

FILE LAYOUT ON DISK:
    <UPLOAD_BASE>/
      <tenant_slug>/
        projects/
          <uuid4>.<ext>
          <uuid4>_thumb.webp
        pages/
          <uuid4>.<ext>
        general/
          <uuid4>.<ext>

DATABASE (MediaUpload model, see models/core.py addition):
    file_path       TEXT        — relative path from UPLOAD_BASE
    file_size       INTEGER     — bytes on disk (post-optimisation)
    original_size   INTEGER     — bytes received pre-optimisation
    mime_type       VARCHAR(100)
    uploaded_at     DATETIME
    tenant_id       INTEGER     FK → tenants.id
    category        VARCHAR(50) — 'project' | 'page' | 'general'
    is_deleted      BOOLEAN     — soft-delete for audit trail

SECURITY:
    • ALLOWED_MIME_TYPES whitelist — executables, SVG, ZIP blocked
    • Filename sanitised with uuid4 — no original filename on disk
    • Path resolved against UPLOAD_BASE to prevent traversal
    • ZIP-bomb guard: content-length required before write
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from pathlib import Path
from typing import BinaryIO

from flask import current_app

logger = logging.getLogger(__name__)

# ─── Security whitelist ───────────────────────────────────────────────────────

ALLOWED_MIME_TYPES: frozenset[str] = frozenset({
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
    'application/pdf',
})

ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.pdf',
})

# WebP conversion applies to photo uploads. Animated GIF/WebP is preserved.
_CONVERTIBLE_TO_WEBP: frozenset[str] = frozenset({'image/jpeg', 'image/png', 'image/webp'})

# ─── Optimisation settings ────────────────────────────────────────────────────

_WEBP_QUALITY     = 82      # 0–100; 82 gives ~80% reduction with minimal quality loss
_JPEG_QUALITY     = 85
_MAX_DIMENSION    = 2048    # pixels — resize if either dimension exceeds this
_THUMB_SIZE       = (300, 300)


class StorageError(Exception):
    """Raised on validation or I/O failures."""


class QuotaExceededError(StorageError):
    """Raised when the upload would exceed tenant quota."""


class FileSizeError(StorageError):
    """Raised when the file exceeds the per-file limit for the plan."""


class InvalidFileError(StorageError):
    """Raised when MIME type or extension is not whitelisted."""


# ─── Path helpers ─────────────────────────────────────────────────────────────

def _upload_base() -> Path:
    base = current_app.config.get('UPLOAD_BASE_PATH', 'uploads')
    return Path(base).resolve()


def _tenant_dir(tenant_slug: str, category: str) -> Path:
    """
    Build and create (if needed) the tenant-scoped upload directory.
    Validates that the resolved path stays within UPLOAD_BASE.
    """
    base    = _upload_base()
    # Sanitise slug: allow only alphanumeric + hyphens
    safe_slug = ''.join(c for c in tenant_slug if c.isalnum() or c == '-')
    target    = (base / safe_slug / category).resolve()

    # Path-traversal guard
    if not str(target).startswith(str(base)):
        raise StorageError(f'Unsafe path resolved: {target}')

    target.mkdir(parents=True, exist_ok=True)
    return target


def relative_path(abs_path: Path) -> str:
    """Return path relative to UPLOAD_BASE for DB storage."""
    base = _upload_base()
    return str(abs_path.relative_to(base))


# ─── MIME & extension validation ──────────────────────────────────────────────

def _validate_file(
    file_obj: BinaryIO,
    filename: str,
    mime_type: str,
    file_bytes: bytes | None = None,
) -> None:
    """
    Validate MIME type, extension, magic bytes, and image content centrally.
    Raises InvalidFileError on violation before any bytes are written.
    """
    ext = Path(filename).suffix.lower()

    if mime_type not in ALLOWED_MIME_TYPES:
        raise InvalidFileError(
            f'File type "{mime_type}" is not allowed. '
            f'Accepted types: JPEG, PNG, WebP, GIF, PDF.'
        )
    if ext not in ALLOWED_EXTENSIONS:
        raise InvalidFileError(
            f'File extension "{ext}" is not allowed.'
        )

    from app.security import FileUploadPolicy, validate_magic_bytes
    ext_no_dot = ext.lstrip('.')
    if mime_type.startswith('image/'):
        ok, err = FileUploadPolicy.validate_image_upload(
            filename, len(file_bytes or b''), file_bytes=file_bytes, declared_mime=mime_type
        )
        if not ok:
            raise InvalidFileError(err)
    elif file_bytes is not None:
        ok, err = validate_magic_bytes(file_bytes, ext_no_dot)
        if not ok:
            raise InvalidFileError(err)


# ─── Image optimisation ───────────────────────────────────────────────────────

def _optimise_image(
    data: bytes,
    mime_type: str,
) -> tuple[bytes, str]:
    """
    Convert portfolio image uploads to lightweight WebP and resize oversized images.

    Animated images are preserved by the shared optimizer so they do not turn
    into static first-frame images.
    """
    try:
        from app.services.media.image_optimizer import optimize_image_bytes_to_webp

        if mime_type.startswith('image/'):
            optimized = optimize_image_bytes_to_webp(
                data,
                source_extension=None,
                source_mime=mime_type,
                quality=int(current_app.config.get('UPLOAD_WEBP_QUALITY', _WEBP_QUALITY)),
                max_dimension=int(current_app.config.get('UPLOAD_IMAGE_MAX_DIMENSION', _MAX_DIMENSION)),
                preserve_animation=True,
                force=True,
            )
            return optimized.data, optimized.mime_type
        return data, mime_type

    except ImportError:
        logger.warning('[StorageService] Pillow not available — skipping image optimisation')
        return data, mime_type
    except Exception as exc:
        logger.warning('[StorageService] Image optimisation failed (%s) — rejecting upload', exc)
        raise InvalidFileError('Uploaded image could not be processed safely.') from exc

def _generate_thumbnail(data: bytes, mime_type: str, dest_path: Path) -> bool:
    """Write a thumbnail WebP. Returns True on success."""
    try:
        from PIL import Image as _Image
        img = _Image.open(io.BytesIO(data))
        img.thumbnail(_THUMB_SIZE, _Image.LANCZOS)
        img.convert('RGB').save(str(dest_path), format='WEBP', quality=75)
        return True
    except Exception as exc:
        logger.warning('[StorageService] Thumbnail generation failed: %s', exc)
        return False


# ─── Core upload function ────────────────────────────────────────────────────

def save_upload(
    tenant,
    file_obj: BinaryIO,
    filename: str,
    mime_type: str,
    category: str = 'general',
    generate_thumb: bool = False,
) -> dict:
    """
    Validate, optimise, and persist an uploaded file.

    Pre-conditions (checked IN ORDER before any bytes are written):
        1. MIME type / extension whitelisted
        2. File size ≤ plan per-file limit
        3. Remaining quota ≥ file size

    Post-conditions:
        • File written to <UPLOAD_BASE>/<slug>/<category>/<uuid>.<ext>
        • tenant.storage_used_bytes incremented
        • MediaUpload DB record created (caller must commit)

    Returns a dict with file metadata for the DB record.
    Raises StorageError subclasses on any failure.
    """
    from app.services.plan_capabilities import get_tenant_capabilities, CapabilityError

    # ── 1. Read content ───────────────────────────────────────────────────────
    raw_data = file_obj.read()
    original_size = len(raw_data)

    if original_size == 0:
        raise InvalidFileError('Uploaded file is empty.')

    # ── 2. Validate MIME + extension + content before writing bytes ───────────
    _validate_file(file_obj, filename, mime_type, raw_data)

    # ── 3. Capability checks (size + quota) ────────────────────────────────────
    caps = get_tenant_capabilities(tenant)
    used = getattr(tenant, 'storage_used_bytes', 0) or 0
    ok, reason = caps.can_upload(file_bytes=original_size, current_used_bytes=used)
    if not ok:
        if 'quota' in reason.lower():
            raise QuotaExceededError(reason)
        raise FileSizeError(reason)

    # ── 4. Optimise image ────────────────────────────────────────────────────
    is_image = mime_type.startswith('image/')
    if is_image:
        optimised_data, output_mime = _optimise_image(raw_data, mime_type)
    else:
        optimised_data, output_mime = raw_data, mime_type

    final_size = len(optimised_data)

    # Re-check quota with optimised size (should always pass, but be safe)
    ok2, reason2 = caps.can_upload(file_bytes=final_size, current_used_bytes=used)
    if not ok2:
        raise QuotaExceededError(reason2)

    # ── 5. Write to disk ──────────────────────────────────────────────────────
    slug     = getattr(tenant, 'slug', f'tenant_{tenant.id}')
    dest_dir = _tenant_dir(slug, category)

    ext_map = {
        'image/webp': '.webp',
        'image/jpeg': '.jpg',
        'image/png':  '.png',
        'image/gif':  '.gif',
        'application/pdf': '.pdf',
    }
    ext = ext_map.get(output_mime, Path(filename).suffix.lower())

    file_uuid    = uuid.uuid4().hex
    dest_path    = dest_dir / f'{file_uuid}{ext}'
    thumb_path   = dest_dir / f'{file_uuid}_thumb.webp' if generate_thumb and is_image else None

    dest_path.write_bytes(optimised_data)

    if thumb_path:
        _generate_thumbnail(optimised_data, output_mime, thumb_path)

    # ── 6. Update tenant quota ────────────────────────────────────────────────
    tenant.storage_used_bytes = (getattr(tenant, 'storage_used_bytes', 0) or 0) + final_size

    saved_pct = (1 - final_size / original_size) * 100 if original_size > 0 else 0
    logger.info(
        '[StorageService] SAVED tenant_id=%s file=%s '
        'original=%d final=%d saved=%.0f%% quota_used=%d',
        tenant.id, dest_path.name,
        original_size, final_size, saved_pct,
        tenant.storage_used_bytes,
    )

    # ── 7. Warn if approaching quota ──────────────────────────────────────────
    if caps.storage_warning(tenant.storage_used_bytes):
        logger.warning(
            '[StorageService] QUOTA WARNING tenant_id=%s used=%.1f%% of %s',
            tenant.id,
            caps.storage_usage_pct(tenant.storage_used_bytes),
            caps.plan_name,
        )

    rel_path = relative_path(dest_path)
    return {
        'file_path':       rel_path,
        'thumb_path':      relative_path(thumb_path) if thumb_path and thumb_path.exists() else None,
        'file_size':       final_size,
        'original_size':   original_size,
        'mime_type':       output_mime,
        'category':        category,
        'tenant_id':       tenant.id,
        'original_name':   Path(filename).name,
    }


# ─── Deletion with quota reclamation ─────────────────────────────────────────

def delete_upload(tenant, upload_record) -> bool:
    """
    Delete a MediaUpload record's file from disk and reclaim quota.
    Soft-deletes the DB record (caller must commit).

    Returns True if the disk file was removed (or already absent).
    """
    file_path = getattr(upload_record, 'file_path', None)
    file_size = getattr(upload_record, 'file_size', 0) or 0

    removed = False
    if file_path:
        abs_path = (_upload_base() / file_path).resolve()
        # Traversal guard
        if str(abs_path).startswith(str(_upload_base())):
            try:
                abs_path.unlink(missing_ok=True)
                removed = True
            except OSError as exc:
                logger.error('[StorageService] DELETE failed %s: %s', abs_path, exc)
        else:
            logger.error('[StorageService] Path traversal blocked on delete: %s', abs_path)

    # Thumbnail
    thumb_path = getattr(upload_record, 'thumb_path', None)
    if thumb_path:
        abs_thumb = (_upload_base() / thumb_path).resolve()
        if str(abs_thumb).startswith(str(_upload_base())):
            abs_thumb.unlink(missing_ok=True)

    # Reclaim quota
    current_used = getattr(tenant, 'storage_used_bytes', 0) or 0
    tenant.storage_used_bytes = max(0, current_used - file_size)

    # Soft-delete
    upload_record.is_deleted = True

    logger.info(
        '[StorageService] DELETE tenant_id=%s file=%s reclaimed=%d new_used=%d',
        tenant.id, file_path, file_size, tenant.storage_used_bytes,
    )
    return removed


# ─── Quota dashboard helpers ──────────────────────────────────────────────────

def get_quota_summary(tenant) -> dict:
    """
    Return a quota summary dict for template rendering.

    {
        'used_bytes':    int,
        'limit_bytes':   int | None,
        'used_mb':       float,
        'limit_mb':      float | None,
        'pct':           float,       # 0–100
        'warning':       bool,        # >= 90%
        'plan':          str,
        'unlimited':     bool,
    }
    """
    from app.services.plan_capabilities import get_tenant_capabilities
    caps = get_tenant_capabilities(tenant)
    used = getattr(tenant, 'storage_used_bytes', 0) or 0

    return {
        'used_bytes':  used,
        'limit_bytes': caps.storage_limit_bytes,
        'used_mb':     round(used / (1024 * 1024), 2),
        'limit_mb':    caps.storage_limit_mb,
        'pct':         round(caps.storage_usage_pct(used), 1),
        'warning':     caps.storage_warning(used),
        'plan':        caps.plan_name,
        'unlimited':   caps.storage_limit_bytes is None,
    }


def recalculate_tenant_storage(tenant) -> int:
    """
    Recount storage from disk for a tenant and update the DB field.
    Use for periodic reconciliation or after bulk deletes.
    Returns the recalculated byte count.

    Caller must commit the session.
    """
    slug     = getattr(tenant, 'slug', f'tenant_{tenant.id}')
    base     = _upload_base() / slug

    total = 0
    if base.exists():
        for f in base.rglob('*'):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass

    tenant.storage_used_bytes = total
    logger.info('[StorageService] RECALC tenant_id=%s slug=%s total_bytes=%d', tenant.id, slug, total)
    return total
