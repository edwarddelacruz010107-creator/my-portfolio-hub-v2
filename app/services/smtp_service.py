"""
app/services/smtp_service.py — Standalone SMTP Delivery Service (v5.7)

ARCHITECTURAL ROLE
───────────────────────────────────────────────────────────────────────────
This module is the SOLE email transport for the Superadmin password-reset
flow, and the FALLBACK transport for Tenant/Admin (invoked from
email_service.py when MailerSend fails).

ISOLATION CONTRACT (superadmin):
    • Zero imports from mailersend_service.py, web3forms_service.py, or
      email_service.py. This file has NO transitive dependency on any
      third-party HTTP email API. If this module's external dependencies
      (DNS + SMTP_HOST reachability) are healthy, superadmin OTP delivery
      cannot be taken down by a MailerSend outage, a Web3Forms plan/billing
      change, or any other tenant/admin provider misconfiguration.
    • Does not read GlobalEmailConfig, TenantCommunicationSettings, or any
      per-tenant/per-portal MailerSend key. Configuration is environment
      variables ONLY (see Configuration section below) — this guarantees
      superadmin recovery still works even if the database holding
      GlobalEmailConfig is degraded (read replica lag, migration in
      progress, etc.) as long as env vars and the OTP record (already
      committed by the caller) are available.

Configuration (environment variables — no DB fallback, no defaults that
mask misconfiguration):
    SMTP_HOST          e.g. smtp.gmail.com
    SMTP_PORT          e.g. 587 (STARTTLS) or 465 (implicit TLS)
    SMTP_USERNAME       authenticated account
    SMTP_PASSWORD       app password / API secret (never logged)
    SMTP_FROM_EMAIL    envelope + header From
    SMTP_FROM_NAME     display name (default: "Portfolio CMS")

Security properties:
    • TLS enforced — STARTTLS on port 587/25, implicit TLS (SMTPS) on 465.
      Plaintext SMTP is never attempted by this module.
    • UTF-8 throughout (headers via email.utils.formataddr + MIME charset).
    • Credentials are never included in log lines, exceptions, or return
      values surfaced to callers/templates.
    • 30-second hard socket timeout (connect + each blocking call) —
      prevents a stalled mail relay from hanging the password-reset
      request thread/worker.
    • Bounded retry (transient errors only): connection refused, timeout,
      and 4xx SMTP temporary failures are retried with backoff. Permanent
      failures (auth error, 5xx recipient refused) are NOT retried.

Public API:
    send_email(to, subject, text, html=None, ...) -> (bool, str)
    send_superadmin_otp(email, otp, ip_address, user_agent, ttl_minutes) -> (bool, str)
    send_security_alert(email, event, ip_address, user_agent, detail) -> (bool, str)
    health_check() -> dict
    validate_configuration() -> list[str]
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.charset import Charset, QP
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger(__name__)

_UTF8_QP = Charset('utf-8')
_UTF8_QP.body_encoding = QP  # avoid base64-encoded bodies; QP stays human-readable in raw MIME

_DEFAULT_TIMEOUT = 30        # seconds — hard requirement per spec
_MAX_RETRIES     = 2         # total attempts = 1 + _MAX_RETRIES, transient errors only
_RETRY_BACKOFF   = 1.5       # seconds, multiplied per attempt


# ─────────────────────────────────────────────────────────────────────────────
# Configuration resolution
# ─────────────────────────────────────────────────────────────────────────────

def _config() -> dict:
    """
    Resolve SMTP configuration strictly from environment variables.

    No DB lookups. No cross-module imports. This is intentional: the
    superadmin recovery path must not depend on anything that itself
    depends on a working database session beyond the OTP record already
    written by otp_service.create_otp_record().
    """
    return {
        'host':       os.environ.get('SMTP_HOST', '').strip(),
        'port':       int(os.environ.get('SMTP_PORT', '587') or 587),
        'username':   os.environ.get('SMTP_USERNAME', '').strip(),
        'password':   os.environ.get('SMTP_PASSWORD', '').strip(),
        'from_email': os.environ.get('SMTP_FROM_EMAIL', '').strip(),
        'from_name':  os.environ.get('SMTP_FROM_NAME', 'Portfolio CMS').strip(),
        'timeout':    _DEFAULT_TIMEOUT,
    }


def _is_configured(cfg: Optional[dict] = None) -> bool:
    cfg = cfg or _config()
    return bool(cfg['host'] and cfg['username'] and cfg['password'] and cfg['from_email'])


def validate_configuration() -> list[str]:
    """Return a list of warning strings. Empty list = fully configured."""
    cfg = _config()
    missing = [k for k in ('host', 'username', 'password', 'from_email') if not cfg[k]]
    if missing:
        return [
            f'smtp_service: missing required env var(s): '
            f'{", ".join("SMTP_" + m.upper() for m in missing)}'
        ]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', '', html)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


_TRANSIENT_SMTP_CODES = {421, 450, 451, 452}  # temporary failures — safe to retry


def _classify_smtp_error(exc: Exception) -> tuple[bool, str]:
    """
    Return (is_transient, safe_message). Transient errors are retried;
    permanent errors (auth, recipient refused, malformed envelope) are not.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return False, 'SMTP authentication failed (check SMTP_USERNAME / SMTP_PASSWORD)'
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return False, 'recipient address refused by server'
    if isinstance(exc, smtplib.SMTPResponseException):
        if exc.smtp_code in _TRANSIENT_SMTP_CODES:
            return True, f'temporary SMTP error {exc.smtp_code}'
        return False, f'SMTP error {exc.smtp_code}'
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return True, 'server disconnected unexpectedly'
    if isinstance(exc, (TimeoutError, ConnectionRefusedError, OSError)):
        return True, f'network error: {type(exc).__name__}'
    return False, f'unexpected error: {type(exc).__name__}'


