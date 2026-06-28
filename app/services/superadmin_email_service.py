"""
app/services/superadmin_email_service.py — Superadmin Email Dispatcher (v6.6)

Delegates to the centralized email_providers architecture.

BREAKING CHANGES from v5.9.1:
  - _resolve_configs()       — kept for backward compat with existing route imports
  - send_email()             — now calls provider_dispatcher.dispatch_email()
  - send_otp()               — unchanged public signature
  - _send_mailersend() etc.  — REMOVED (use provider classes directly)
  - urllib usage             — REMOVED (all HTTP via requests)
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

from app.services.email_providers.provider_dispatcher import dispatch_email as _dispatch
from app.services.email_providers.provider_registry import get_active_providers

logger = logging.getLogger(__name__)


# ── Backward-compat shim for existing route: `from app.services.superadmin_email_service import _resolve_configs`
def _resolve_configs() -> list:
    """
    Backward-compatible shim.

    The old return type was a list of raw dicts.
    Now returns the list of active BaseEmailProvider instances.
    Callers that only check `if not _resolve_configs()` still work correctly.
    """
    return get_active_providers(portal="superadmin")


def _html_to_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def send_email(
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Send via the active provider chain (superadmin portal).

    Returns (True, '') on success or (False, error_message) on failure.
    """
    plain = text or _html_to_text(html)
    result = _dispatch(
        to=to,
        subject=subject,
        html=html,
        text=plain,
        portal="superadmin",
    )
    if result.success:
        return True, result.message_id or ""
    return False, result.error or "Unknown error"


def send_otp(
    email: str,
    otp: str,
    portal: str = "superadmin",
    ip_address: str = "",
    ttl_minutes: int = 10,
) -> tuple[bool, str]:
    """Send OTP verification email for superadmin or admin portal password reset."""
    portal_label = "Superadmin" if portal == "superadmin" else "Admin"
    html = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px;
                background:#0f172a;color:#f1f5f9;border-radius:12px;">
      <div style="text-align:center;margin-bottom:28px;">
        <div style="display:inline-block;background:#1e293b;border-radius:8px;
                    padding:12px 20px;border:1px solid #334155;">
          <span style="font-size:20px;font-weight:700;color:#38bdf8;letter-spacing:0.05em;">
            PORTFOLIO CMS
          </span>
        </div>
      </div>
      <h2 style="text-align:center;color:#f1f5f9;margin-bottom:8px;font-size:22px;">
        {portal_label} Verification Code
      </h2>
      <p style="text-align:center;color:#94a3b8;margin-bottom:28px;">
        Your one-time password for {portal_label.lower()} access
      </p>
      <div style="text-align:center;background:#1e293b;border:1px solid #334155;
                  border-radius:12px;padding:28px;margin-bottom:24px;">
        <div style="font-size:44px;font-weight:800;letter-spacing:0.18em;
                    color:#38bdf8;font-family:monospace;">{otp}</div>
        <p style="color:#94a3b8;font-size:13px;margin-top:12px;">
          Expires in {ttl_minutes} minutes
        </p>
      </div>
      <div style="background:#1e293b;border-left:3px solid #f59e0b;border-radius:4px;
                  padding:16px;margin-bottom:20px;">
        <p style="margin:0;color:#fbbf24;font-size:13px;font-weight:600;">Security Notice</p>
        <p style="margin:6px 0 0;color:#94a3b8;font-size:12px;">
          IP: {ip_address or 'unknown'}<br>
          If you did not request this code, secure your account immediately.
        </p>
      </div>
      <p style="text-align:center;color:#475569;font-size:11px;">
        Portfolio CMS &bull; Security System
      </p>
    </div>
    """
    return send_email(
        email,
        f"Portfolio CMS — {portal_label} Verification Code",
        html,
    )
