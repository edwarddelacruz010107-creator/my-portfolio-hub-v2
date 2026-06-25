"""
app/services/superadmin_smtp_service.py — Isolated Superadmin SMTP Service (v5.9)

ARCHITECTURAL ROLE
──────────────────────────────────────────────────────────────────────────────
Dedicated SMTP transport for ALL superadmin-level email. Completely isolated
from tenant providers, GlobalEmailConfig, and any third-party API SDKs.

ISOLATION CONTRACT:
  • Reads ONLY from SUPERADMIN_SMTP_* environment variables — never from DB
  • No imports from mailersend_service, email_service, or tenant providers
  • Cannot be disrupted by tenant misconfiguration, DB degradation, or
    MailerSend outages
  • Zero cross-tenant data access

SCOPE — used for:
  • Superadmin OTP delivery
  • Superadmin password reset
  • Platform-level security alerts
  • Owner account recovery
  • Global platform notifications

FALLS BACK TO:
  • smtp_service.py shared SMTP (SMTP_* vars) if SUPERADMIN_SMTP_HOST not set
    This ensures backward compatibility with existing deployments.

ENV VARS (preferred):
  SUPERADMIN_SMTP_HOST
  SUPERADMIN_SMTP_PORT         (default: 587)
  SUPERADMIN_SMTP_USERNAME
  SUPERADMIN_SMTP_PASSWORD
  SUPERADMIN_SMTP_USE_TLS      (default: true)
  SUPERADMIN_SMTP_USE_SSL      (default: false — only for port 465)
  SUPERADMIN_FROM_EMAIL
  SUPERADMIN_FROM_NAME         (default: "Portfolio CMS Admin")

SECURITY:
  • TLS enforced — never plaintext
  • Credentials never logged
  • 30s hard socket timeout
  • Retry on transient errors (max 2 retries, 1.5s backoff)
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
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger(__name__)

_TIMEOUT     = 30    # seconds
_MAX_RETRIES = 2
_BACKOFF     = 1.5   # seconds


# ─────────────────────────────────────────────────────────────────────────────
# Configuration resolution (env vars only — no DB)
# ─────────────────────────────────────────────────────────────────────────────

def _superadmin_config() -> dict:
    """
    Resolve superadmin SMTP configuration from environment variables.
    Falls back to shared SMTP_* vars for backward compatibility.
    """
    def _e(key: str, fallback_key: str = '', default: str = '') -> str:
        return (
            os.environ.get(key, '').strip()
            or (os.environ.get(fallback_key, '').strip() if fallback_key else '')
            or default
        )

    host     = _e('SUPERADMIN_SMTP_HOST',     'SMTP_HOST')
    port_raw = _e('SUPERADMIN_SMTP_PORT',     'SMTP_PORT', '587')
    username = _e('SUPERADMIN_SMTP_USERNAME', 'SMTP_USERNAME')
    password = _e('SUPERADMIN_SMTP_PASSWORD', 'SMTP_PASSWORD')
    from_email = _e('SUPERADMIN_FROM_EMAIL',  'SMTP_FROM_EMAIL')
    from_name  = _e('SUPERADMIN_FROM_NAME',   'SMTP_FROM_NAME', 'Portfolio CMS Admin')

    tls_raw = os.environ.get('SUPERADMIN_SMTP_USE_TLS', '').strip().lower()
    ssl_raw = os.environ.get('SUPERADMIN_SMTP_USE_SSL', '').strip().lower()

    # Default TLS on unless SSL explicitly requested
    use_ssl = ssl_raw in ('1', 'true', 'yes')
    use_tls = not use_ssl  # TLS (STARTTLS) is the default

    if tls_raw:
        use_tls = tls_raw in ('1', 'true', 'yes')

    try:
        port = int(port_raw)
    except (ValueError, TypeError):
        port = 587

    return {
        'host':       host,
        'port':       port,
        'username':   username,
        'password':   password,
        'from_email': from_email,
        'from_name':  from_name,
        'use_tls':    use_tls,
        'use_ssl':    use_ssl,
    }


def _is_configured(cfg: dict) -> bool:
    return bool(cfg['host'] and cfg['username'] and cfg['password'] and cfg['from_email'])


def validate_configuration() -> list[str]:
    """Return list of warning strings for missing/incomplete config."""
    cfg = _superadmin_config()
    issues = []
    if not cfg['host']:
        issues.append('SUPERADMIN_SMTP_HOST not set (falling back to SMTP_HOST)')
    if not cfg['username']:
        issues.append('SUPERADMIN_SMTP_USERNAME not set')
    if not cfg['password']:
        issues.append('SUPERADMIN_SMTP_PASSWORD not set')
    if not cfg['from_email']:
        issues.append('SUPERADMIN_FROM_EMAIL not set')
    return issues


# ─────────────────────────────────────────────────────────────────────────────
# Internal SMTP sender
# ─────────────────────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _attempt_send(cfg: dict, to: str, subject: str, html: str, text: str) -> tuple[bool, str]:
    """Single SMTP send attempt — raises on transient errors for retry logic."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = formataddr((cfg['from_name'], cfg['from_email']))
    msg['To']      = to
    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html',  'utf-8'))

    host, port = cfg['host'], cfg['port']

    try:
        if cfg['use_ssl'] or port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=_TIMEOUT) as server:
                server.login(cfg['username'], cfg['password'])
                server.sendmail(cfg['from_email'], [to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(cfg['username'], cfg['password'])
                server.sendmail(cfg['from_email'], [to], msg.as_string())
        return True, ''

    except smtplib.SMTPAuthenticationError:
        # Permanent — do not retry
        logger.error('[SuperadminSMTP] Authentication failed — check credentials (host=%s)', host)
        return False, 'Authentication failed'

    except smtplib.SMTPRecipientsRefused:
        logger.error('[SuperadminSMTP] Recipient refused to=%s', to)
        return False, 'Recipient refused'

    except (smtplib.SMTPConnectError, ConnectionRefusedError, TimeoutError, OSError) as e:
        # Transient — allow retry
        raise

    except smtplib.SMTPException as e:
        logger.error('[SuperadminSMTP] SMTP error: %s', str(e)[:200])
        return False, f'SMTP error: {str(e)[:100]}'


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def send_email(
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    *,
    to_name: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Send an email through the isolated superadmin SMTP channel.

    Args:
        to:      Recipient email
        subject: Email subject
        html:    HTML body
        text:    Optional plain-text fallback (auto-generated if omitted)

    Returns:
        (True, '')          — sent successfully
        (False, reason)     — all attempts failed
    """
    cfg = _superadmin_config()
    if not _is_configured(cfg):
        issues = validate_configuration()
        logger.critical(
            '[SuperadminSMTP] Not configured — cannot send. Issues: %s',
            '; '.join(issues)
        )
        return False, 'Superadmin SMTP not configured'

    plain = text or _html_to_text(html)

    last_err = 'Unknown error'
    for attempt in range(1 + _MAX_RETRIES):
        try:
            ok, err = _attempt_send(cfg, to, subject, html, plain)
            if ok:
                logger.info('[SuperadminSMTP] Sent to=%s subject=%r attempt=%d', to, subject, attempt + 1)
                return True, ''
            if err in ('Authentication failed', 'Recipient refused'):
                return False, err  # permanent — don't retry
            last_err = err
        except (smtplib.SMTPConnectError, ConnectionRefusedError, TimeoutError, OSError) as e:
            last_err = f'Connection failed: {type(e).__name__}'
            logger.warning(
                '[SuperadminSMTP] Transient error attempt=%d/%d: %s',
                attempt + 1, 1 + _MAX_RETRIES, str(e)[:120]
            )

        if attempt < _MAX_RETRIES:
            time.sleep(_BACKOFF * (attempt + 1))

    logger.error('[SuperadminSMTP] All %d attempts failed for to=%s: %s', 1 + _MAX_RETRIES, to, last_err)
    return False, last_err


def send_superadmin_otp(
    email: str,
    otp: str,
    ip_address: str = '',
    user_agent: str = '',
    ttl_minutes: int = 10,
) -> tuple[bool, str]:
    """Send OTP code to superadmin via the isolated SMTP channel."""
    html = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px;background:#0f172a;color:#f1f5f9;border-radius:12px;">
      <div style="text-align:center;margin-bottom:28px;">
        <div style="display:inline-block;background:#1e293b;border-radius:8px;padding:12px 20px;border:1px solid #334155;">
          <span style="font-size:20px;font-weight:700;color:#38bdf8;letter-spacing:0.05em;">PORTFOLIO CMS</span>
        </div>
      </div>
      <h2 style="text-align:center;color:#f1f5f9;margin-bottom:8px;font-size:22px;">Superadmin Verification Code</h2>
      <p style="text-align:center;color:#94a3b8;margin-bottom:28px;">Your one-time password for superadmin access</p>
      <div style="text-align:center;background:#1e293b;border:1px solid #334155;border-radius:12px;padding:28px;margin-bottom:24px;">
        <div style="font-size:44px;font-weight:800;letter-spacing:0.18em;color:#38bdf8;font-family:monospace;">{otp}</div>
        <p style="color:#94a3b8;font-size:13px;margin-top:12px;">Expires in {ttl_minutes} minutes</p>
      </div>
      <div style="background:#1e293b;border-left:3px solid #f59e0b;border-radius:4px;padding:16px;margin-bottom:20px;">
        <p style="margin:0;color:#fbbf24;font-size:13px;font-weight:600;">Security Notice</p>
        <p style="margin:6px 0 0;color:#94a3b8;font-size:12px;">
          IP: {ip_address or 'unknown'}<br>
          If you did not request this code, secure your account immediately.
        </p>
      </div>
      <p style="text-align:center;color:#475569;font-size:11px;">Portfolio CMS &bull; Superadmin Security System</p>
    </div>
    """
    return send_email(email, 'Portfolio CMS — Superadmin Verification Code', html)


def send_security_alert(
    email: str,
    event: str,
    ip_address: str = '',
    user_agent: str = '',
    detail: str = '',
) -> tuple[bool, str]:
    """Send a security alert to the superadmin."""
    html = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px;background:#1a0000;color:#fef2f2;border-radius:12px;border:1px solid #7f1d1d;">
      <h2 style="color:#fca5a5;margin-bottom:4px;">🚨 Security Alert</h2>
      <p style="color:#fca5a5;opacity:.8;margin-bottom:24px;">Portfolio CMS — Superadmin Portal</p>
      <div style="background:#2d0000;border-radius:8px;padding:20px;margin-bottom:20px;">
        <p style="margin:0 0 8px;font-size:14px;"><strong>Event:</strong> {event}</p>
        <p style="margin:0 0 8px;font-size:14px;"><strong>IP Address:</strong> {ip_address or 'unknown'}</p>
        {f'<p style="margin:0;font-size:13px;color:#fca5a5;opacity:.7;">{detail}</p>' if detail else ''}
      </div>
      <p style="color:#7f1d1d;font-size:11px;text-align:center;">Portfolio CMS Security System</p>
    </div>
    """
    return send_email(email, f'Portfolio CMS — Security Alert: {event}', html)


def health_check() -> dict:
    """Return status dict for /health/email endpoint."""
    cfg   = _superadmin_config()
    ready = _is_configured(cfg)
    issues = validate_configuration() if not ready else []
    return {
        'superadmin_smtp': {
            'configured': ready,
            'host':       cfg['host'] or None,
            'port':       cfg['port'],
            'issues':     issues,
        }
    }