def _build_message(
    to: str,
    subject: str,
    text: str,
    html: Optional[str],
    cfg: dict,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> MIMEMultipart:
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = formataddr((cfg['from_name'], cfg['from_email']))
    msg['To']      = formataddr((to_name, to)) if to_name else to
    if reply_to:
        msg['Reply-To'] = reply_to

    plain = text or (html and _html_to_text(html)) or ''
    plain_part = MIMEText(plain, 'plain')
    plain_part.set_payload(plain, charset=_UTF8_QP)
    msg.attach(plain_part)
    if html:
        html_part = MIMEText(html, 'html')
        html_part.set_payload(html, charset=_UTF8_QP)
        msg.attach(html_part)
    return msg


def _dispatch(cfg: dict, msg: MIMEMultipart, to: str) -> None:
    """One physical SMTP transaction. Raises on failure — caller classifies."""
    use_ssl = cfg['port'] == 465

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=cfg['timeout'], context=context) as server:
            server.login(cfg['username'], cfg['password'])
            server.sendmail(cfg['from_email'], [to], msg.as_string())
    else:
        with smtplib.SMTP(cfg['host'], cfg['port'], timeout=cfg['timeout']) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(cfg['username'], cfg['password'])
            server.sendmail(cfg['from_email'], [to], msg.as_string())


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def send_email(
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    context: str = 'general',
) -> tuple[bool, str]:
    """
    Send a single email via SMTP with bounded retry on transient failure.

    Args:
        to:       recipient address.
        subject:  email subject.
        text:     plain-text body (required logically; derived from html if absent).
        html:     optional HTML body.
        to_name:  optional recipient display name.
        reply_to: optional Reply-To header.
        context:  free-text tag used only in log lines (e.g. 'superadmin_otp',
                  'security_alert', 'admin_fallback', 'tenant_fallback').

    Returns:
        (True, 'delivered') on success.
        (False, safe_error_message) on failure — never contains credentials.
    """
    cfg = _config()

    if not _is_configured(cfg):
        msg = 'SMTP not configured (missing SMTP_HOST/USERNAME/PASSWORD/FROM_EMAIL)'
        logger.error('smtp.send [%s]: %s', context, msg)
        return False, msg

    last_err = 'unknown error'
    for attempt in range(1, _MAX_RETRIES + 2):  # 1 initial + N retries
        t0 = time.monotonic()
        try:
            mime_msg = _build_message(to, subject, text, html, cfg, to_name=to_name, reply_to=reply_to)
            _dispatch(cfg, mime_msg, to)
            elapsed = (time.monotonic() - t0) * 1000
            logger.info(
                'smtp.send [%s]: delivered to=%s subject="%s" attempt=%d/%d latency=%.0fms',
                context, to, subject[:60], attempt, _MAX_RETRIES + 1, elapsed,
            )
            return True, 'delivered'

        except Exception as exc:  # noqa: BLE001 — classified below, never leaks raw exc to caller
            transient, safe_msg = _classify_smtp_error(exc)
            last_err = safe_msg
            logger.warning(
                'smtp.send [%s]: attempt %d/%d failed to=%s reason=%s transient=%s',
                context, attempt, _MAX_RETRIES + 1, to, safe_msg, transient,
            )
            if not transient or attempt > _MAX_RETRIES:
                break
            time.sleep(_RETRY_BACKOFF * attempt)

    logger.error('smtp.send [%s]: ALL ATTEMPTS FAILED to=%s last_error=%s', context, to, last_err)
    return False, last_err


