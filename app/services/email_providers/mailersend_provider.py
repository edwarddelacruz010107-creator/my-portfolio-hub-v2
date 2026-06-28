"""
app/services/email_providers/mailersend_provider.py

MailerSend provider — pure `requests` HTTP, NO SDK dependency.

Key decisions:
  • All HTTP calls use requests with explicit timeouts (never urllib).
  • Validation hits GET /v1/domains — 200=valid, 401=invalid key, other=error.
  • Send hits POST /v1/email — requires 200 or 202.
  • Sender domain validation: sender email must contain @, domain must appear
    in the verified MailerSend domains list, otherwise sending is BLOCKED.
  • response.success is NEVER used — we read HTTP status codes directly.
  • All exceptions are surfaced, never swallowed.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests as _requests

from .base_provider import BaseEmailProvider, EmailSendResult

logger = logging.getLogger("email_provider.mailersend")

_MS_API_BASE = "https://api.mailersend.com/v1"
_DEFAULT_TIMEOUT = 20  # seconds


class MailerSendProvider(BaseEmailProvider):
    """
    MailerSend transactional email via direct HTTPS REST API.

    cfg keys (all strings, resolved by provider_registry before instantiation):
        api_key       — MailerSend API key (never logged)
        sender_email  — From address (must be on a verified domain)
        sender_name   — From display name
    """

    def __init__(self, api_key: str, sender_email: str, sender_name: str = "Portfolio CMS"):
        self._api_key = api_key
        self._sender_email = sender_email.strip().lower()
        self._sender_name = sender_name.strip() or "Portfolio CMS"

    @property
    def provider_name(self) -> str:
        return "mailersend"

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def is_configured(self) -> bool:
        return bool(self._api_key and self._sender_email and "@" in self._sender_email)

    # ── Credential validation ──────────────────────────────────────────────

    def validate_credentials(self) -> tuple[bool, str]:
        """Hit GET /v1/domains — 200=valid, 401=invalid key."""
        if not self._api_key:
            return False, "MailerSend API key is not set."

        try:
            resp = _requests.get(
                f"{_MS_API_BASE}/domains",
                headers=self._auth_headers(),
                timeout=_DEFAULT_TIMEOUT,
            )
        except _requests.Timeout:
            return False, "MailerSend API timed out during validation."
        except _requests.ConnectionError as exc:
            return False, f"MailerSend: network connection error — {type(exc).__name__}"
        except Exception as exc:
            logger.exception("mailersend.validate_credentials: unexpected error")
            return False, f"MailerSend: unexpected error — {type(exc).__name__}"

        if resp.status_code == 200:
            logger.info("mailersend.validate_credentials: OK")
            return True, "MailerSend API key verified successfully."
        if resp.status_code == 401:
            logger.warning("mailersend.validate_credentials: 401 invalid key")
            return False, "Invalid MailerSend API key (401 Unauthorized)."
        if resp.status_code == 429:
            return False, "MailerSend rate limit hit during validation — try again shortly."

        try:
            body = resp.json()
            detail = body.get("message", "")
        except Exception:
            detail = resp.text[:120]

        logger.error("mailersend.validate_credentials: unexpected HTTP %d — %s", resp.status_code, detail)
        return False, f"MailerSend returned HTTP {resp.status_code}: {detail}"

    # ── Sender domain validation ───────────────────────────────────────────

    def validate_sender(self) -> tuple[bool, str]:
        """
        Verify the sender email exists, contains @, and its domain is in
        the account's verified MailerSend domains.

        Blocks sending if the domain is not verified.
        """
        if not self._sender_email:
            return False, "Sender email is not configured."
        if "@" not in self._sender_email:
            return False, f"Sender email '{self._sender_email}' is not a valid email address."

        sender_domain = self._sender_email.split("@", 1)[1].lower()

        try:
            resp = _requests.get(
                f"{_MS_API_BASE}/domains",
                headers=self._auth_headers(),
                timeout=_DEFAULT_TIMEOUT,
            )
        except _requests.Timeout:
            return False, "MailerSend timed out while checking sender domain."
        except _requests.ConnectionError as exc:
            return False, f"MailerSend: network error during sender check — {type(exc).__name__}"
        except Exception as exc:
            logger.exception("mailersend.validate_sender: unexpected error")
            return False, f"MailerSend sender check error — {type(exc).__name__}"

        if resp.status_code == 401:
            return False, "Cannot verify sender domain — invalid MailerSend API key."
        if resp.status_code != 200:
            return False, f"MailerSend domain list returned HTTP {resp.status_code}."

        try:
            data = resp.json()
            domains = data.get("data", [])
        except Exception:
            return False, "MailerSend returned unparseable domain list."

        verified_domains = {
            d.get("name", "").lower()
            for d in domains
            if d.get("is_verified") or d.get("verified")  # handle both field names
        }

        if sender_domain in verified_domains:
            logger.info("mailersend.validate_sender: domain '%s' verified", sender_domain)
            return True, f"Sender domain '{sender_domain}' is verified on MailerSend."

        if verified_domains:
            hint = f"Verified domains: {', '.join(sorted(verified_domains))}"
        else:
            hint = "No verified domains found on this account."

        logger.warning(
            "mailersend.validate_sender: domain '%s' NOT in verified set. %s",
            sender_domain, hint,
        )
        return (
            False,
            f"Sender domain '{sender_domain}' is not verified on MailerSend. "
            f"{hint}. Add and verify this domain in your MailerSend account, "
            f"or use a sender on a verified domain.",
        )

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
                error="MailerSend provider is not configured.",
            )

        payload: dict = {
            "from": {"email": self._sender_email, "name": self._sender_name},
            "to": [{"email": to, "name": to_name or ""}],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if reply_to:
            payload["reply_to"] = [{"email": reply_to}]

        t0 = time.monotonic()
        try:
            resp = _requests.post(
                f"{_MS_API_BASE}/email",
                json=payload,
                headers=self._auth_headers(),
                timeout=_DEFAULT_TIMEOUT,
            )
        except _requests.Timeout:
            err = f"MailerSend: request timed out after {_DEFAULT_TIMEOUT}s"
            logger.error("mailersend.send_email: timeout to=%s", to)
            return EmailSendResult(success=False, provider=self.provider_name, error=err)
        except _requests.ConnectionError as exc:
            err = f"MailerSend: connection error — {type(exc).__name__}"
            logger.error("mailersend.send_email: connection error to=%s: %s", to, exc)
            return EmailSendResult(success=False, provider=self.provider_name, error=err)
        except Exception as exc:
            logger.exception("mailersend.send_email: unexpected error to=%s", to)
            return EmailSendResult(
                success=False,
                provider=self.provider_name,
                error=f"MailerSend: unexpected error — {type(exc).__name__}",
            )

        latency = (time.monotonic() - t0) * 1000
        http_status = resp.status_code

        if http_status in (200, 202):
            msg_id = resp.headers.get("x-message-id", "")
            try:
                body = resp.json()
                msg_id = msg_id or body.get("id", "")
            except Exception:
                pass
            logger.info(
                "mailersend.send_email: delivered to=%s subject=%r msg_id=%s latency=%.0fms",
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

        # Non-success
        try:
            body = resp.json()
            detail = body.get("message", "") or str(body)[:200]
            errors = body.get("errors", {})
            if errors:
                detail += f" | errors: {errors}"
        except Exception:
            detail = resp.text[:200]

        logger.error(
            "mailersend.send_email: HTTP %d to=%s subject=%r error=%s",
            http_status, to, subject[:60], detail,
        )

        if http_status == 401:
            return EmailSendResult(
                success=False, provider=self.provider_name,
                http_status=http_status,
                error=f"MailerSend: invalid API key (401). {detail}",
            )
        if http_status == 422:
            return EmailSendResult(
                success=False, provider=self.provider_name,
                http_status=http_status,
                error=f"MailerSend: unprocessable entity (422) — check sender domain/email. {detail}",
            )
        if http_status == 429:
            return EmailSendResult(
                success=False, provider=self.provider_name,
                http_status=http_status,
                error=f"MailerSend: rate limit exceeded (429). {detail}",
            )
        return EmailSendResult(
            success=False, provider=self.provider_name,
            http_status=http_status,
            error=f"MailerSend: HTTP {http_status}. {detail}",
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
        if ok:
            result["status"] = "ok"
            # Also validate sender domain
            s_ok, s_msg = self.validate_sender()
            result["sender_verified"] = s_ok
            result["sender_detail"] = s_msg
        else:
            result["status"] = f"error: {msg}"
        return result
