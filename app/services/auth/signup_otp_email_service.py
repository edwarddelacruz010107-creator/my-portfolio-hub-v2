"""Signup email-verification OTP delivery.

This module is intentionally small and focused: account-creation OTPs must be
sent through the same SuperAdmin → Email & Forms provider chain used by the
platform email settings page.  It does not read SMTP/API credentials directly
and it never logs OTP values or secrets.
"""
from __future__ import annotations

import html as _html
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SIGNUP_OTP_TTL_MINUTES = 3
_SIGNUP_OTP_SUBJECT = "Your MyPortfolioHub verification code"


@dataclass(frozen=True)
class SignupOtpDeliveryResult:
    ok: bool
    provider_hint: str
    error: str = ""


def get_signup_otp_ttl_minutes(default: int = DEFAULT_SIGNUP_OTP_TTL_MINUTES) -> int:
    """Resolve public signup OTP expiry.

    Signup verification is a short-lived public flow, so it is controlled by
    SIGNUP_OTP_TTL_MINUTES instead of the password-recovery OTP setting.
    """
    try:
        from flask import current_app

        configured = current_app.config.get("SIGNUP_OTP_TTL_MINUTES")
        return max(1, int(configured or default))
    except Exception:
        logger.debug("signup_otp: could not resolve SIGNUP_OTP_TTL_MINUTES", exc_info=True)
        return default


def _configured_provider_names() -> list[str]:
    """Return non-sensitive active provider names from the central dispatcher."""
    try:
        from app.services.superadmin_email_service import _resolve_configs

        providers = _resolve_configs()
        names: list[str] = []
        for provider in providers:
            name = str(provider.get("provider") or "").strip()
            if name:
                names.append(name)
        return names
    except Exception:
        logger.exception("signup_otp: failed to inspect active SuperAdmin email providers")
        return []


def _build_signup_otp_email(username: str, otp: str, ttl_minutes: int) -> tuple[str, str, str]:
    """Build a lightweight, deliverability-friendly signup OTP email.

    Keep both text and HTML clear and mostly text-based. The raw OTP is only
    inserted into the message body and must never be logged.
    """
    safe_name = _html.escape((username or "there").strip() or "there")
    safe_otp = _html.escape(str(otp).strip())
    ttl = max(1, int(ttl_minutes or DEFAULT_SIGNUP_OTP_TTL_MINUTES))

    text = (
        f"Use this code to verify your MyPortfolioHub account: {otp}. "
        f"This code expires in {ttl} minutes. "
        "If you did not create this account, you can ignore this email.\n\n"
        "Security note: never share this code with anyone.\n"
        "Need help? Contact MyPortfolioHub support.\n"
    )

    html = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;background:#ffffff;color:#111827;border:1px solid #e5e7eb;border-radius:12px;">
  <p style="font-size:13px;line-height:1.5;margin:0 0 12px;color:#4f46e5;font-weight:700;">MyPortfolioHub</p>
  <h1 style="font-size:22px;line-height:1.25;margin:0 0 12px;color:#111827;">Your verification code</h1>
  <p style="font-size:15px;line-height:1.6;margin:0 0 18px;color:#374151;">
    Hi {safe_name}, use this code to verify your MyPortfolioHub account.
  </p>
  <p style="font-family:Consolas,Menlo,Monaco,monospace;font-size:32px;letter-spacing:.28em;font-weight:800;line-height:1.2;margin:0 0 18px;color:#111827;">
    {safe_otp}
  </p>
  <p style="font-size:14px;line-height:1.6;margin:0 0 12px;color:#374151;">
    This code expires in {ttl} minutes.
  </p>
  <p style="font-size:14px;line-height:1.6;margin:0 0 18px;color:#374151;">
    If you did not create this account, you can ignore this email. Never share this code with anyone.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:18px 0;">
  <p style="font-size:12px;line-height:1.5;margin:0;color:#6b7280;">
    This is an automated security email from MyPortfolioHub. Need help? Contact MyPortfolioHub support.
  </p>
</div>
""".strip()
    return _SIGNUP_OTP_SUBJECT, text, html


def send_signup_verification_otp(
    *,
    recipient_email: str,
    username: str,
    otp: str,
    ttl_minutes: Optional[int] = None,
    context: str = "signup",
) -> SignupOtpDeliveryResult:
    """Send account-creation OTP through SuperAdmin Email & Forms providers.

    Provider resolution, retries, and fallback behavior are delegated to
    app.services.superadmin_email_service, which is the same centralized
    dispatcher used by the SuperAdmin Email & Forms test button.
    """
    email = (recipient_email or "").strip().lower()
    ttl = max(1, int(ttl_minutes or get_signup_otp_ttl_minutes()))
    providers = _configured_provider_names()
    provider_hint = ",".join(providers) if providers else "none"

    logger.info(
        "Signup verification OTP email send started context=%s provider_candidates=%s recipient=%s",
        context,
        provider_hint,
        email,
    )

    if not email:
        logger.error("Signup verification OTP email failed context=%s error_type=MissingRecipient", context)
        return SignupOtpDeliveryResult(False, provider_hint, "Missing recipient email")

    if not providers:
        logger.error(
            "Signup verification OTP email failed context=%s recipient=%s error_type=NoActiveProvider",
            context,
            email,
        )
        return SignupOtpDeliveryResult(
            False,
            provider_hint,
            "No active SuperAdmin Email & Forms provider is configured.",
        )

    subject, text, html = _build_signup_otp_email(username=username, otp=otp, ttl_minutes=ttl)

    try:
        from app.services.superadmin_email_service import send_email as _send_email

        ok, err = _send_email(to=email, subject=subject, html=html, text=text)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Signup verification OTP email failed context=%s recipient=%s error_type=%s",
            context,
            email,
            type(exc).__name__,
        )
        return SignupOtpDeliveryResult(False, provider_hint, type(exc).__name__)

    if ok:
        logger.info(
            "Signup verification OTP email sent context=%s provider_candidates=%s recipient=%s",
            context,
            provider_hint,
            email,
        )
        return SignupOtpDeliveryResult(True, provider_hint, "")

    logger.error(
        "Signup verification OTP email failed context=%s provider_candidates=%s recipient=%s error_type=ProviderFailure reason=%s",
        context,
        provider_hint,
        email,
        str(err or "")[:180],
    )
    return SignupOtpDeliveryResult(False, provider_hint, str(err or "Provider failure"))
