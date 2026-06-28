"""
app/services/email_providers — Centralized provider architecture.

Public API:
    dispatch_email(...)     — send via provider chain with failover
    send_test_email(...)    — test send with structured diagnostics
    get_active_providers()  — list of configured active providers
    get_provider_status()   — dict of all provider states (for UI badges)
    validate_mailersend()   — validate MailerSend API key
    validate_resend()       — validate Resend API key
    validate_smtp()         — validate SMTP credentials
"""
from .provider_dispatcher import dispatch_email, send_test_email
from .provider_registry import get_active_providers, get_provider_status
from .provider_validator import validate_mailersend, validate_resend, validate_smtp

__all__ = [
    "dispatch_email",
    "send_test_email",
    "get_active_providers",
    "get_provider_status",
    "validate_mailersend",
    "validate_resend",
    "validate_smtp",
]