def send_superadmin_otp(
    email: str,
    otp: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_minutes: int = 10,
) -> tuple[bool, str]:
    """
    Send a superadmin password-reset OTP.

    This is the ONLY delivery path for superadmin OTP — no MailerSend,
    no Web3Forms, no tenant/admin email configuration is consulted.
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    subject = f'[Portfolio CMS] Superadmin Password Reset OTP — {now}'

    text = (
        f'Hello,\n\n'
        f'A SUPERADMIN password reset was requested for your Portfolio CMS account.\n\n'
        f'Your one-time password (OTP) is:\n\n'
        f'    {otp}\n\n'
        f'This OTP expires in {ttl_minutes} minutes.\n'
        f'Do NOT share it with anyone. Portfolio CMS staff will never ask for it.\n\n'
        f'Request details:\n'
        f'  IP address : {ip_address or "unknown"}\n'
        f'  User agent : {(user_agent or "unknown")[:120]}\n'
        f'  Time (UTC) : {now}\n\n'
        f'If you did not request this, rotate your credentials immediately and review\n'
        f'recent superadmin audit logs.\n\n'
        f'— Portfolio CMS Superadmin Security'
    )
    html = f'''
<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:2rem;
            border:1px solid #e5e7eb;border-radius:8px;">
  <h2 style="color:#1f2937;margin-top:0;">Superadmin Password Reset OTP</h2>
  <p>A password reset was requested for your <strong>Superadmin</strong> account.</p>
  <div style="background:#f9fafb;border:1px solid #d1d5db;border-radius:6px;
              padding:1.5rem;text-align:center;margin:1.5rem 0;">
    <span style="font-size:2rem;font-weight:700;letter-spacing:.4rem;color:#dc2626;">{otp}</span>
  </div>
  <p style="color:#6b7280;font-size:.9rem;">
    Expires in <strong>{ttl_minutes} minutes</strong>. Do not share this code.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:1.5rem 0;">
  <p style="color:#9ca3af;font-size:.8rem;">
    Request IP: {ip_address or "unknown"} &bull; Time: {now}
  </p>
</div>'''

    ok, err = send_email(
        to=email, subject=subject, text=text, html=html, context='superadmin_otp',
    )
    return ok, err


def send_admin_otp(
    email: str,
    otp: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_minutes: int = 10,
) -> tuple[bool, str]:
    """
    Send an admin (tenant admin user) password-reset OTP via SMTP.

    Delivery path: SMTP primary (this function) → MailerSend fallback
    (handled by the caller in password_reset_service.initiate_admin_reset).

    Mirrors send_superadmin_otp() but uses the 'admin_otp' log context and
    admin-appropriate copy so log correlation is unambiguous.
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    subject = f'[Portfolio CMS] Admin Password Reset OTP — {now}'

    text = (
        f'Hello,\n\n'
        f'A password reset was requested for your Portfolio CMS Admin account.\n\n'
        f'Your one-time password (OTP) is:\n\n'
        f'    {otp}\n\n'
        f'This OTP expires in {ttl_minutes} minutes.\n'
        f'Do NOT share it with anyone. Portfolio CMS staff will never ask for it.\n\n'
        f'Request details:\n'
        f'  IP address : {ip_address or "unknown"}\n'
        f'  User agent : {(user_agent or "unknown")[:120]}\n'
        f'  Time (UTC) : {now}\n\n'
        f'If you did not request this, secure your account immediately.\n\n'
        f'— Portfolio CMS Admin Security'
    )
    html = f'''
<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:2rem;
            border:1px solid #e5e7eb;border-radius:8px;">
  <h2 style="color:#1f2937;margin-top:0;">Admin Password Reset OTP</h2>
  <p>A password reset was requested for your <strong>Admin</strong> account.</p>
  <div style="background:#f9fafb;border:1px solid #d1d5db;border-radius:6px;
              padding:1.5rem;text-align:center;margin:1.5rem 0;">
    <span style="font-size:2rem;font-weight:700;letter-spacing:.4rem;color:#4f46e5;">{otp}</span>
  </div>
  <p style="color:#6b7280;font-size:.9rem;">
    Expires in <strong>{ttl_minutes} minutes</strong>. Do not share this code.
  </p>
  <hr style="border:none;border-top:1px solid #e5e7eb;margin:1.5rem 0;">
  <p style="color:#9ca3af;font-size:.8rem;">
    Request IP: {ip_address or "unknown"} &bull; Time: {now}
  </p>
</div>'''

    ok, err = send_email(
        to=email, subject=subject, text=text, html=html, context='admin_otp',
    )
    return ok, err


