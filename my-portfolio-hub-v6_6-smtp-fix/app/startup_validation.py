"""
app/startup_validation.py — Environment variable validation at startup.

CRIT-01 / CRIT-02 FIX:
  Fails application startup immediately if required secrets are missing
  or contain placeholder/insecure values.

Usage (in create_app(), after app.config.from_object()):
    from app.startup_validation import validate_startup_env
    validate_startup_env(app)
"""
import os
import sys
import logging

logger = logging.getLogger(__name__)

# Minimum acceptable entropy for SECRET_KEY (chars, not bits)
_SECRET_KEY_MIN_LEN = 32

# Strings that indicate a placeholder was accidentally left in
_PLACEHOLDER_FRAGMENTS = (
    "REPLACE_WITH",
    "<REQUIRED",
    "changeme",
    "your_secret",
    "example",
    "placeholder",
)

_REQUIRED_ALWAYS = [
    "SECRET_KEY",
    "FERNET_KEY",
]

_REQUIRED_PRODUCTION = [
    "CORE_DATABASE_URL",
    "TENANT_DATABASE_URL",
]

_REQUIRED_IF_PAYMONGO = [
    "PAYMONGO_SECRET_KEY",
    "PAYMONGO_PUBLIC_KEY",
    "PAYMONGO_WEBHOOK_SECRET",
]

_REQUIRED_IF_MAILERSEND = [
    "MAILERSEND_API_KEY",
    "MAILERSEND_FROM_EMAIL",
]


def _has_placeholder(value: str) -> bool:
    """Return True if value contains a known placeholder fragment."""
    v_lower = value.lower()
    return any(p.lower() in v_lower for p in _PLACEHOLDER_FRAGMENTS)


def _check_secret_key(value: str) -> list[str]:
    """Validate SECRET_KEY quality. Returns list of error strings."""
    errors = []
    if len(value) < _SECRET_KEY_MIN_LEN:
        errors.append(
            f"SECRET_KEY is too short ({len(value)} chars; minimum {_SECRET_KEY_MIN_LEN})."
        )
    if _has_placeholder(value):
        errors.append(
            "SECRET_KEY contains placeholder text — it was not replaced with a real secret."
        )
    # Detect the specific corrupted key from CRIT-02
    if "<REQUIRED" in value or "generate a strong" in value.lower():
        errors.append(
            "SECRET_KEY contains audit-marker text from CRIT-02. "
            "Generate a clean key: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )
    return errors


def _check_paymongo_webhook_secret(value: str) -> list[str]:
    """Validate PAYMONGO_WEBHOOK_SECRET is not a URL (CRIT-03)."""
    errors = []
    if value.startswith("http://") or value.startswith("https://"):
        errors.append(
            "PAYMONGO_WEBHOOK_SECRET is set to a URL, not an HMAC signing secret (CRIT-03). "
            "Copy the signing secret from the PayMongo webhook dashboard."
        )
    if _has_placeholder(value):
        errors.append("PAYMONGO_WEBHOOK_SECRET contains placeholder text.")
    return errors


def validate_startup_env(app) -> None:
    """
    Validate environment variables at startup.

    Raises SystemExit(1) on CRITICAL failures in production.
    Logs warnings in development.
    """
    is_production = not app.debug and os.environ.get("FLASK_ENV") != "development"
    errors: list[str] = []
    warnings: list[str] = []

    # ── Always-required secrets ───────────────────────────────────────────────
    for key in _REQUIRED_ALWAYS:
        val = os.environ.get(key) or app.config.get(key, "")
        if not val:
            errors.append(f"Missing required environment variable: {key}")
        elif key == "SECRET_KEY":
            errors.extend(_check_secret_key(str(val)))
        elif _has_placeholder(str(val)):
            errors.append(f"{key} contains placeholder text — replace with real value.")

    # ── Production-only requirements ──────────────────────────────────────────
    if is_production:
        for key in _REQUIRED_PRODUCTION:
            val = os.environ.get(key) or app.config.get(key, "")
            if not val:
                errors.append(f"Missing required production variable: {key}")
            elif _has_placeholder(str(val)):
                errors.append(f"{key} contains placeholder text.")

        # MailerSend
        for key in _REQUIRED_IF_MAILERSEND:
            val = os.environ.get(key, "")
            if not val:
                warnings.append(f"MailerSend key not set: {key} — email delivery will fail.")
            elif _has_placeholder(val):
                errors.append(f"{key} contains placeholder text.")

    # ── PayMongo conditional ──────────────────────────────────────────────────
    if os.environ.get("PAYMONGO_ENABLED", "").lower() in ("true", "1", "yes"):
        for key in _REQUIRED_IF_PAYMONGO:
            val = os.environ.get(key, "")
            if not val:
                errors.append(f"PAYMONGO_ENABLED=true but {key} is not set.")
            elif key == "PAYMONGO_WEBHOOK_SECRET":
                errors.extend(_check_paymongo_webhook_secret(val))
            elif _has_placeholder(val):
                errors.append(f"{key} contains placeholder text.")

    # ── Redis recommendation ──────────────────────────────────────────────────
    if is_production and not os.environ.get("REDIS_URL"):
        warnings.append(
            "REDIS_URL not set in production — rate limiting and caching will use "
            "in-memory fallback (not shared across workers)."
        )

    # ── Report ────────────────────────────────────────────────────────────────
    for w in warnings:
        logger.warning("⚠  ENV WARNING: %s", w)

    if errors:
        logger.critical("=" * 70)
        logger.critical("STARTUP VALIDATION FAILED — %d error(s):", len(errors))
        for i, err in enumerate(errors, 1):
            logger.critical("  %d. %s", i, err)
        logger.critical("=" * 70)
        logger.critical(
            "Fix the above errors and restart. "
            "See .env.example for required variable format."
        )
        if is_production:
            sys.exit(1)
        else:
            logger.warning("Development mode — startup validation errors are non-fatal.")
