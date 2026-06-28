"""
app/security.py — Security utilities and hardening

v3.9 Production Hardening:
  • CRITICAL FIX #1: ALLOWED_BILLING_PROOF_EXTENSIONS restricted to jpg/jpeg/png/webp/pdf only
  • CRITICAL FIX #2: BLOCKED_EXTENSIONS expanded with php/html/htm/svg/xml
  • CRITICAL FIX #3: MAX_FILE_SIZE_MB aligned to 10 MB (matches MAX_CONTENT_LENGTH)
  • HIGH PRIORITY FIX #4: Magic-byte validation added (validate_magic_bytes)

Includes:
  • Password policy validation (strength, complexity)
  • Account lockout & failed login tracking
  • File upload security (extension + magic-byte)
  • Rate limit helpers
  • Audit logging for security events
"""

import re
import secrets
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _normalize_to_utc(value):
    """Return a UTC-aware datetime for comparison."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ── Password Policy ────────────────────────────────────────────────────────

class PasswordPolicy:
    """
    SaaS password policy requirements.
    All passwords must meet these minimum criteria.
    """
    MIN_LENGTH = 12
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_NUMBERS = True
    REQUIRE_SPECIAL = True

    BANNED_PASSWORDS = {
        'password', '12345678', 'qwerty', 'letmein', 'welcome',
        'admin', 'test', 'demo', 'temp', 'changeme',
        'password123', 'admin123', 'test123', 'pass', 'pass123',
    }

    @classmethod
    def validate(cls, password: str) -> tuple[bool, str]:
        if not password:
            return False, "Password is required."

        if len(password) < cls.MIN_LENGTH:
            return False, f"Password must be at least {cls.MIN_LENGTH} characters long."

        if password.lower() in cls.BANNED_PASSWORDS:
            return False, "Password is too common. Please choose a stronger password."

        if cls.REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
            return False, "Password must contain at least one uppercase letter."

        if cls.REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
            return False, "Password must contain at least one lowercase letter."

        if cls.REQUIRE_NUMBERS and not re.search(r'[0-9]', password):
            return False, "Password must contain at least one number."

        if cls.REQUIRE_SPECIAL and not re.search(r'[!@#$%^&*()_+=\-\[\]{};:\'",.<>?/\\|`~]', password):
            return False, "Password must contain at least one special character (!@#$%^&*, etc)."

        return True, ""

    @classmethod
    def score_strength(cls, password: str) -> str:
        score = 0
        if len(password) >= cls.MIN_LENGTH:
            score += 1
        if len(password) >= 16:
            score += 1
        if re.search(r'[A-Z]', password):
            score += 1
        if re.search(r'[a-z]', password):
            score += 1
        if re.search(r'[0-9]', password):
            score += 1
        if re.search(r'[!@#$%^&*()_+=\-\[\]{};:\'",.<>?/\\|`~]', password):
            score += 1

        if score <= 2:
            return 'weak'
        elif score <= 3:
            return 'fair'
        elif score <= 4:
            return 'good'
        else:
            return 'strong'


# ── Account Lockout & Failed Login Tracking ───────────────────────────────

class AccountLockout:
    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 15
    ATTEMPT_RESET_MINUTES = 30

    @classmethod
    def is_locked(cls, user, db) -> bool:

        if not user.failed_login_attempts:
            return False

        if user.failed_login_attempts < cls.MAX_FAILED_ATTEMPTS:
            return False

        if not user.last_failed_login_at:
            return False


        last_failed_login_at = _normalize_to_utc(
            user.last_failed_login_at
        )

        lockout_end = (
            last_failed_login_at +
            timedelta(minutes=cls.LOCKOUT_DURATION_MINUTES)
        )


        now = datetime.now(timezone.utc)


        # cooldown finished
        if now >= lockout_end:

            user.failed_login_attempts = 0
            user.last_failed_login_at = None

            db.session.commit()

            logger.info(
                "Account lockout expired for %s",
                user.username
            )

            return False


        return True

    @classmethod
    def get_lockout_remaining(cls, user, db) -> int:
        if not cls.is_locked(user, db):
            return 0
        lockout_end = (_normalize_to_utc(user.last_failed_login_at) + timedelta(minutes=cls.LOCKOUT_DURATION_MINUTES))
        remaining = (lockout_end - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining))

    @classmethod
    def record_failed_attempt(cls, user, db) -> None:
        now = datetime.now(timezone.utc)
        if user.last_failed_login_at:
            last_failed_login_at = _normalize_to_utc(user.last_failed_login_at)
            reset_threshold = now - timedelta(minutes=cls.ATTEMPT_RESET_MINUTES)
            if last_failed_login_at < reset_threshold:
                user.failed_login_attempts = 0
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        user.last_failed_login_at = now
        db.session.commit()
        logger.warning(
            'Failed login attempt for user %s (attempt %d/%d)',
            user.username,
            user.failed_login_attempts,
            cls.MAX_FAILED_ATTEMPTS,
        )

    @classmethod
    def clear_failed_attempts(cls, user, db) -> None:
        user.failed_login_attempts = 0
        user.last_failed_login_at = None
        db.session.commit()


# ── Magic-Byte Validation ─────────────────────────────────────────────────
#
# CRITICAL FIX #4: Verify file content matches declared extension.
# Do NOT rely solely on MIME headers — they are user-supplied and trivially spoofed.

_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    # JPEG: always starts with FF D8 FF
    'jpg':  (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
    # PNG: 8-byte signature
    'png':  (b'\x89PNG\r\n\x1a\n',),
    # WEBP: "RIFF" at byte 0, "WEBP" at byte 8
    # We check both markers to be precise.
    'webp': (b'RIFF',),
    # PDF: always starts with %PDF
    'pdf':  (b'%PDF',),
}

# How many leading bytes to read for signature matching
_MAGIC_READ_BYTES = 12


def validate_magic_bytes(file_bytes: bytes, ext: str) -> tuple[bool, str]:
    """
    Verify the leading magic bytes of *file_bytes* match the expected
    signature for *ext* (lowercased extension without leading dot).

    Returns (True, '') on success, (False, human-friendly message) on mismatch.

    Extensions not in _MAGIC_SIGNATURES are treated as valid (no rule = no block).
    For WEBP we additionally check that bytes 8:12 equal b'WEBP'.
    """
    ext = ext.lower().lstrip('.')
    signatures = _MAGIC_SIGNATURES.get(ext)
    if not signatures:
        # No rule defined — allow
        return True, ''

    header = file_bytes[:_MAGIC_READ_BYTES]

    for sig in signatures:
        if header.startswith(sig):
            # Extra WEBP check: bytes 8-12 must be "WEBP"
            if ext == 'webp':
                if len(file_bytes) >= 12 and file_bytes[8:12] == b'WEBP':
                    return True, ''
                return False, 'The uploaded file does not appear to be a valid WEBP image.'
            return True, ''

    return (
        False,
        f'The file content does not match its extension (.{ext}). '
        f'Please upload a genuine {ext.upper()} file.',
    )


# ── File Upload Security ───────────────────────────────────────────────────

class FileUploadPolicy:
    """
    File upload security constraints.

    v3.9 changes:
      • ALLOWED_BILLING_PROOF_EXTENSIONS: restricted to image+pdf only
        (removed zip, doc, docx, xls, xlsx, txt)
      • BLOCKED_EXTENSIONS: added php, html, htm, svg, xml
      • MAX_FILE_SIZE_MB: reduced from 50 → 10 to match MAX_CONTENT_LENGTH=10MB
      • validate_billing_proof_upload(): new dedicated method for billing proofs
    """
    MAX_IMAGE_SIZE_MB = 10
    MAX_FILE_SIZE_MB  = 10   # FIX #3: was 50, now aligned with MAX_CONTENT_LENGTH

    ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}

    # FIX #1: Billing proofs — images + PDF only. NO zip/office/text.
    ALLOWED_BILLING_PROOF_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'pdf'}

    # General document uploads (non-billing) retain broader rules.
    ALLOWED_DOCUMENT_EXTENSIONS = {
        'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt',
        'jpg', 'jpeg', 'png', 'webp',
    }

    # FIX #2: Expanded blocked list — added web-executable / markup types
    BLOCKED_EXTENSIONS = {
        # Executables / scripts
        'exe', 'bat', 'cmd', 'sh', 'ps1', 'scr',
        'vbs', 'js', 'jar', 'class', 'pyc',
        'dll', 'so', 'dylib', 'app', 'bin',
        'msi', 'dmg', 'pkg',
        # Web-executable / markup (FIX #2)
        'php', 'html', 'htm', 'svg', 'xml',
    }

    @classmethod
    def _check_common(cls, filename: str, file_size_bytes: int, max_mb: int) -> tuple[bool, str, str]:
        """
        Shared pre-checks: empty name, size, extension present, blocked.
        Returns (ok, error_message, ext).
        """
        if not filename:
            return False, 'Filename is required.', ''

        max_bytes = max_mb * 1024 * 1024
        if file_size_bytes > max_bytes:
            return False, f'File must be smaller than {max_mb} MB.', ''

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if not ext:
            return False, 'File must have an extension.', ''

        if ext in cls.BLOCKED_EXTENSIONS:
            return False, f"File type '.{ext}' is not permitted.", ext

        return True, '', ext

    @classmethod
    def validate_image_upload(cls, filename: str, file_size_bytes: int) -> tuple[bool, str]:
        ok, err, ext = cls._check_common(filename, file_size_bytes, cls.MAX_IMAGE_SIZE_MB)
        if not ok:
            return False, err
        if ext not in cls.ALLOWED_IMAGE_EXTENSIONS:
            return False, f"Only {', '.join(sorted(cls.ALLOWED_IMAGE_EXTENSIONS))} images are allowed."
        return True, ''

    @classmethod
    def validate_document_upload(cls, filename: str, file_size_bytes: int) -> tuple[bool, str]:
        ok, err, ext = cls._check_common(filename, file_size_bytes, cls.MAX_FILE_SIZE_MB)
        if not ok:
            return False, err
        if ext not in cls.ALLOWED_DOCUMENT_EXTENSIONS:
            return False, f"Only {', '.join(sorted(cls.ALLOWED_DOCUMENT_EXTENSIONS))} documents are allowed."
        return True, ''

    @classmethod
    def validate_billing_proof_upload(
        cls,
        filename: str,
        file_size_bytes: int,
        file_bytes: bytes | None = None,
    ) -> tuple[bool, str]:
        """
        FIX #1 + FIX #4 — Stricter billing proof validation.

        Allowed: jpg, jpeg, png, webp, pdf only.
        Rejected: zip, doc, docx, xls, xlsx, txt, svg, php, and everything
                  in BLOCKED_EXTENSIONS.
        Magic-byte check applied when file_bytes is provided.
        """
        ok, err, ext = cls._check_common(filename, file_size_bytes, cls.MAX_FILE_SIZE_MB)
        if not ok:
            return False, err

        if ext not in cls.ALLOWED_BILLING_PROOF_EXTENSIONS:
            allowed_str = ', '.join(sorted(cls.ALLOWED_BILLING_PROOF_EXTENSIONS))
            return False, (
                f"Billing proofs must be an image (JPG, PNG, WEBP) or PDF. "
                f"'.{ext}' files are not accepted."
            )

        # FIX #4: Magic-byte validation
        if file_bytes is not None:
            ok_magic, magic_err = validate_magic_bytes(file_bytes, ext)
            if not ok_magic:
                return False, magic_err

        return True, ''


# ── Security Audit Logging ────────────────────────────────────────────────

def log_security_event(event_type: str, user=None, description: str = '', severity: str = 'info') -> None:
    username = user.username if user else 'anonymous'
    log_msg = f"[SECURITY] {event_type.upper()} | User: {username} | {description}"
    if severity == 'critical':
        logger.critical(log_msg)
    elif severity == 'warning':
        logger.warning(log_msg)
    else:
        logger.info(log_msg)


def require_password_reset_on_next_login(user, db, reason: str = 'Security policy') -> None:
    user.require_password_reset = True
    db.session.commit()
    log_security_event(
        'password_reset_required', user,
        f"Reason: {reason}", 'warning',
    )


# ── 2FA Token Validation ───────────────────────────────────────────────────

def generate_secure_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def validate_totp_token(totp_obj, token: str) -> bool:
    try:
        return totp_obj.verify(token, valid_window=1)
    except Exception:
        return False
