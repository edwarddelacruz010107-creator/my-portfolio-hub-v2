"""Cloudinary-backed image storage for portfolio media.

This provider is deliberately isolated behind the same ``save_image`` contract
used by local and Supabase storage. Profile photos, project screenshots,
testimonial avatars, certificates, badges, landing images, and theme catalog
images can therefore switch providers without route-specific rewrites.

Security notes:
* credentials are read only from Flask config/environment variables;
* uploads are validated through the shared FileUploadPolicy;
* images are converted to lightweight WebP before upload when enabled;
* deletion accepts only URLs hosted by Cloudinary and owned by the configured
  folder root.
"""

from __future__ import annotations

import io
import logging
import os
import re
import uuid
from typing import Optional
from urllib.parse import unquote, urlparse

from flask import current_app
from werkzeug.datastructures import FileStorage

logger = logging.getLogger(__name__)


def _config_value(name: str, default: str = "") -> str:
    try:
        value = current_app.config.get(name, default)
    except RuntimeError:
        value = os.environ.get(name, default)
    return str(value or "").strip()


def _parse_cloudinary_url(value: str) -> tuple[str, str, str] | None:
    """Parse ``cloudinary://api_key:api_secret@cloud_name`` safely."""
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme != "cloudinary" or not parsed.hostname:
        return None
    api_key = unquote(parsed.username or "")
    api_secret = unquote(parsed.password or "")
    cloud_name = parsed.hostname
    if not api_key or not api_secret or not cloud_name:
        return None
    return cloud_name, api_key, api_secret


def _credentials() -> tuple[str, str, str]:
    from_url = _parse_cloudinary_url(_config_value("CLOUDINARY_URL"))
    if from_url:
        return from_url
    return (
        _config_value("CLOUDINARY_CLOUD_NAME"),
        _config_value("CLOUDINARY_API_KEY"),
        _config_value("CLOUDINARY_API_SECRET"),
    )


def is_configured() -> bool:
    return all(_credentials())


def _configure_cloudinary():
    try:
        import cloudinary
    except ImportError as exc:
        raise RuntimeError("cloudinary package is not installed. Run: pip install cloudinary") from exc

    cloud_name, api_key, api_secret = _credentials()
    if not all((cloud_name, api_key, api_secret)):
        raise RuntimeError(
            "Cloudinary is selected but credentials are incomplete. Set "
            "CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET "
            "or set CLOUDINARY_URL."
        )

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )
    return cloudinary


def _read_file_bytes(file: FileStorage) -> bytes:
    stream = getattr(file, "stream", file)
    try:
        stream.seek(0)
    except Exception:
        pass
    data = stream.read()
    try:
        stream.seek(0)
    except Exception:
        pass
    return data


