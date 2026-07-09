"""
app/services/media/image_optimizer.py

Central image optimisation helpers for Portfolio Hub.

Purpose
-------
All public-facing photo uploads should be stored as lightweight WebP files so
portfolio pages load faster and consume less storage.  This module is intentionally
framework-light so it can be reused by:

* legacy local uploads via app.utils.save_image()
* Supabase upload helpers
* the newer tenant media storage service
* maintenance/backfill scripts for existing JPG/PNG files

Animated GIF/WebP files are preserved by default to avoid silently converting an
animation into a single static frame.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import logging
from typing import Iterable

logger = logging.getLogger(__name__)

WEBP_SOURCE_EXTENSIONS: frozenset[str] = frozenset({"jpg", "jpeg", "png", "webp"})
WEBP_SOURCE_MIME_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/webp"})
PRESERVED_EXTENSIONS: frozenset[str] = frozenset({"gif"})
DEFAULT_WEBP_QUALITY = 82
DEFAULT_MAX_DIMENSION = 2048


@dataclass(frozen=True)
class OptimizedImage:
    """Result returned by :func:`optimize_image_bytes_to_webp`."""

    data: bytes
    extension: str
    mime_type: str
    converted: bool
    original_size: int
    final_size: int
    width: int | None = None
    height: int | None = None
    original_format: str | None = None

    @property
    def bytes_saved(self) -> int:
        return max(0, self.original_size - self.final_size)

    @property
    def percent_saved(self) -> float:
        if self.original_size <= 0:
            return 0.0
        return round((self.bytes_saved / self.original_size) * 100, 2)


def _normalise_ext(ext: str | None) -> str:
    return (ext or "").lower().lstrip(".")


def _normalise_size(max_size: tuple[int, int] | int | None) -> tuple[int, int] | None:
    if max_size is None:
        return None
    if isinstance(max_size, int):
        return (max_size, max_size)
    if len(max_size) != 2:
        return None
    w, h = int(max_size[0]), int(max_size[1])
    if w <= 0 or h <= 0:
        return None
    return (w, h)


def is_webp_candidate(extension: str | None, mime_type: str | None = None) -> bool:
    """Return True when an upload should be converted/re-encoded as WebP."""
    ext = _normalise_ext(extension)
    mime = (mime_type or "").lower()
    return ext in WEBP_SOURCE_EXTENSIONS or mime in WEBP_SOURCE_MIME_TYPES


def optimize_image_bytes_to_webp(
    data: bytes,
    source_extension: str | None = None,
    *,
    source_mime: str | None = None,
    max_size: tuple[int, int] | int | None = None,
    quality: int = DEFAULT_WEBP_QUALITY,
    max_dimension: int | None = DEFAULT_MAX_DIMENSION,
    preserve_animation: bool = True,
    force: bool = True,
) -> OptimizedImage:
    """Validate, resize, strip metadata, and encode image bytes as WebP.

    Parameters
    ----------
    data:
        Raw uploaded image bytes.
    source_extension/source_mime:
        Used to decide whether this upload is eligible for WebP conversion.
    max_size:
        Optional exact thumbnail-style bounding box.  Existing callers use this
        for profile/testimonial/certificate images.
    quality:
        WebP quality 1-100.  The app defaults to 82 for a good size/quality
        balance and may pass a higher value for profile photos.
    max_dimension:
        Safety cap for very large uploads when no max_size is supplied.
    preserve_animation:
        Keep animated GIF/WebP as the original bytes so animation is not lost.
    force:
        When False and the upload is not a WebP candidate, original bytes are
        returned unchanged.

    Returns
    -------
    OptimizedImage
        ``extension`` is normally ``webp``.  Animated/unsupported images return
        the original extension and ``converted=False``.
    """
    if not data:
        raise ValueError("Cannot optimise an empty image.")

    source_ext = _normalise_ext(source_extension)
    source_mime = (source_mime or "").lower()

    if not force and not is_webp_candidate(source_ext, source_mime):
        return OptimizedImage(
            data=data,
            extension=source_ext or "bin",
            mime_type=source_mime or "application/octet-stream",
            converted=False,
            original_size=len(data),
            final_size=len(data),
        )

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - production requires Pillow
        raise RuntimeError("Pillow is required for WebP upload optimisation.") from exc

    with Image.open(io.BytesIO(data)) as img:
        original_format = (img.format or source_ext or "").upper()
        is_animated = bool(getattr(img, "is_animated", False))

        if preserve_animation and is_animated:
            mime = source_mime or {
                "GIF": "image/gif",
                "WEBP": "image/webp",
            }.get(original_format, "image/" + (source_ext or "gif"))
            return OptimizedImage(
                data=data,
                extension=source_ext or original_format.lower() or "gif",
                mime_type=mime,
                converted=False,
                original_size=len(data),
                final_size=len(data),
                width=getattr(img, "width", None),
                height=getattr(img, "height", None),
                original_format=original_format,
            )

        img = ImageOps.exif_transpose(img)

        size_box = _normalise_size(max_size)
        if size_box:
            img.thumbnail(size_box, Image.LANCZOS)
        elif max_dimension:
            max_dimension = int(max_dimension)
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

        # WebP supports alpha. Keep transparency for PNG/WebP uploads that have it.
        has_alpha = img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")

        buf = io.BytesIO()
        q = max(1, min(100, int(quality or DEFAULT_WEBP_QUALITY)))
        img.save(buf, format="WEBP", quality=q, method=6, optimize=True)
        webp_data = buf.getvalue()

    return OptimizedImage(
        data=webp_data,
        extension="webp",
        mime_type="image/webp",
        converted=True,
        original_size=len(data),
        final_size=len(webp_data),
        width=getattr(img, "width", None),
        height=getattr(img, "height", None),
        original_format=original_format,
    )


def image_mime_for_extension(extension: str | None) -> str:
    """Small extension→MIME helper shared by upload backends."""
    ext = _normalise_ext(extension)
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
