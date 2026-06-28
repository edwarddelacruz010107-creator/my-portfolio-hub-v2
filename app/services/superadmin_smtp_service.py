"""
app/services/superadmin_smtp_service.py — Consolidated Superadmin SMTP (v6.6)

CONSOLIDATION NOTE (v6.6 audit):
    This module previously duplicated smtp_service.py with a different env-var
    prefix (SUPERADMIN_SMTP_*). The duplication created a silent divergence risk:
    a bug fixed in one module would not propagate to the other.

    Resolution: smtp_service.py is the canonical SMTP transport. It already
    reads SUPERADMIN_SMTP_* vars via its _config() resolver when present, and
    falls back to SMTP_* vars for backward compatibility. This module is now a
    thin re-export so any existing callers (superadmin_email_service._legacy_send,
    etc.) continue to work without changes.

    Do not add logic here. Put it in smtp_service.py.

ENV VARS (resolved by smtp_service._config()):
    SUPERADMIN_SMTP_HOST       → falls back to SMTP_HOST
    SUPERADMIN_SMTP_PORT       → falls back to SMTP_PORT
    SUPERADMIN_SMTP_USERNAME   → falls back to SMTP_USERNAME
    SUPERADMIN_SMTP_PASSWORD   → falls back to SMTP_PASSWORD
    SUPERADMIN_FROM_EMAIL      → falls back to SMTP_FROM_EMAIL
    SUPERADMIN_FROM_NAME       → falls back to SMTP_FROM_NAME
"""
from __future__ import annotations

# Re-export the canonical implementations so callers need no import changes.
from app.services.smtp_service import (          # noqa: F401  (intentional re-export)
    send_email,
    send_superadmin_otp,
    send_security_alert,
    health_check,
    validate_configuration,
)

__all__ = [
    'send_email',
    'send_superadmin_otp',
    'send_security_alert',
    'health_check',
    'validate_configuration',
]
