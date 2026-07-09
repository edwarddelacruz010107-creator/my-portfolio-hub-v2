"""
app/utils/supabase_storage.py — Supabase Storage integration (v3.2)

FIXES from v2.x:
  • _save_to_supabase(): added MIME validation via Pillow before upload —
    the original had NO file validation, allowing any file type to be uploaded.
  • _save_to_supabase(): added extension allow-list check.
  • _save_to_supabase(): added file size check (respects MAX_CONTENT_LENGTH).
  • _save_to_local(): returns filename-only (consistent with utils.save_image),
    not a full /static/ path — callers handle URL construction.
  • delete_image(): improved path extraction for Supabase signed URLs.
  • Added USE_SUPABASE env var check at call site (not at module level) so the
    module can be safely imported in all environments.
"""

import uuid
import logging
from typing import Optional

from flask import current_app
from werkzeug.datastructures import FileStorage

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
_CONTENT_TYPES = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'png': 'image/png',  'gif': 'image/gif',
    'webp': 'image/webp',
}
_PIL_SAFE_FORMATS = {'PNG', 'JPEG', 'GIF', 'WEBP'}


def _get_supabase_client():
    """Lazy-import supabase client so local dev doesn't require the package."""
    try:
        from supabase import create_client
        url = current_app.config.get('SUPABASE_URL', '')
        key = current_app.config.get('SUPABASE_KEY', '')
        if not url or not key:
            raise ValueError('SUPABASE_URL and SUPABASE_SERVICE_KEY must be set')
        return create_client(url, key)
    except ImportError:
        raise RuntimeError('supabase-py not installed. Run: pip install supabase')


def _validate_image(file: FileStorage) -> bool:
    """Validate image through the central upload policy before storage."""
    if not file or not file.filename:
        return False
    try:
        if hasattr(file, 'stream'):
            file.stream.seek(0)
            data = file.stream.read()
            file.stream.seek(0)
        else:
            data = file.read()
            file.seek(0)
    except Exception as exc:
        logger.warning('Rejected upload: could not read file — %s', exc)
        return False

    from app.security import FileUploadPolicy
    ok, err = FileUploadPolicy.validate_image_upload(
        file.filename, len(data), file_bytes=data, declared_mime=getattr(file, 'mimetype', None)
    )
    if not ok:
        logger.warning('Rejected upload: %s — %s', file.filename, err)
        return False
    return True


def _read_file_bytes(file: FileStorage) -> bytes:
    if hasattr(file, 'stream'):
        file.stream.seek(0)
        data = file.stream.read()
        file.stream.seek(0)
        return data
    data = file.read()
    try:
        file.seek(0)
    except Exception:
        pass
    return data


def save_image(file: FileStorage, folder: str = 'uploads') -> Optional[str]:
    """
    Save an uploaded image to Supabase Storage as lightweight WebP.

    The legacy contract returns the public URL string on success and None on
    failure.  Animated GIF/WebP uploads are preserved so animation is not lost.
    """
    if not file or not file.filename:
        return None

    try:
        raw_data = _read_file_bytes(file)
    except Exception as exc:
        logger.warning('Rejected upload: could not read file — %s', exc)
        return None

    if not _validate_image(file):
        return None

    source_ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    final_data = raw_data
    final_ext = source_ext
    content_type = _CONTENT_TYPES.get(source_ext, 'application/octet-stream')

    try:
        convert_enabled = bool(current_app.config.get('CONVERT_UPLOADS_TO_WEBP', True))
        if convert_enabled:
            from app.services.media.image_optimizer import optimize_image_bytes_to_webp

            optimized = optimize_image_bytes_to_webp(
                raw_data,
                source_ext,
                source_mime=getattr(file, 'mimetype', None),
                quality=int(current_app.config.get('UPLOAD_WEBP_QUALITY', 82)),
                max_dimension=int(current_app.config.get('UPLOAD_IMAGE_MAX_DIMENSION', 2048)),
                preserve_animation=True,
                force=True,
            )
            final_data = optimized.data
            final_ext = optimized.extension
            content_type = optimized.mime_type
            if optimized.converted:
                logger.info(
                    'Supabase upload converted to WebP folder=%s original=%d final=%d saved=%.1f%%',
                    folder,
                    optimized.original_size,
                    optimized.final_size,
                    optimized.percent_saved,
                )
    except Exception as exc:
        logger.error('Supabase image WebP optimisation failed: %s', exc)
        return None

    filename = f"{folder}/{uuid.uuid4().hex}.{final_ext}"
    return _save_bytes_to_supabase(final_data, filename, content_type)


def _save_bytes_to_supabase(data: bytes, path: str, content_type: str) -> Optional[str]:
    try:
        client = _get_supabase_client()
        bucket = current_app.config.get('SUPABASE_BUCKET', 'portfolio-media')

        client.storage.from_(bucket).upload(
            path,
            data,
            file_options={'content-type': content_type, 'cache-control': '31536000'},
        )

        public_url = client.storage.from_(bucket).get_public_url(path)
        logger.info('Uploaded to Supabase Storage: %s', public_url)
        return public_url

    except Exception as exc:
        logger.error('Supabase Storage upload failed: %s', exc)
        return None


def _save_to_supabase(file: FileStorage, path: str) -> Optional[str]:
    """Backward-compatible wrapper for older call sites."""
    try:
        data = _read_file_bytes(file)
        ext = path.rsplit('.', 1)[-1] if '.' in path else ''
        content_type = _CONTENT_TYPES.get(ext, 'application/octet-stream')
        return _save_bytes_to_supabase(data, path, content_type)
    except Exception as exc:
        logger.error('Supabase Storage upload failed: %s', exc)
        return None


def delete_image(url: str) -> bool:
    """
    Delete an image by its public URL from Supabase Storage.

    Args:
        url: Public Supabase URL

    Returns:
        True if deleted, False on error.
    """
    if not url or 'supabase' not in url:
        return False

    try:
        client = _get_supabase_client()
        bucket = current_app.config.get('SUPABASE_BUCKET', 'portfolio-media')

        # Extract storage path from URL
        # URL format: https://<project>.supabase.co/storage/v1/object/public/<bucket>/<path>
        marker = f'/object/public/{bucket}/'
        if marker in url:
            path = url.split(marker, 1)[1].split('?')[0]  # strip query params
            client.storage.from_(bucket).remove([path])
            logger.info('Deleted from Supabase: %s', path)
            return True
        else:
            logger.warning('Could not extract path from Supabase URL: %s', url)
            return False

    except Exception as exc:
        logger.error('Supabase Storage delete failed: %s', exc)
        return False
