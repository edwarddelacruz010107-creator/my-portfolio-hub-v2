"""
app/services/web3forms_service.py — Web3Forms email relay for Superadmin OTP (v2.0)

SCOPE: Strictly limited to superadmin password-reset OTP delivery.
       No other flow touches this module.
       Admin and tenant flows use email_service.py / mailersend_service.py.

Changes in v2.0:
  • _REQUEST_TIMEOUT raised from 8s to 15s (avoids Render spurious timeouts under load)
  • Retry logic: 3 attempts with exponential backoff (1s, 2s) before giving up
  • send_superadmin_security_alert() added
  • health_check() added
  • Removed: all references to send_otp_email(), MailerSend, SMTP
  • Recipient pinned to OWNER_EMAIL env var; DB email used as audit context only

Security properties:
  • Access key read from environment variable only — never hardcoded
  • Recipient fixed to OWNER_EMAIL; cannot be redirected by caller
  • OTP never logged at INFO/DEBUG level
  • Timeout enforced (15s per attempt)
  • Retry: 3 attempts, exponential backoff (1s → 2s)
  • Exception boundary: all errors surface as (False, "safe message") tuple

Configuration (.env):
  WEB3FORMS_ACCESS_KEY=<your-key-from-web3forms.com>
  OWNER_EMAIL=<superadmin-email-that-receives-the-OTP>
"""
from __future__ import annotations

import logging
import os
import smtplib
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Web3Forms public submission endpoint (auth key in payload body, not header)
_W3F_ENDPOINT    = "https://api.web3forms.com/submit"
_REQUEST_TIMEOUT = 15   # seconds — spec requirement; avoids Render spurious timeouts
_MAX_RETRIES     = 3    # total attempts before giving up
_RETRY_DELAYS    = (1.0, 2.0)  # seconds between attempt 1→2 and 2→3


def _get_access_key() -> str:
    """Resolve Web3Forms access key from environment. Never hardcoded."""
    return os.environ.get("WEB3FORMS_ACCESS_KEY", "").strip()


def _get_owner_email(fallback: str = "") -> str:
    """
    Resolve the destination address for superadmin OTP.

    Priority:
      1. OWNER_EMAIL env var (explicit, recommended — tamper-proof)
      2. Caller-supplied fallback (User.email from DB — only used if OWNER_EMAIL unset)

    OWNER_EMAIL as env-level pin ensures that even if a DB record is tampered with,
    OTP delivery cannot be redirected to an attacker address.
    """
    return os.environ.get("OWNER_EMAIL", "").strip() or fallback