def send_security_alert(
    email: str,
    event: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    detail: str = '',
) -> tuple[bool, str]:
    """
    Send a superadmin security-alert email (e.g. successful password change,
    repeated failed OTP attempts, account lockout).
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    subject = f'[Portfolio CMS] Superadmin Security Alert — {event}'

    text = (
        f'Hello,\n\n'
        f'Security event on your Superadmin account: {event}\n\n'
        f'{detail}\n\n'
        f'IP address : {ip_address or "unknown"}\n'
        f'User agent : {(user_agent or "unknown")[:120]}\n'
        f'Time (UTC) : {now}\n\n'
        f'If this was not you, secure your account immediately.\n\n'
        f'— Portfolio CMS Superadmin Security'
    )
    ok, err = send_email(
        to=email, subject=subject, text=text, context='security_alert',
    )
    return ok, err


def health_check() -> dict:
    """
    Return a status dict without sending real email. Used by /health/email
    and by the superadmin Email Settings diagnostics panel.
    """
    cfg = _config()
    result = {
        'provider':    'smtp',
        'configured':  _is_configured(cfg),
        'host':        cfg['host'] or None,
        'port':        cfg['port'],
        'status':      'unknown',
        'warnings':    validate_configuration(),
    }

    if not result['configured']:
        result['status'] = 'not_configured'
        return result

    try:
        use_ssl = cfg['port'] == 465
        t0 = time.monotonic()
        if use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=5, context=ctx):
                pass
        else:
            with smtplib.SMTP(cfg['host'], cfg['port'], timeout=5) as s:
                s.ehlo()
        result['status'] = 'ok'
        result['latency_ms'] = round((time.monotonic() - t0) * 1000)
    except Exception as exc:
        result['status'] = f'error: {type(exc).__name__}'

    return result