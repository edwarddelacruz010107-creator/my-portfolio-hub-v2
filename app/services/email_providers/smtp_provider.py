"""
app/services/email_providers/smtp_provider.py

SMTP email provider with full TLS support, retry on transient errors,
and structured error classification.

Supports:
  • STARTTLS (port 587 — recommended)
  • TLS/SSL (port 465 — implicit TLS)
  • None (only for dev; blocked in production via config)

Permanent errors (auth failure, recipient refused) are NOT retried.
Transient errors (timeout, connection refused, server disconnect) trigger retry.
"""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
import time
from email.charset import Charset, QP
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

from .base_provider import BaseEmailProvider, EmailSendResult

logger = logging.getLogger("email_provider.smtp")

_UTF8_QP = Charset("utf-8")
_UTF8_QP.body_encoding = QP

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 2
_BACKOFF = 1.5


class SMTPProvider(BaseEmailProvider):
    """
    SMTP email provider.

    cfg keys:
        host         — SMTP server hostname
        port         — SMTP port (587 for STARTTLS, 465 for SSL)
        username     — SMTP login username
        password     — SMTP login password (never logged)
        sender_email — From address
        sender_name  — From display name
        encryption   — 'tls' (STARTTLS) | 'ssl' | 'none'
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender_email: str,
        sender_name: str = "Portfolio CMS",
        encryption: str = "tls",
    ):
        self._host = host.strip()
        self._port = int(port) if port else 587
        self._username = username.strip()
        self._password = password  # never strip passwords
        self._sender_email = sender_email.strip().lower()
        self._sender_name = sender_name.strip() or "Portfolio CMS"
        self._encryption = (encryption or "tls").lower()

    @property
    def provider_name(self) -> str:
        return "smtp"

    def is_configured(self) -> bool:
        return bool(
            self._host
            and self._username
            and self._password
            and self._sender_email
            and "@" in self._sender_email
        )

    # ── Credential validation ──────────────────────────────────────────────

    def validate_credentials(self) -> tuple[bool, str]:
        """Attempt a real SMTP login without sending email."""
        if not self.is_configured():
            return False, "SMTP not configured (missing host, username, password, or sender_email)."

        try:
            self._connect_and_login(timeout=10)
            return True, f"SMTP connection to {self._host}:{self._port} successful. Authentication passed."
        except smtplib.SMTPAuthenticationError:
            hint = ""
            if "gmail" in self._host.lower():
                hint = " For Gmail, use an App Password (not your account password)."
            return False, f"SMTP authentication failed — check username and password.{hint}"
        except smtplib.SMTPConnectError as exc:
            return False, f"SMTP: could not connect to {self._host}:{self._port}. Verify the host. ({type(exc).__name__})"
        except (TimeoutError, ConnectionRefusedError) as exc:
            return False, f"SMTP: connection to {self._host}:{self._port} timed out or was refused."
        except ssl.SSLError as exc:
            return False, f"SMTP: SSL/TLS error — {str(exc)[:120]}"
        except Exception as exc:
            # Never include password in error
            safe = str(exc)[:120].replace(self._password, "***") if self._password else str(exc)[:120]
            return False, f"SMTP: connection failed — {safe}"

    def _connect_and_login(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        """Open a connection and login. Raises on failure — never returns a value."""
        use_ssl = (self._encryption == "ssl") or (self._port == 465)

        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(self._host, self._port, timeout=timeout, context=ctx) as server:
                server.login(self._username, self._password)
        elif self._encryption == "none":
            with smtplib.SMTP(self._host, self._port, timeout=timeout) as server:
                server.login(self._username, self._password)
        else:  # STARTTLS
            with smtplib.SMTP(self._host, self._port, timeout=timeout) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(self._username, self._password)

    # ── Sender validation ──────────────────────────────────────────────────

    def validate_sender(self) -> tuple[bool, str]:
        if not self._sender_email:
            return False, "SMTP sender email is not configured."
        if "@" not in self._sender_email:
            return False, f"SMTP sender email '{self._sender_email}' is not valid."
        return True, f"SMTP sender email '{self._sender_email}' is set."

    # ── Message building ───────────────────────────────────────────────────

    def _build_message(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        to_name: Optional[str],
        reply_to: Optional[str],
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((self._sender_name, self._sender_email))
        msg["To"] = formataddr((to_name, to)) if to_name else to
        if reply_to:
            msg["Reply-To"] = reply_to

        plain_part = MIMEText(text, "plain")
        plain_part.set_payload(text, charset=_UTF8_QP)
        msg.attach(plain_part)

        html_part = MIMEText(html, "html")
        html_part.set_payload(html, charset=_UTF8_QP)
        msg.attach(html_part)
        return msg

    # ── Email delivery ─────────────────────────────────────────────────────

    def _dispatch(self, msg: MIMEMultipart, to: str) -> None:
        """One SMTP transaction. Raises on failure."""
        use_ssl = (self._encryption == "ssl") or (self._port == 465)

        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(self._host, self._port, timeout=_DEFAULT_TIMEOUT, context=ctx) as server:
                server.login(self._username, self._password)
                server.sendmail(self._sender_email, [to], msg.as_string())
        elif self._encryption == "none":
            with smtplib.SMTP(self._host, self._port, timeout=_DEFAULT_TIMEOUT) as server:
                server.login(self._username, self._password)
                server.sendmail(self._sender_email, [to], msg.as_string())
        else:  # STARTTLS
            with smtplib.SMTP(self._host, self._port, timeout=_DEFAULT_TIMEOUT) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(self._username, self._password)
                server.sendmail(self._sender_email, [to], msg.as_string())

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
                error="SMTP provider is not configured.",
            )

        msg = self._build_message(to, subject, html, text, to_name, reply_to)
        last_err = "unknown error"

        for attempt in range(1, _MAX_RETRIES + 2):
            t0 = time.monotonic()
            try:
                self._dispatch(msg, to)
                latency = (time.monotonic() - t0) * 1000
                logger.info(
                    "smtp.send_email: delivered to=%s subject=%r attempt=%d latency=%.0fms",
                    to, subject[:60], attempt, latency,
                )
                return EmailSendResult(
                    success=True,
                    provider=self.provider_name,
                    latency_ms=latency,
                    sender_verified=True,
                )

            except smtplib.SMTPAuthenticationError:
                last_err = "SMTP authentication failed (check username/password)"
                logger.error("smtp.send_email: auth failed to=%s", to)
                break  # permanent — do not retry

            except smtplib.SMTPRecipientsRefused:
                last_err = f"Recipient refused: {to}"
                logger.error("smtp.send_email: recipient refused to=%s", to)
                break  # permanent

            except smtplib.SMTPResponseException as exc:
                transient_codes = {421, 450, 451, 452}
                if exc.smtp_code in transient_codes and attempt <= _MAX_RETRIES:
                    last_err = f"SMTP temporary error {exc.smtp_code}"
                    logger.warning("smtp.send_email: transient %d attempt=%d, retrying", exc.smtp_code, attempt)
                    time.sleep(_BACKOFF * attempt)
                    continue
                last_err = f"SMTP error {exc.smtp_code}: {exc.smtp_error}"
                break

            except (smtplib.SMTPServerDisconnected, TimeoutError, ConnectionRefusedError, OSError) as exc:
                last_err = f"SMTP transient: {type(exc).__name__}"
                if attempt <= _MAX_RETRIES:
                    logger.warning("smtp.send_email: transient %s attempt=%d, retrying", type(exc).__name__, attempt)
                    time.sleep(_BACKOFF * attempt)
                    continue
                break

            except ssl.SSLError as exc:
                last_err = f"SMTP SSL error: {str(exc)[:120]}"
                logger.error("smtp.send_email: SSL error to=%s: %s", to, exc)
                break

            except Exception as exc:
                safe = str(exc)[:120].replace(self._password, "***") if self._password else str(exc)[:120]
                last_err = f"SMTP unexpected error: {safe}"
                logger.exception("smtp.send_email: unexpected error to=%s", to)
                break

        logger.error("smtp.send_email: ALL ATTEMPTS FAILED to=%s: %s", to, last_err)
        return EmailSendResult(
            success=False,
            provider=self.provider_name,
            error=last_err,
        )

    # ── Health check ───────────────────────────────────────────────────────

    def health_check(self) -> dict:
        result: dict = {
            "provider": self.provider_name,
            "configured": self.is_configured(),
            "status": "unconfigured",
            "host": self._host or None,
            "port": self._port,
        }
        if not self.is_configured():
            return result

        t0 = time.monotonic()
        ok, msg = self.validate_credentials()
        result["latency_ms"] = round((time.monotonic() - t0) * 1000)
        result["status"] = "ok" if ok else f"error: {msg}"
        return result