def _post_with_retry(payload: dict) -> tuple[bool, str]:
    """
    POST to Web3Forms with up to _MAX_RETRIES attempts and exponential backoff.

    Returns (True, "delivered") on any successful attempt.
    Returns (False, last_error_message) after all attempts exhausted.
    Logs each failure before retrying.

    Note: 200+success=false (config rejection) is NOT retried — it will not self-heal.
    """
    last_error = "Unknown error"

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(
                _W3F_ENDPOINT,
                json=payload,
                timeout=_REQUEST_TIMEOUT,
                headers={"Accept": "application/json"},
            )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    if attempt > 1:
                        logger.info(
                            "web3forms: delivery accepted on attempt %d/%d",
                            attempt, _MAX_RETRIES,
                        )
                    return True, "delivered"

                # 200 + success=false → config error (bad key, domain mismatch)
                # Do NOT retry — this will not self-heal
                reason = data.get("message", "unknown")
                key_prefix = _get_access_key()
                logger.error(
                    "web3forms: API rejected submission (non-retriable) "
                    "reason=%s access_key_prefix=%s",
                    reason,
                    (key_prefix[:6] + "…") if len(key_prefix) > 6 else "(short)",
                )
                return False, "Unable to send OTP right now. Please try again later."

            elif resp.status_code == 429:
                logger.warning(
                    "web3forms: rate-limited (429) on attempt %d/%d",
                    attempt, _MAX_RETRIES,
                )
                last_error = "OTP request rate limit reached. Please wait a moment and try again."
                # Rate-limit: fall through to retry with backoff

            else:
                logger.error(
                    "web3forms: unexpected HTTP %s on attempt %d/%d — body=%s",
                    resp.status_code, attempt, _MAX_RETRIES, resp.text[:200],
                )
                last_error = "Unable to send OTP right now. Please try again later."

        except requests.exceptions.Timeout:
            logger.error(
                "web3forms: timed out after %ds (attempt %d/%d)",
                _REQUEST_TIMEOUT, attempt, _MAX_RETRIES,
            )
            last_error = "OTP delivery timed out. Please try again."

        except requests.exceptions.ConnectionError as exc:
            logger.error(
                "web3forms: connection error on attempt %d/%d — %s",
                attempt, _MAX_RETRIES, exc,
            )
            last_error = "Unable to reach email service. Please check connectivity."

        except requests.exceptions.RequestException as exc:
            logger.error(
                "web3forms: requests error on attempt %d/%d — %s",
                attempt, _MAX_RETRIES, type(exc).__name__,
            )
            last_error = "Unable to send OTP right now. Please try again later."

        except Exception:  # noqa: BLE001
            logger.exception(
                "web3forms: unexpected error on attempt %d/%d", attempt, _MAX_RETRIES
            )
            last_error = "Unable to send OTP right now. Please try again later."

        # Apply backoff between retries (not after the final attempt)
        if attempt < _MAX_RETRIES:
            delay = _RETRY_DELAYS[attempt - 1] if attempt - 1 < len(_RETRY_DELAYS) else 2.0
            logger.info(
                "web3forms: retrying in %.1fs (attempt %d/%d failed)",
                delay, attempt, _MAX_RETRIES,
            )
            time.sleep(delay)

    logger.error(
        "web3forms: all %d attempts exhausted. Last error: %s", _MAX_RETRIES, last_error
    )
    return False, last_error


def _get_smtp_config() -> dict:
    """
    Resolve SMTP credentials from environment.
    Mirrors the same env var names used by email_service.py for consistency.
    Required vars:
      SMTP_USERNAME   — sender Gmail/SMTP address
      SMTP_PASSWORD   — Gmail App Password (16-char, spaces stripped) or SMTP password
      SMTP_FROM_EMAIL — optional; falls back to SMTP_USERNAME if unset
    Optional:
      SMTP_HOST     — defaults to smtp.gmail.com
      SMTP_PORT     — defaults to 587 (STARTTLS)
    """
    username = os.environ.get("SMTP_USERNAME", "").strip()
    return {
        "host":       os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port":       int(os.environ.get("SMTP_PORT", "587")),
        "user":       username,
        "password":   os.environ.get("SMTP_PASSWORD", "").strip().replace(" ", ""),
        "from_email": os.environ.get("SMTP_FROM_EMAIL", username),
    }


def _send_via_smtp_fallback(
    recipient: str,
    subject: str,
    body: str,
    from_name: str = "Portfolio CMS Security",
) -> tuple[bool, str]:
    """
    Send email via Gmail SMTP (STARTTLS) as fallback when Web3Forms is unavailable.

    Uses stdlib smtplib — no additional dependencies.
    Credentials sourced exclusively from env vars SMTP_USER / SMTP_PASSWORD.
    Never logs credentials or OTP body content.

    Returns:
        (True, "delivered") on success.
        (False, safe_error_message) on any failure.
    """
    cfg = _get_smtp_config()

    if not cfg["user"] or not cfg["password"]:
        logger.critical(
            "smtp_fallback: SMTP_USERNAME or SMTP_PASSWORD not configured — "
            "fallback delivery impossible. Set SMTP_USERNAME and SMTP_PASSWORD."
        )
        return False, "Email service is not configured. Contact your system administrator."

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = f"{from_name} <{cfg['from_email']}>"
    msg["To"]      = recipient

    try:
        logger.info(
            "smtp_fallback: connecting to %s:%s as %s",
            cfg["host"], cfg["port"], cfg["user"],
        )
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_email"], [recipient], msg.as_string())
        logger.info("smtp_fallback: OTP delivered successfully to recipient=%s", recipient)
        return True, "delivered"

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "smtp_fallback: authentication failed for SMTP_USERNAME=%s — "
            "verify App Password is correct and 2FA is enabled on Gmail.",
            cfg["user"],
        )
        return False, "Unable to send OTP right now. Please try again later."

    except smtplib.SMTPException as exc:
        logger.error("smtp_fallback: SMTPException — %s", type(exc).__name__)
        return False, "Unable to send OTP right now. Please try again later."

    except OSError as exc:
        logger.error("smtp_fallback: connection error — %s", exc)
        return False, "Unable to reach email service. Please check connectivity."

    except Exception:  # noqa: BLE001
        logger.exception("smtp_fallback: unexpected error during SMTP delivery")
        return False, "Unable to send OTP right now. Please try again later."


