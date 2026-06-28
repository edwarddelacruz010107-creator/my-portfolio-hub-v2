"""
app/services/email_providers/resend_provider.py

Resend email provider — pure `requests`, NO urllib.

All HTTP calls use `requests` with explicit timeouts.
Validation hits GET /domains — 200=valid, 401=invalid key.
Send hits POST /emails — requires 200 or 201.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests as _requests

from .base_provider import BaseEmailProvider, EmailSendResult

logger = logging.getLogger("email_provider.resend")

_RESEND_API_BASE = "https://api.resend.com"
_DEFAULT_TIMEOUT = 20


class ResendProvider(BaseEmailProvider):
    """
    Resend transactional email via direct HTTPS REST API.

    cfg keys:
        api_key       — Resend API key (never logged)
        sender_email  — From address (must be on a verified Resend domain)
        sender_name   — From display name
    """

    def __init__(self, api_key: str, sender_email: str, sender_name: str = "Portfolio CMS"):
        self._api_key = api_key.strip() if api_key else ""
        self._sender_email = sender_email.strip().lower() if sender_email else ""
        self._sender_name = sender_name.strip() or "Portfolio CMS"

    @property
    def provider_name(self) -> str:
        return "resend"

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def is_configured(self) -> bool:
        """
        Return True only when api_key and sender_email are both non-empty.

        This is the root cause of "Not configured" appearing when a key exists:
        the old code checked the raw encrypted blob rather than the decrypted
        value, so a decryption failure or empty string after decryption would
        report 'not configured' even when an encrypted key was present.
        """
        return bool(self._api_key and self._sender_email and "@" in self._sender_email)

    # ── Credential validation ──────────────────────────────────────────────

    def validate_credentials(self) -> tuple[bool, str]:
        """Hit GET /domains — 200=valid, 401=invalid key."""
        if not self._api_key:
            return False, "Resend API key is not set."

        try:
            resp = _requests.get(
                f"{_RESEND_API_BASE}/domains",
                headers=self._auth_headers(),
                timeout=_DEFAULT_TIMEOUT,
            )
        except _requests.Timeout:
            return False, "Resend API timed out during validation."
        except _requests.ConnectionError as exc:
            return False, f"Resend: network connection error — {type(exc).__name__}"
        except Exception as exc:
            logger.exception("resend.validate_credentials: unexpected error")
            return False, f"Resend: unexpected error — {type(exc).__name__}"

        if resp.status_code == 200:
            logger.info("resend.validate_credentials: OK")
            return True, "Resend API key verified successfully."

        if resp.status_code == 401:
            logger.warning("resend.validate_credentials: 401 invalid key")
            return False, "Invalid Resend API key (401 Unauthorized)."

        if resp.status_code == 429:
            return False, "Resend rate limit hit during validation — try again shortly."

        try:
            body = resp.json()
            detail = body.get("message", "") or body.get("name", "")
        except Exception:
            detail = resp.text[:120]

        logger.error("resend.validate_credentials: HTTP %d — %s", resp.status_code, detail)
        return False, f"Resend returned HTTP {resp.status_code}: {detail}"

    # ── Sender validation ──────────────────────────────────────────────────

    def validate_sender(self) -> tuple[bool, str]:
        if not self._sender_email:
            return False, "Sender email is not configured."
        if "@" not in self._sender_email:
            return False, f"Sender email '{self._sender_email}' is not a valid email address."
        return True, f"Sender email '{self._sender_email}' is set."

    # ── Email delivery ─────────────────────────────────────────────────────

    def send_email(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        to_name: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> EmailSendResult:
        if not self.is_configured():
            return EmailSendResult(
                success=False,
                provider=self.provider_name,
                error="Resend provider is not configured.",
            )

        from_str = (
            f"{self._sender_name} <{self._sender_email}>"
            if self._sender_name
            else self._sender_email
        )

        payload: dict = {
            "from": from_str,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if reply_to:
            payload["reply_to"] = reply_to

        t0 = time.monotonic()
        try:
            resp = _requests.post(
                f"{_RESEND_API_BASE}/emails",
                json=payload,
                headers=self._auth_headers(),
                timeout=_DEFAULT_TIMEOUT,
            )
        except _requests.Timeout:
            err = f"Resend: request timed out after {_DEFAULT_TIMEOUT}s"
            logger.error("resend.send_email: timeout to=%s", to)
            return EmailSendResult(success=False, provider=self.provider_name, error=err)
        except _requests.ConnectionError as exc:
            err = f"Resend: connection error — {type(exc).__name__}"
            logger.error("resend.send_email: connection error to=%s: %s", to, exc)
            return EmailSendResult(success=False, provider=self.provider_name, error=err)
        except Exception as exc:
            logger.exception("resend.send_email: unexpected error to=%s", to)
            return EmailSendResult(
                success=False,
                provider=self.provider_name,
                error=f"Resend: unexpected error — {type(exc).__name__}",
            )

        latency = (time.monotonic() - t0) * 1000
        http_status = resp.status_code

        if http_status in (200, 201):
            try:
                body = resp.json()
                msg_id = body.get("id", "")
            except Exception:
                msg_id = ""
            logger.info(
                "resend.send_email: delivered to=%s subject=%r msg_id=%s latency=%.0fms",
                to, subject[:60], msg_id, latency,
            )
            return EmailSendResult(
                success=True,
                provider=self.provider_name,
                message_id=msg_id,
                http_status=http_status,
                latency_ms=latency,
                sender_verified=True,
            )

        try:
            body = resp.json()
            detail = body.get("message", "") or body.get("name", "") or str(body)[:200]
        except Exception:
            detail = resp.text[:200]

        logger.error(
            "resend.send_email: HTTP %d to=%s subject=%r error=%s",
            http_status, to, subject[:60], detail,
        )

        if http_status == 401:
            return EmailSendResult(
                success=False, provider=self.provider_name,
                http_status=http_status,
                error=f"Resend: invalid API key (401). {detail}",
            )
        if http_status == 422:
            return EmailSendResult(
                success=False, provider=self.provider_name,
                http_status=http_status,
                error=f"Resend: unprocessable entity (422) — check sender email. {detail}",
            )
        if http_status == 429:
            return EmailSendResult(
                success=False, provider=self.provider_name,
                http_status=http_status,
                error=f"Resend: rate limit exceeded (429). {detail}",
            )
        return EmailSendResult(
            success=False, provider=self.provider_name,
            http_status=http_status,
            error=f"Resend: HTTP {http_status}. {detail}",
        )

    # ── Health check ───────────────────────────────────────────────────────

    def health_check(self) -> dict:
        result: dict = {
            "provider": self.provider_name,
            "configured": self.is_configured(),
            "status": "unconfigured",
            "sender_email": self._sender_email,
        }
        if not self.is_configured():
            return result

        t0 = time.monotonic()
        ok, msg = self.validate_credentials()
        result["latency_ms"] = round((time.monotonic() - t0) * 1000)
        result["status"] = "ok" if ok else f"error: {msg}"
        return result
