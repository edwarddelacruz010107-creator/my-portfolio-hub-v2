"""Private storage and retrieval for manual-payment proof files.

Payment QR codes are public checkout assets. Customer payment proofs are not.
This module keeps that boundary explicit and provides compatibility reads for
legacy proof references until ``flask migrate-private-billing-proofs --apply``
has moved them out of public storage.
"""

from __future__ import annotations

import io
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

LOCAL_REFERENCE_PREFIX = "private-local:"
CLOUDINARY_REFERENCE_PREFIX = "cloudinary-auth:"
PRIVATE_PROOF_SUBFOLDER = "billing_proofs"

_ALLOWED_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "pdf"})
_MIME_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


class PrivateProofStorageError(RuntimeError):
    """Raised when a proof cannot be safely stored or retrieved."""


@dataclass(frozen=True)
class BillingProofContent:
    data: bytes
    mimetype: str
    download_name: str


def _configured_provider() -> str:
    provider = str(current_app.config.get("STORAGE_PROVIDER") or "").strip().lower()
    if provider:
        return provider
    if bool(current_app.config.get("USE_CLOUDINARY_STORAGE", False)):
        return "cloudinary"
    if bool(current_app.config.get("USE_SUPABASE_STORAGE", False)):
        return "supabase"
    return "local"