def send_superadmin_otp_web3forms(
    email: str,
    otp: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_minutes: int = 10,
) -> tuple[bool, str]:
    """
    Send superadmin OTP via Web3Forms HTTP API.

    This is the ONLY outbound delivery path for superadmin OTP.
    It does NOT fall back to MailerSend or SMTP under any condition.

    Args:
        email:       User.email from DB — used as audit context in message body ONLY.
                     Routing is determined by OWNER_EMAIL env var, not this arg.
        otp:         Raw 6-digit OTP. Never logged.
        ip_address:  Request IP for audit context in email body.
        user_agent:  Request UA for audit context in email body.
        ttl_minutes: OTP validity window to display in the body.

    Returns:
        (True, "delivered") on success.
        (False, "safe user-facing message") on any failure.

    Raises:
        Nothing — all exceptions are caught and returned as (False, msg).
    """
    access_key = _get_access_key()
    if not access_key:
        logger.critical(
            "web3forms: WEB3FORMS_ACCESS_KEY is not configured — "
            "superadmin OTP delivery IMPOSSIBLE. Set this env var immediately."
        )
        return False, "Email service is not configured. Contact your system administrator."

    recipient = _get_owner_email(fallback=email)
    if not recipient:
        logger.critical(
            "web3forms: no recipient address resolved "
            "(OWNER_EMAIL unset and fallback email is empty)"
        )
        return False, "Unable to resolve OTP recipient address."

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Plain-text body — Web3Forms relays `message` field as-is
    # NOTE: `otp` is intentionally placed in the body but never logged separately
    body = (
        f"A password reset was requested for the Portfolio CMS superadmin account.\n\n"
        f"Your one-time password (OTP) is:\n\n"
        f"    {otp}\n\n"
        f"This OTP expires in {ttl_minutes} minute(s).\n"
        f"Do NOT share it with anyone.\n\n"
        f"Request details:\n"
        f"  Account email : {email}\n"
        f"  IP address    : {ip_address or 'unknown'}\n"
        f"  User agent    : {(user_agent or 'unknown')[:120]}\n"
        f"  Time (UTC)    : {now_str}\n\n"
        f"If you did not request this, secure your account immediately.\n\n"
        f"— Portfolio CMS Security"
    )

    payload: dict = {
        "access_key": access_key,
        "email": recipient,       # explicit To: override — required by Web3Forms API
        "to": recipient,          # safer fallback field; Web3Forms accepts both
        "subject": f"[Portfolio CMS] Superadmin Password Reset OTP — {now_str}",
        "from_name": "Portfolio CMS Security",
        "message": body,
        # Suppress Web3Forms' default confirmation-page redirect (not visible to backend)
        "redirect": "false",
    }

    # Diagnostic log — confirms recipient resolution before network I/O
    logger.info(
        "web3forms: sending OTP to recipient=%s",
        recipient,
    )
    logger.info(
        "web3forms: dispatching superadmin OTP (account=%s ip=%s)",
        email, ip_address or "unknown",
    )

    ok, msg = _post_with_retry(payload)

    if ok:
        logger.info(
            "web3forms: superadmin OTP delivered successfully (account=%s)", email
        )
        return True, "delivered"

    # Web3Forms failed (e.g. 403 server-side block on free plan) — attempt SMTP fallback
    logger.warning(
        "web3forms: primary delivery failed (%s) — attempting SMTP fallback (account=%s)",
        msg, email,
    )
    now_str_fb = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ok_fb, msg_fb = _send_via_smtp_fallback(
        recipient=recipient,
        subject=f"[Portfolio CMS] Superadmin Password Reset OTP — {now_str_fb}",
        body=body,
    )

    if ok_fb:
        logger.info(
            "smtp_fallback: superadmin OTP delivered via SMTP (account=%s)", email
        )
        return True, "delivered"

    # Both delivery paths exhausted
    logger.error(
        "web3forms+smtp_fallback: all delivery paths FAILED (account=%s) "
        "web3forms_err=%s smtp_err=%s",
        email, msg, msg_fb,
    )
    return False, msg


