"""
app/services/email_providers/provider_dispatcher.py

Central dispatch engine for multi-provider email delivery.

Key behaviors:
  - Reloads provider priority chain fresh on every dispatch call (never stale)
  - Validates active provider before send
  - Detects which provider actually succeeded
  - Returns structured EmailSendResult with full diagnostics
  - Only fails over on TRANSIENT errors (timeout, 5xx, 429, network)
  - DOES NOT fail over on permanent errors (invalid key, bad sender, 401, 422)
  - Logs every attempt with provider, latency, result
  - Prevents silent failures and false success responses

Structured log format (machine-parseable):
  [DISPATCH] event=... provider=... portal=... to=... subject=... result=...
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from .base_provider import BaseEmailProvider, EmailSendResult, is_transient_error
from .provider_registry import get_active_providers

logger = logging.getLogger("email_provider.dispatcher")

# File logger for persistent audit trail
_file_handler_added = False


def _ensure_file_log():
    global _file_handler_added
    if _file_handler_added:
        return
    try:
        import os
        import logging.handlers
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "email_provider.log"),
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
        )
        fh.setLevel(logging.DEBUG)
        fmt = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        fh.setFormatter(fmt)
        logging.getLogger("email_provider").addHandler(fh)
        _file_handler_added = True
    except Exception as exc:
        logger.warning("dispatcher: could not set up file log — %s", exc)


def dispatch_email(
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    portal: str = "superadmin",
    delivery_mode: str = "primary",
) -> EmailSendResult:
    """
    Send email through the active provider chain with automatic failover.

    Failover rules:
      - TRANSIENT errors (timeout, connection, 429, 5xx): try next provider
      - PERMANENT errors (401 invalid key, 422 bad sender, auth failure): STOP, no failover

    Args:
        to:            Recipient email address.
        subject:       Email subject.
        html:          HTML body.
        text:          Plain text body (auto-generated from html if not supplied).
        to_name:       Optional recipient display name.
        reply_to:      Optional Reply-To address.
        portal:        'superadmin' | 'admin' | 'tenant'
        delivery_mode: 'primary' | 'fallback' (informational, set by caller)

    Returns:
        EmailSendResult with success, provider, http_status, message_id, error.
    """
    _ensure_file_log()

    if not text:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

    providers = get_active_providers(portal=portal)

    if not providers:
        msg = (
            "No active email providers configured. "
            "Enable at least one provider in Superadmin → Email Settings."
        )
        logger.error(
            "[DISPATCH] event=no_providers portal=%s to=%s subject=%r",
            portal, to, subject[:60],
        )
        return EmailSendResult(
            success=False,
            provider="none",
            error=msg,
        )

    last_result: Optional[EmailSendResult] = None
    mode = delivery_mode

    for i, provider in enumerate(providers):
        name = provider.provider_name
        is_first = (i == 0)

        logger.info(
            "[DISPATCH] event=attempt provider=%s portal=%s to=%s subject=%r delivery_mode=%s",
            name, portal, to, subject[:60], mode,
        )

        try:
            result = provider.send_email(
                to=to,
                subject=subject,
                html=html,
                text=text,
                to_name=to_name,
                reply_to=reply_to,
            )
        except Exception as exc:
            # Should not happen — providers catch internally — but safety net
            logger.exception(
                "[DISPATCH] event=provider_exception provider=%s to=%s: %s",
                name, to, exc,
            )
            result = EmailSendResult(
                success=False,
                provider=name,
                error=f"{name}: unexpected exception — {type(exc).__name__}",
            )

        result.delivery_mode = mode

        if result.success:
            logger.info(
                "[DISPATCH] event=delivered provider=%s portal=%s to=%s subject=%r "
                "msg_id=%s latency_ms=%s delivery_mode=%s",
                name, portal, to, subject[:60],
                result.message_id, result.latency_ms, mode,
            )
            return result

        # Delivery failed
        logger.warning(
            "[DISPATCH] event=failed provider=%s portal=%s to=%s error=%r",
            name, portal, to, result.error,
        )

        last_result = result

        # Decide whether to failover
        if not is_transient_error(result.error or ""):
            logger.error(
                "[DISPATCH] event=permanent_failure provider=%s — no failover. error=%r",
                name, result.error,
            )
            # Permanent failure — stop immediately, do not try next provider
            result.delivery_mode = mode
            return result

        if i + 1 < len(providers):
            next_name = providers[i + 1].provider_name
            logger.warning(
                "[DISPATCH] event=failover from=%s to=%s reason=%r",
                name, next_name, result.error,
            )
            mode = "fallback"

    # All providers exhausted
    final_error = last_result.error if last_result else "All providers failed."
    logger.critical(
        "[DISPATCH] event=all_providers_failed portal=%s to=%s subject=%r last_error=%r providers_tried=%s",
        portal, to, subject[:60], final_error,
        [p.provider_name for p in providers],
    )
    return EmailSendResult(
        success=False,
        provider="none",
        error=f"All providers failed. Last error: {final_error}",
        delivery_mode="exhausted",
    )


def send_test_email(to: str, portal: str = "superadmin") -> dict:
    """
    Send a test email and return structured diagnostics.

    Returns a dict matching the documented response format:
    {
        "success": bool,
        "provider": str,
        "http_status": int | None,
        "message_id": str,
        "delivery_mode": str,
        "sender_verified": bool,
        "error": str,
        "providers_tried": [str],
        "active_providers": [str],
    }
    """
    _ensure_file_log()

    providers = get_active_providers(portal=portal)
    active_names = [p.provider_name for p in providers]

    logger.info(
        "[TEST] event=send_test to=%s portal=%s active_providers=%s",
        to, portal, active_names,
    )

    if not providers:
        return {
            "success": False,
            "provider": "none",
            "http_status": None,
            "message_id": "",
            "delivery_mode": "none",
            "sender_verified": False,
            "error": (
                "No active providers configured. "
                "Enable at least one provider in Email Settings and try again."
            ),
            "providers_tried": [],
            "active_providers": active_names,
        }

    html = (
        "<div style='font-family:sans-serif;max-width:520px;margin:auto;"
        "padding:24px;border:1px solid #e5e7eb;border-radius:8px;'>"
        "<h2 style='color:#1f2937;'>✅ Email Delivery Test</h2>"
        "<p>This test email confirms your email provider is configured correctly.</p>"
        "<p style='color:#6b7280;font-size:.85rem;'>Sent by Portfolio CMS "
        "Email Settings → Send Test</p></div>"
    )
    text = (
        "Email Delivery Test\n\n"
        "This test email confirms your email provider is configured correctly.\n\n"
        "Sent by Portfolio CMS."
    )

    providers_tried = []
    last_result: Optional[EmailSendResult] = None
    mode = "primary"

    for i, provider in enumerate(providers):
        name = provider.provider_name
        providers_tried.append(name)

        try:
            result = provider.send_email(
                to=to,
                subject="[Portfolio CMS] Email Delivery Test",
                html=html,
                text=text,
            )
        except Exception as exc:
            logger.exception("[TEST] provider_exception provider=%s to=%s", name, to)
            result = EmailSendResult(
                success=False,
                provider=name,
                error=f"{name}: exception — {type(exc).__name__}",
            )

        result.delivery_mode = mode
        last_result = result

        if result.success:
            logger.info(
                "[TEST] event=success provider=%s to=%s msg_id=%s latency_ms=%s",
                name, to, result.message_id, result.latency_ms,
            )
            d = result.to_dict()
            d["providers_tried"] = providers_tried
            d["active_providers"] = active_names
            return d

        logger.warning("[TEST] event=failed provider=%s error=%r", name, result.error)

        # Only failover on transient errors
        if not is_transient_error(result.error or ""):
            logger.error(
                "[TEST] event=permanent_failure provider=%s — no failover", name
            )
            break

        if i + 1 < len(providers):
            mode = "fallback"

    final_error = last_result.error if last_result else "Unknown error"
    logger.error(
        "[TEST] event=all_failed to=%s error=%r providers_tried=%s",
        to, final_error, providers_tried,
    )

    return {
        "success": False,
        "provider": last_result.provider if last_result else "none",
        "http_status": last_result.http_status if last_result else None,
        "message_id": "",
        "delivery_mode": "failed",
        "sender_verified": False,
        "error": final_error,
        "providers_tried": providers_tried,
        "active_providers": active_names,
    }