def _safe_folder(folder: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", (folder or "uploads").strip()).strip("-")
    return safe or "uploads"


def _folder_path(folder: str) -> str:
    root = re.sub(r"[^a-zA-Z0-9_/-]+", "-", _config_value("CLOUDINARY_FOLDER_ROOT", "myportfoliohub")).strip("/")
    leaf = _safe_folder(folder)
    return f"{root}/{leaf}" if root else leaf


def save_image(file: FileStorage, folder: str = "uploads") -> Optional[str]:
    """Validate, optimize, and upload an image to Cloudinary.

    Returns the HTTPS CDN URL on success, otherwise ``None``. Errors are logged
    without leaking credentials.
    """
    if not file or not getattr(file, "filename", None):
        return None

    try:
        raw_data = _read_file_bytes(file)
        if not raw_data:
            logger.warning("Cloudinary upload rejected: empty file")
            return None

        from app.security import FileUploadPolicy

        ok, error = FileUploadPolicy.validate_image_upload(
            file.filename,
            len(raw_data),
            file_bytes=raw_data,
            declared_mime=getattr(file, "mimetype", None),
        )
        if not ok:
            logger.warning("Cloudinary upload rejected for %s: %s", file.filename, error)
            return None

        source_ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        final_data = raw_data
        final_ext = source_ext or "webp"
        final_mime = getattr(file, "mimetype", None) or "application/octet-stream"

        if bool(current_app.config.get("CONVERT_UPLOADS_TO_WEBP", True)):
            from app.services.media.image_optimizer import optimize_image_bytes_to_webp

            optimized = optimize_image_bytes_to_webp(
                raw_data,
                source_ext,
                source_mime=getattr(file, "mimetype", None),
                quality=int(current_app.config.get("UPLOAD_WEBP_QUALITY", 82)),
                max_dimension=int(current_app.config.get("UPLOAD_IMAGE_MAX_DIMENSION", 2048)),
                preserve_animation=True,
                force=True,
            )
            final_data = optimized.data
            final_ext = optimized.extension
            final_mime = optimized.mime_type
            logger.info(
                "Cloudinary image optimized folder=%s original=%d final=%d saved=%.1f%%",
                folder,
                optimized.original_size,
                optimized.final_size,
                optimized.percent_saved,
            )

        _configure_cloudinary()
        import cloudinary.uploader

        public_id = uuid.uuid4().hex
        result = cloudinary.uploader.upload(
            io.BytesIO(final_data),
            resource_type="image",
            folder=_folder_path(folder),
            public_id=public_id,
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            invalidate=True,
            format=final_ext if final_ext in {"webp", "jpg", "jpeg", "png", "gif"} else None,
            context={
                "app": "myportfoliohub",
                "category": _safe_folder(folder),
                "content_type": final_mime,
            },
        )
        secure_url = str(result.get("secure_url") or "").strip()
        if not secure_url.startswith("https://"):
            logger.error("Cloudinary upload completed without a secure_url")
            return None
        logger.info("Uploaded image to Cloudinary folder=%s public_id=%s", folder, result.get("public_id"))
        return secure_url
    except Exception:
        logger.exception("Cloudinary image upload failed for folder=%s", folder)
        return None
    finally:
        try:
            file.stream.seek(0)
        except Exception:
            pass



def save_billing_proof(file: FileStorage, folder: str = "billing") -> Optional[str]:
    """Upload a validated billing proof to persistent Cloudinary storage.

    Image proofs use the normal optimized image pipeline. PDF proofs are
    uploaded as authenticated server-side raw assets. The caller is still
    responsible for applying ``FileUploadPolicy.validate_billing_proof_upload``
    before this function is called.
    """
    if not file or not getattr(file, "filename", None):
        return None

    extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if extension in {"png", "jpg", "jpeg", "webp"}:
        return save_image(file, folder=folder)
    if extension != "pdf":
        return None

    try:
        raw_data = _read_file_bytes(file)
        if not raw_data:
            return None
        _configure_cloudinary()
        import cloudinary.uploader

        public_id = f"{uuid.uuid4().hex}.pdf"
        result = cloudinary.uploader.upload(
            io.BytesIO(raw_data),
            resource_type="raw",
            folder=_folder_path(folder),
            public_id=public_id,
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            invalidate=True,
            context={
                "app": "myportfoliohub",
                "category": "billing-proof",
                "content_type": "application/pdf",
            },
        )
        secure_url = str(result.get("secure_url") or "").strip()
        if secure_url.startswith("https://"):
            logger.info("Uploaded billing proof PDF to Cloudinary public_id=%s", result.get("public_id"))
            return secure_url
        return None
    except Exception:
        logger.exception("Cloudinary billing proof upload failed")
        return None
    finally:
        try:
            file.stream.seek(0)
        except Exception:
            pass

def is_cloudinary_url(url: str | None) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "res.cloudinary.com"


def _public_id_from_url(url: str) -> str | None:
    """Extract a Cloudinary public ID from this app's direct delivery URL."""
    if not is_cloudinary_url(url):
        return None
    parsed = urlparse(url)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    try:
        upload_index = segments.index("upload")
    except ValueError:
        return None

    tail = segments[upload_index + 1 :]
    # Direct URLs commonly include a version segment such as v1720000000.
    if tail and re.fullmatch(r"v\d+", tail[0]):
        tail = tail[1:]

    configured_root = _folder_path("").split("/", 1)[0]
    if configured_root and configured_root in tail:
        tail = tail[tail.index(configured_root) :]

    if not tail:
        return None
    last = tail[-1]
    if "." in last:
        tail[-1] = last.rsplit(".", 1)[0]
    public_id = "/".join(part for part in tail if part)
    root = re.sub(r"[^a-zA-Z0-9_/-]+", "-", _config_value("CLOUDINARY_FOLDER_ROOT", "myportfoliohub")).strip("/")
    if root and not (public_id == root or public_id.startswith(root + "/")):
        logger.warning("Refusing to delete Cloudinary asset outside configured root: %s", public_id)
        return None
    return public_id or None


def delete_image(url: str) -> bool:
    """Delete a Cloudinary image by its stored HTTPS URL."""
    public_id = _public_id_from_url(url)
    if not public_id:
        return False
    try:
        _configure_cloudinary()
        import cloudinary.uploader

        result = cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True)
        status = str((result or {}).get("result") or "").lower()
        if status in {"ok", "not found"}:
            logger.info("Deleted Cloudinary image public_id=%s result=%s", public_id, status)
            return True
        logger.warning("Cloudinary delete returned %s for public_id=%s", status or "unknown", public_id)
        return False
    except Exception:
        logger.exception("Cloudinary image delete failed for public_id=%s", public_id)
        return False
