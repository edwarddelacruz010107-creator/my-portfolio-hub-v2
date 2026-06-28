"""
app/services/email_providers/base_provider.py

Abstract base contract that every email provider MUST implement.
All providers are stateless — credentials are resolved fresh on every call.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class EmailSendResult:
    """Structured result from an email send attempt."""

    def __init__(
        self,
        success: bool,
        provider: str,
        message_id: str = "",
        http_status: Optional[int] = None,
        error: str = "",
        delivery_mode: str = "primary",
        sender_verified: bool = True,
        latency_ms: Optional[float] = None,
    ):
        self.success = success
        self.provider = provider
        self.message_id = message_id
        self.http_status = http_status
        self.error = error
        self.delivery_mode = delivery_mode
        self.sender_verified = sender_verified
        self.latency_ms = latency_ms

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "provider": self.provider,
            "message_id": self.message_id,
            "http_status": self.http_status,
            "error": self.error,
            "delivery_mode": self.delivery_mode,
            "sender_verified": self.sender_verified,
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms else None,
        }


# Errors that should trigger failover to the next provider
TRANSIENT_ERRORS = {
    "timeout",
    "connection",
    "rate_limit",
    "server_error",
    "503",
    "502",
    "500",
    "429",
}

# Errors that are permanent — DO NOT failover
PERMANENT_ERRORS = {
    "auth",
    "authentication",
    "unauthorized",
    "invalid_key",
    "invalid_sender",
    "unverified_domain",
    "bad_request",
    "malformed",
}


def is_transient_error(error_msg: str) -> bool:
    """Return True if error_msg describes a transient failure (retry/failover allowed)."""
    msg_lower = error_msg.lower()
    # Never failover on permanent errors regardless of other matches
    for perm in PERMANENT_ERRORS:
        if perm in msg_lower:
            return False
    for trans in TRANSIENT_ERRORS:
        if trans in msg_lower:
            return True
    return False


class BaseEmailProvider(ABC):
    """
    Abstract base for all email providers.

    Every concrete provider implements this contract.  The dispatcher
    calls the methods in this order:
        1. is_configured()           — skip entirely if False
        2. validate_credentials()    — validate API key / SMTP auth
        3. validate_sender()         — validate sender domain/email
        4. send_email(...)           — actual delivery
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier: 'mailersend' | 'smtp' | 'resend'"""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when all mandatory credentials are present."""

    @abstractmethod
    def validate_credentials(self) -> tuple[bool, str]:
        """
        Validate API key / SMTP credentials against the remote service.
        Returns (True, success_msg) or (False, error_msg).
        MUST NOT swallow exceptions — let the dispatcher handle them.
        """

    @abstractmethod
    def validate_sender(self) -> tuple[bool, str]:
        """
        Validate the configured sender email/domain.
        Returns (True, success_msg) or (False, error_msg).
        """

    @abstractmethod
    def send_email(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        to_name: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> EmailSendResult:
        """
        Deliver an email.
        Returns EmailSendResult with success=True on delivery.
        MUST NOT return success=True on uncertain delivery.
        """

    def send_test_email(self, to: str) -> EmailSendResult:
        """Send a standardised test email. Override for provider-specific test."""
        html = (
            "<div style='font-family:sans-serif;max-width:520px;margin:auto;"
            "padding:24px;border:1px solid #e5e7eb;border-radius:8px;'>"
            "<h2 style='color:#1f2937;'>✅ Email Delivery Test</h2>"
            "<p>This test email confirms your <strong>"
            f"{self.provider_name}</strong> provider is configured correctly.</p>"
            "<p style='color:#6b7280;font-size:.85rem;'>Sent by Portfolio CMS "
            "Email Settings → Send Test</p></div>"
        )
        text = (
            f"Email Delivery Test\n\n"
            f"This test email confirms your {self.provider_name} provider "
            f"is configured correctly.\n\nSent by Portfolio CMS."
        )
        return self.send_email(
            to=to,
            subject="[Portfolio CMS] Email Delivery Test",
            html=html,
            text=text,
        )

    @abstractmethod
    def health_check(self) -> dict:
        """
        Return a status dict without sending real email.
        Keys: provider, configured, status, latency_ms (optional).
        """
