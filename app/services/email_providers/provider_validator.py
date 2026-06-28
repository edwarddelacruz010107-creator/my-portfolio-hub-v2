"""
app/services/email_providers/provider_validator.py

Centralized validation entry points.  All validation logic lives here —
routes delegate to these functions, never duplicate logic inline.

Each validate_* function:
  - Accepts decrypted credentials (never raw encrypted blobs)
  - Returns (bool, str) — (success, human-readable message)
  - Never swallows exceptions
  - Logs structured diagnostics
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("email_provider.validator")


def validate_mailersend(
    api_key: str,
    sender_email: Optional[str] = None,
    validate_sender_domain: bool = False,
) -> tuple[bool, str]:
    """
    Validate a MailerSend API key.
    Optionally also validate the sender domain (requires sender_email).
    Returns (True, msg) or (False, error).
    """
    from .mailersend_provider import MailerSendProvider

    if not api_key:
        return False, "No MailerSend API key provided."

    provider = MailerSendProvider(
        api_key=api_key,
        sender_email=sender_email or "check@validation.only",
    )

    ok, msg = provider.validate_credentials()
    if not ok:
        return False, msg

    if validate_sender_domain and sender_email and "@" in sender_email:
        p2 = MailerSendProvider(api_key=api_key, sender_email=sender_email)
        s_ok, s_msg = p2.validate_sender()
        if not s_ok:
            return True, f"API key valid, but sender domain issue: {s_msg}"

    return True, msg


def validate_resend(api_key: str) -> tuple[bool, str]:
    """
    Validate a Resend API key.
    Returns (True, msg) or (False, error).
    """
    from .resend_provider import ResendProvider

    if not api_key:
        return False, "No Resend API key provided."

    provider = ResendProvider(
        api_key=api_key,
        sender_email="check@validation.only",
    )
    return provider.validate_credentials()


def validate_smtp(
    host: str,
    port: int,
    username: str,
    password: str,
    sender_email: str,
    encryption: str = "tls",
) -> tuple[bool, str]:
    """
    Validate SMTP credentials by opening a real connection and authenticating.
    Returns (True, msg) or (False, error).
    """
    from .smtp_provider import SMTPProvider

    if not host:
        return False, "SMTP host is required."
    if not username:
        return False, "SMTP username is required."
    if not password:
        return False, "SMTP password is required."
    if not sender_email:
        return False, "SMTP sender email is required."

    provider = SMTPProvider(
        host=host,
        port=port,
        username=username,
        password=password,
        sender_email=sender_email,
        encryption=encryption,
    )
    return provider.validate_credentials()