def private_proof_root() -> Path:
    """Return the configured non-public root and reject unsafe placement."""
    configured = str(current_app.config.get("PRIVATE_UPLOAD_FOLDER") or "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        root = (Path(current_app.instance_path) / "private_uploads").resolve()

    public_roots: list[Path] = []
    configured_public = current_app.config.get("UPLOAD_FOLDER")
    if configured_public:
        public_roots.append(Path(configured_public).resolve())
    if current_app.static_folder:
        public_roots.append(Path(current_app.static_folder).resolve())

    for public_root in public_roots:
        try:
            root.relative_to(public_root)
        except ValueError:
            continue
        raise PrivateProofStorageError(
            "PRIVATE_UPLOAD_FOLDER must be outside the public upload/static tree."
        )
    return root


def ensure_private_proof_directory() -> Path:
    """Create the private proof directory with owner-only permissions."""
    if (
        not current_app.testing
        and not current_app.debug
        and _configured_provider() != "cloudinary"
        and not bool(current_app.config.get("PRIVATE_UPLOAD_PERSISTENT", False))
    ):
        raise PrivateProofStorageError(
            "Private billing-proof storage is not persistent. Configure "
            "PRIVATE_UPLOAD_FOLDER on a mounted private disk or use Cloudinary."
        )

    root = private_proof_root()
    directory = (root / PRIVATE_PROOF_SUBFOLDER).resolve()
    try:
        directory.relative_to(root)
    except ValueError as exc:
        raise PrivateProofStorageError("Invalid private proof destination.") from exc
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(root, 0o700)
        os.chmod(directory, 0o700)
    except OSError:
        # Windows and some mounted filesystems do not implement POSIX modes.
        pass
    return directory


def _extension(value: str) -> str:
    path = unquote(urlparse(value).path) if value.startswith(("http://", "https://")) else value
    name = path.rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension if extension in _ALLOWED_EXTENSIONS else ""


def _read_upload(file_storage: FileStorage) -> tuple[bytes, str]:
    filename = secure_filename(getattr(file_storage, "filename", "") or "")
    extension = _extension(filename)
    if not filename or not extension:
        raise PrivateProofStorageError("The billing proof filename is invalid.")
    try:
        file_storage.stream.seek(0)
        data = file_storage.stream.read()
        file_storage.stream.seek(0)
    except Exception as exc:
        raise PrivateProofStorageError("The billing proof could not be read.") from exc

    from app.security import FileUploadPolicy

    ok, error = FileUploadPolicy.validate_billing_proof_upload(
        filename,
        len(data),
        file_bytes=data,
    )
    if not ok:
        raise PrivateProofStorageError(error or "The billing proof failed validation.")
    from app.services.media.malware_scan import require_clean_sensitive_upload
    try:
        require_clean_sensitive_upload(data)
    except ValueError as exc:
        raise PrivateProofStorageError(str(exc)) from exc
    return data, extension


def _write_local_private_bytes(data: bytes, extension: str) -> str:
    directory = ensure_private_proof_directory()
    for _ in range(4):
        filename = f"{secrets.token_hex(24)}.{extension}"
        destination = directory / filename
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return f"{LOCAL_REFERENCE_PREFIX}{filename}"
    raise PrivateProofStorageError("Could not allocate a private proof object key.")


def save_private_billing_proof(file_storage: FileStorage) -> str:
    """Validate and save a proof, returning an opaque database reference."""
    data, extension = _read_upload(file_storage)
    provider = _configured_provider()

    if provider == "cloudinary":
        try:
            from app.utils.cloudinary_storage import save_private_billing_proof as _save_cloudinary

            file_storage.stream.seek(0)
            reference = _save_cloudinary(file_storage, folder="billing-proofs")
            if reference:
                return reference
        except Exception as exc:
            logger.exception("Authenticated Cloudinary billing-proof upload failed")
            raise PrivateProofStorageError(
                "The payment proof could not be stored privately. Please try again."
            ) from exc
        raise PrivateProofStorageError(
            "The payment proof could not be stored privately. Please try again."
        )

    if provider not in {"local", "supabase", ""}:
        raise PrivateProofStorageError(f"Unsupported private storage provider: {provider}.")
    return _write_local_private_bytes(data, extension)


def _safe_local_private_filename(reference: str | None) -> str | None:
    if not isinstance(reference, str) or not reference.startswith(LOCAL_REFERENCE_PREFIX):
        return None
    filename = reference[len(LOCAL_REFERENCE_PREFIX):].strip()
    if not filename or secure_filename(filename) != filename or "/" in filename or "\\" in filename:
        return None
    if not _extension(filename):
        return None
    return filename


def local_private_filename(reference: str | None) -> str | None:
    """Return only the opaque local filename, for private orphan management."""
    return _safe_local_private_filename(reference)


def resolve_private_proof_path(reference: str | None) -> Path | None:
    """Resolve private-local references and read-only legacy local references."""
    filename = _safe_local_private_filename(reference)
    if filename:
        directory = (private_proof_root() / PRIVATE_PROOF_SUBFOLDER).resolve()
        target = (directory / filename).resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            return None
        return target if target.is_file() else None

    if not isinstance(reference, str) or reference.startswith(("http://", "https://")):
        return None
    from app.services.media.upload_storage import normalize_upload_reference, resolve_upload_file

    normalized = normalize_upload_reference(reference, "billing")
    if not normalized or normalized[0] != "billing":
        return None
    return resolve_upload_file("billing", normalized[1])


def _max_proof_bytes() -> int:
    from app.security import FileUploadPolicy

    configured = current_app.config.get("MAX_PRIVATE_PROOF_DOWNLOAD_BYTES")
    if configured:
        return max(1, int(configured))
    return int(FileUploadPolicy.MAX_FILE_SIZE_MB * 1024 * 1024)


def _validated_content(data: bytes, source_name: str) -> BillingProofContent:
    extension = _extension(source_name)
    if not extension:
        raise PrivateProofStorageError("The stored proof type is not allowed.")
    if len(data) > _max_proof_bytes():
        raise PrivateProofStorageError("The stored proof exceeds the allowed size.")

    from app.security import FileUploadPolicy

    validation_name = f"payment-proof.{extension}"
    ok, error = FileUploadPolicy.validate_billing_proof_upload(
        validation_name,
        len(data),
        file_bytes=data,
    )
    if not ok:
        raise PrivateProofStorageError(error or "The stored proof failed validation.")
    return BillingProofContent(
        data=data,
        mimetype=_MIME_BY_EXTENSION[extension],
        download_name=validation_name,
    )


def _fetch_cloudinary_content(url: str, source_name: str) -> BillingProofContent:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "res.cloudinary.com":
        raise PrivateProofStorageError("The remote proof host is not allowed.")

    try:
        response = requests.get(
            url,
            stream=True,
            timeout=(5, 20),
            allow_redirects=False,
        )
        try:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            maximum = _max_proof_bytes()
            declared_length = response.headers.get("Content-Length")
            if declared_length and int(declared_length) > maximum:
                raise PrivateProofStorageError("The stored proof exceeds the allowed size.")
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum:
                    raise PrivateProofStorageError("The stored proof exceeds the allowed size.")
                chunks.append(chunk)
        finally:
            response.close()
    except PrivateProofStorageError:
        raise
    except requests.RequestException as exc:
        raise PrivateProofStorageError("The remote proof is unavailable.") from exc
    return _validated_content(b"".join(chunks), source_name)


def load_private_billing_proof(reference: str | None) -> BillingProofContent:
    """Load a proof for an already-authorized server-side response."""
    if not isinstance(reference, str) or not reference.strip():
        raise PrivateProofStorageError("No payment proof is attached.")
    reference = reference.strip()

    path = resolve_private_proof_path(reference)
    if path:
        maximum = _max_proof_bytes()
        try:
            with path.open("rb") as handle:
                data = handle.read(maximum + 1)
        except OSError as exc:
            raise PrivateProofStorageError("The stored proof is unavailable.") from exc
        return _validated_content(data, path.name)

    if reference.startswith(CLOUDINARY_REFERENCE_PREFIX):
        from app.utils.cloudinary_storage import private_billing_proof_signed_url

        signed_url = private_billing_proof_signed_url(reference)
        if not signed_url:
            raise PrivateProofStorageError("The stored proof reference is invalid.")
        return _fetch_cloudinary_content(signed_url, reference)

    # Compatibility only: old versions stored direct public Cloudinary URLs.
    # The migration command re-uploads these as authenticated assets and deletes
    # the public object. Never fetch arbitrary URLs here (SSRF boundary).
    if reference.startswith(("http://", "https://")):
        from app.utils.cloudinary_storage import is_cloudinary_url

        if not is_cloudinary_url(reference):
            raise PrivateProofStorageError("The legacy proof host is not allowed.")
        return _fetch_cloudinary_content(reference, reference)

    raise PrivateProofStorageError("The stored proof is unavailable.")


def private_proof_exists(reference: str | None) -> bool:
    if not reference:
        return False
    if resolve_private_proof_path(reference):
        return True
    return isinstance(reference, str) and reference.startswith(
        (CLOUDINARY_REFERENCE_PREFIX, "https://res.cloudinary.com/")
    )


def private_proof_size(reference: str | None) -> int | None:
    path = resolve_private_proof_path(reference)
    if not path:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _legacy_reference_is_public_qr(reference: str) -> bool:
    """Protect a public QR asset if an old proof row reused its filename."""
    if reference.startswith((LOCAL_REFERENCE_PREFIX, CLOUDINARY_REFERENCE_PREFIX)):
        return False
    try:
        from app.models.portfolio import PaymentInstruction, PaymentMethod
        from app.services.media.upload_storage import normalize_upload_reference

        if reference.startswith(("http://", "https://")):
            candidates = (reference,)
        else:
            normalized = normalize_upload_reference(reference, "billing")
            if not normalized or normalized[0] != "billing":
                return True
            filename = normalized[1]
            candidates = (
                filename,
                f"billing/{filename}",
                f"uploads/billing/{filename}",
                f"/uploads/billing/{filename}",
                f"static/uploads/billing/{filename}",
                f"/static/uploads/billing/{filename}",
            )

        return bool(
            PaymentMethod.query
            .filter(PaymentMethod.qr_image.in_(candidates))
            .with_entities(PaymentMethod.id)
            .first()
            or PaymentInstruction.query
            .filter(PaymentInstruction.qr_image.in_(candidates))
            .with_entities(PaymentInstruction.id)
            .first()
        )
    except Exception:
        logger.exception("Could not verify whether a legacy proof is also a public QR")
        return True


def delete_private_billing_proof(reference: str | None) -> bool:
    if not reference:
        return True
    reference = reference.strip()
    if _legacy_reference_is_public_qr(reference):
        # Clearing the PaymentSubmission reference is safe; the shared public
        # checkout asset must remain available to its PaymentMethod/Instruction.
        return True
    path = resolve_private_proof_path(reference)
    if path:
        try:
            path.unlink()
            return True
        except OSError:
            logger.exception("Could not delete a local private billing proof")
            return False

    if reference.startswith(CLOUDINARY_REFERENCE_PREFIX):
        from app.utils.cloudinary_storage import delete_private_billing_proof as _delete_cloudinary

        return bool(_delete_cloudinary(reference))

    if reference.startswith(("http://", "https://")):
        from app.utils.cloudinary_storage import delete_image, is_cloudinary_url

        return bool(is_cloudinary_url(reference) and delete_image(reference))
    return False


def import_legacy_billing_proof(reference: str) -> str:
    """Copy a validated legacy proof into private storage without deleting it."""
    content = load_private_billing_proof(reference)
    storage = FileStorage(
        stream=io.BytesIO(content.data),
        filename=content.download_name,
        content_type=content.mimetype,
    )
    return save_private_billing_proof(storage)


def is_legacy_public_billing_proof(filename: str) -> bool:
    """Fail closed when a public billing path names a submitted proof."""
    if not filename or secure_filename(filename) != filename or "/" in filename or "\\" in filename:
        return True
    candidates = (
        filename,
        f"billing/{filename}",
        f"uploads/billing/{filename}",
        f"/uploads/billing/{filename}",
        f"static/uploads/billing/{filename}",
        f"/static/uploads/billing/{filename}",
    )
    try:
        from app.models.portfolio import PaymentSubmission

        return (
            PaymentSubmission.query
            .filter(PaymentSubmission.payment_proof.in_(candidates))
            .with_entities(PaymentSubmission.id)
            .first()
            is not None
        )
    except Exception:
        logger.exception("Could not classify a legacy public billing asset; access denied")
        return True