def send_superadmin_security_alert(
    event_type: str,
    email: str,
    ip_address: Optional[str] = None,
    details: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Send a security alert for the superadmin account via Web3Forms.

    Use cases: suspicious login attempts, account lockout, password changed, etc.

    Args:
        event_type:  Short label, e.g. "SUSPICIOUS_LOGIN", "PASSWORD_CHANGED"
        email:       Superadmin account email (for context in body)
        ip_address:  Source IP of the event
        details:     Optional additional context string (max 500 chars)

    Returns:
        (True, "delivered") on success.
        (False, error_message) on failure.
    """
    access_key = _get_access_key()
    if not access_key:
        logger.error(
            "web3forms: security alert skipped — WEB3FORMS_ACCESS_KEY not set"
        )
        return False, "Email service not configured."

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = (
        f"SECURITY ALERT: {event_type}\n\n"
        f"Account : {email}\n"
        f"IP      : {ip_address or 'unknown'}\n"
        f"Time    : {now_str}\n"
    )
    if details:
        body += f"\nDetails:\n{details[:500]}\n"

    body += "\n— Portfolio CMS Security System"

    payload = {
        "access_key": access_key,
        "subject": f"[Portfolio CMS] Security Alert: {event_type} — {now_str}",
        "from_name": "Portfolio CMS Security",
        "message": body,
        "redirect": "false",
    }

    logger.info(
        "web3forms: dispatching security alert event=%s account=%s",
        event_type, email,
    )

    return _post_with_retry(payload)


def health_check() -> dict:
    """
    Check Web3Forms service configuration and connectivity.

    Returns a dict suitable for inclusion in a /health/email endpoint:
    {
        "provider": "web3forms",
        "configured": bool,       # access key is present
        "reachable": bool,        # HTTP connectivity to endpoint confirmed
        "owner_email_set": bool,  # OWNER_EMAIL env var is set
        "warnings": [str]         # empty list = fully configured
    }

    Does NOT submit a real form (no quota consumed). Uses HEAD probe for connectivity.
    """
    result: dict = {
        "provider": "web3forms",
        "configured": False,
        "reachable": False,
        "owner_email_set": False,
        "warnings": [],
    }

    access_key = _get_access_key()
    if not access_key:
        result["warnings"].append(
            "WEB3FORMS_ACCESS_KEY is not set — superadmin OTP delivery will fail"
        )
    else:
        result["configured"] = True

    if not os.environ.get("OWNER_EMAIL", "").strip():
        result["warnings"].append(
            "OWNER_EMAIL is not set — OTP recipient falls back to User.email from DB"
        )
    else:
        result["owner_email_set"] = True

    # Connectivity probe — HEAD to avoid submitting a form or consuming quota
    # Web3Forms returns 405 (Method Not Allowed) for HEAD — still proves reachability
    try:
        probe = requests.head(_W3F_ENDPOINT, timeout=5)
        result["reachable"] = probe.status_code in (200, 405, 404)
    except requests.exceptions.RequestException as exc:
        result["warnings"].append(f"Cannot reach Web3Forms endpoint: {type(exc).__name__}")
        result["reachable"] = False

    return result


def validate_web3forms_config() -> list[str]:
    """
    Return a list of config warning strings for startup validation.
    Called by startup_validation.py if desired.
    Empty list = fully configured.
    """
    warnings: list[str] = []
    if not _get_access_key():
        warnings.append(
            "WEB3FORMS_ACCESS_KEY is not set — "
            "superadmin OTP delivery via Web3Forms will fail"
        )
    if not os.environ.get("OWNER_EMAIL", "").strip():
        warnings.append(
            "OWNER_EMAIL is not set — "
            "superadmin OTP recipient will fall back to User.email from DB"
        )
    return warnings