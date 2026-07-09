"""
app/services/superadmin_email_service.py — Superadmin Multi-Provider Email Dispatcher (v5.9.1)

Sends ALL superadmin-portal transactional email (OTP, password reset) through
the configured provider chain in priority order with automatic failover.

Provider resolution order (DB config → env fallback):
  1. MailerSend  — DB: GlobalEmailConfig.superadmin_mailersend_api_key (via get_portal_key)
  2. SMTP        — DB: sa_smtp_* columns → env: SUPERADMIN_SMTP_* → SMTP_*
  3. Resend      — DB: sa_resend_api_key → env: RESEND_API_KEY

Only ACTIVE providers in priority order are tried. Transient failures
automatically fall through to the next provider.

This service is used by:
  • password_reset_service.py   — admin/superadmin OTP delivery
  • superadmin/__init__.py      — superadmin OTP
"""
from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
import time
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger(__name__)

_SMTP_TIMEOUT = 30
_MAX_RETRIES  = 1
_BACKOFF      = 1.5

_TRANSIENT_SMTP = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    ConnectionRefusedError,
    TimeoutError,
    OSError,
)


def _clean_sender_name(value: str | None) -> str:
    name = (value or '').strip()
    # Old installs often have the generic default persisted. For signup OTPs
    # and password/security mail, the public brand should be clear.
    return 'MyPortfolioHub' if not name or name == 'Portfolio CMS' else name


def _safe_error(value: str | None, limit: int = 180) -> str:
    text = str(value or '').replace('\n', ' ').replace('\r', ' ')
    text = re.sub(r'(Bearer\s+)[A-Za-z0-9._\-]+', r'\1[redacted]', text)
    text = re.sub(r'(api[_-]?key["\':= ]+)[^,} ]+', r'\1[redacted]', text, flags=re.I)
    return text[:limit]


def _reply_to_for(sender_email: str) -> str:
    return (
        os.environ.get('SUPERADMIN_REPLY_TO_EMAIL', '').strip()
        or os.environ.get('EMAIL_REPLY_TO', '').strip()
        or os.environ.get('REPLY_TO_EMAIL', '').strip()
        or sender_email
    )


# ─────────────────────────────────────────────────────────────────────────────
# SMTP send
# ─────────────────────────────────────────────────────────────────────────────

def _send_smtp(cfg: dict, to: str, subject: str, html: str, text: str) -> tuple[bool, str]:
    """Send via SMTP. cfg keys: host, port, username, password, sender_email, sender_name, encryption."""
    host     = cfg['host']
    port     = cfg.get('port', 587)
    username = cfg['username']
    password = cfg['password']
    from_email = cfg['sender_email']
    from_name  = _clean_sender_name(cfg.get('sender_name', 'MyPortfolioHub'))
    reply_to   = cfg.get('reply_to') or _reply_to_for(from_email)
    enc      = (cfg.get('encryption') or 'tls').lower()

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = formataddr((from_name, from_email))
    msg['To']      = to
    if reply_to:
        msg['Reply-To'] = reply_to
    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    msg.attach(MIMEText(html,  'html',  'utf-8'))

    try:
        if enc == 'ssl' or port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=_SMTP_TIMEOUT) as s:
                s.login(username, password)
                s.sendmail(from_email, [to], msg.as_string())
        elif enc == 'none':
            with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT) as s:
                s.login(username, password)
                s.sendmail(from_email, [to], msg.as_string())
        else:  # tls (STARTTLS default)
            with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT) as s:
                s.ehlo(); s.starttls(context=ssl.create_default_context()); s.ehlo()
                s.login(username, password)
                s.sendmail(from_email, [to], msg.as_string())
        return True, ''
    except smtplib.SMTPAuthenticationError:
        return False, 'SMTP authentication failed'
    except smtplib.SMTPRecipientsRefused:
        return False, 'Recipient refused'
    except _TRANSIENT_SMTP as e:
        raise  # let caller retry
    except smtplib.SMTPException as e:
        return False, f'SMTP error: {str(e)[:120]}'


# ─────────────────────────────────────────────────────────────────────────────
# Resend send
# ─────────────────────────────────────────────────────────────────────────────

def _send_resend(cfg: dict, to: str, subject: str, html: str, text: str) -> tuple[bool, str]:
    api_key = cfg['api_key']
    from_email = cfg['sender_email']
    from_name  = _clean_sender_name(cfg.get('sender_name', 'MyPortfolioHub'))
    reply_to   = cfg.get('reply_to') or _reply_to_for(from_email)

    payload = json.dumps({
        'from':    f'{from_name} <{from_email}>',
        'to':      [to],
        'subject': subject,
        'html':    html,
        'text':    text,
        'reply_to': reply_to,
    }).encode()

    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status in (200, 201):
                return True, ''
            return False, f'Resend HTTP {resp.status}'
    except urllib.error.HTTPError as e:
        body = e.read(200).decode(errors='ignore')
        if e.code == 401:
            return False, 'Resend: invalid API key'
        if e.code in (429, 503):
            raise ConnectionError(f'Resend transient {e.code}')
        return False, f'Resend HTTP {e.code}: {body}'
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ConnectionError(f'Resend network error: {e}')


# ─────────────────────────────────────────────────────────────────────────────
# MailerSend send
# ─────────────────────────────────────────────────────────────────────────────

def _send_mailersend(cfg: dict, to: str, subject: str, html: str, text: str) -> tuple[bool, str]:
    api_key = cfg['api_key']
    from_email = cfg['sender_email']
    from_name  = _clean_sender_name(cfg.get('sender_name', 'MyPortfolioHub'))
    reply_to   = cfg.get('reply_to') or _reply_to_for(from_email)

    payload = json.dumps({
        'from': {'email': from_email, 'name': from_name},
        'to':   [{'email': to}],
        'subject': subject,
        'html': html,
        'text': text,
        'reply_to': {'email': reply_to} if reply_to else None,
    }).encode()

    req = urllib.request.Request(
        'https://api.mailersend.com/v1/email',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type':  'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status in (200, 202):
                return True, ''
            return False, f'MailerSend HTTP {resp.status}'
    except urllib.error.HTTPError as e:
        body = e.read(200).decode(errors='ignore')
        if e.code == 401:
            return False, 'MailerSend: invalid API key'
        if e.code in (429, 503):
            raise ConnectionError(f'MailerSend transient {e.code}')
        return False, f'MailerSend HTTP {e.code}: {body}'
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ConnectionError(f'MailerSend network error: {e}')


# ─────────────────────────────────────────────────────────────────────────────
# Config resolution (DB → env fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_configs() -> list[dict]:
    """
    Return list of provider configs in priority order (only active + configured).
    Each dict has keys: provider, <provider-specific credentials>
    """
    try:
        from app.models.core import GlobalEmailConfig
        # fresh=True is required here: this is the actual send-time credential
        # resolver. Without it, a stale identity-mapped GlobalEmailConfig
        # instance (loaded earlier in this worker/thread's lifetime, before
        # the superadmin last saved SMTP/MailerSend/Resend settings) can be
        # returned instead of the just-committed row, so "Save SMTP" appears
        # to succeed in the UI but real deliveries keep using the old
        # credentials. See app/superadmin/routes/email_settings.py for the
        # same fix already applied to the settings/status endpoints.
        cfg = GlobalEmailConfig.get(fresh=True)
    except Exception:
        cfg = None

    configs = []

    def _e(key, fallback=''):
        return os.environ.get(key, '').strip() or fallback

    priority = cfg.get_sa_provider_priority() if cfg else ['mailersend', 'smtp', 'resend']

    for provider in priority:
        if provider == 'mailersend':
            active = cfg.sa_mailersend_active if cfg is not None and cfg.sa_mailersend_active is not None else True
            if not active:
                continue
            api_key = cfg.get_portal_key('superadmin') if cfg else ''
            api_key = api_key or _e('SUPERADMIN_MAILERSEND_API_KEY') or _e('MAILERSEND_API_KEY')
            sender_email = (cfg.get_portal_sender_email('superadmin') if cfg else '') or _e('MAILERSEND_FROM_EMAIL')
            sender_name  = _clean_sender_name((cfg.get_portal_sender_name('superadmin') if cfg else '') or _e('MAILERSEND_FROM_NAME', 'MyPortfolioHub'))
            if api_key and sender_email:
                configs.append({
                    'provider': 'mailersend',
                    'api_key': api_key,
                    'sender_email': sender_email,
                    'sender_name': sender_name,
                    'reply_to': _reply_to_for(sender_email),
                })

        elif provider == 'smtp':
            active = cfg.sa_smtp_active if cfg is not None and cfg.sa_smtp_active is not None else False
            # DB first, then env
            host     = (cfg.sa_smtp_host if cfg else '') or _e('SUPERADMIN_SMTP_HOST') or _e('SMTP_HOST')
            port_raw = (cfg.sa_smtp_port if cfg else None) or int(_e('SUPERADMIN_SMTP_PORT') or _e('SMTP_PORT') or '587')
            username = (cfg.sa_smtp_username if cfg else '') or _e('SUPERADMIN_SMTP_USERNAME') or _e('SMTP_USERNAME')
            password = ''
            try:
                password = (cfg.sa_smtp_password if cfg else '') or _e('SUPERADMIN_SMTP_PASSWORD') or _e('SMTP_PASSWORD')
            except Exception:
                password = _e('SUPERADMIN_SMTP_PASSWORD') or _e('SMTP_PASSWORD')
            sender_email = (cfg.sa_smtp_sender_email if cfg else '') or _e('SUPERADMIN_FROM_EMAIL') or _e('SMTP_FROM_EMAIL')
            sender_name  = _clean_sender_name((cfg.sa_smtp_sender_name if cfg else '') or _e('SUPERADMIN_FROM_NAME') or _e('SMTP_FROM_NAME', 'MyPortfolioHub'))
            encryption   = (cfg.sa_smtp_encryption if cfg else '') or 'tls'
            # Sender email falls back to username when blank (common Gmail setup)
            sender_email = sender_email or username
            if host and username and password and active:
                configs.append({
                    'provider': 'smtp',
                    'host': host,
                    'port': port_raw,
                    'username': username,
                    'password': password,
                    'sender_email': sender_email,
                    'sender_name': sender_name,
                    'encryption': encryption,
                    'reply_to': _reply_to_for(sender_email),
                })

        elif provider == 'resend':
            active = cfg.sa_resend_active if cfg is not None and cfg.sa_resend_active is not None else False
            if not active:
                continue
            api_key = (cfg.sa_resend_api_key if cfg else '') or _e('RESEND_API_KEY')
            sender_email = (cfg.sa_resend_sender_email if cfg else '') or _e('RESEND_FROM_EMAIL')
            sender_name  = _clean_sender_name((cfg.sa_resend_sender_name if cfg else '') or _e('RESEND_FROM_NAME', 'MyPortfolioHub'))
            if api_key and sender_email:
                configs.append({
                    'provider': 'resend',
                    'api_key': api_key,
                    'sender_email': sender_email,
                    'sender_name': sender_name,
                    'reply_to': _reply_to_for(sender_email),
                })

    return configs


def _html_to_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'[ \t]+', ' ', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def send_email(
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Send via first available active provider in priority order.
    Falls back through the chain on transient errors.
    Returns (True, '') or (False, last_error).
    """
    plain = text or _html_to_text(html)
    providers = _resolve_configs()
    provider_names = [str(p.get('provider') or 'unknown') for p in providers]
    logger.info('[SAEmail] Provider candidates=%s to=%s subject=%r', provider_names or ['legacy-env-smtp'], to, subject)

    if not providers:
        # Last-resort: env-only SMTP (backward compat)
        from app.services.superadmin_smtp_service import send_email as _legacy_send
        logger.warning('[SAEmail] No DB providers active — falling back to env-only SMTP')
        return _legacy_send(to=to, subject=subject, html=html, text=plain)

    last_err = 'No providers configured'
    for pcfg in providers:
        name = pcfg['provider']
        logger.info('[SAEmail] Selected provider candidate=%s to=%s', name, to)
        for attempt in range(1 + _MAX_RETRIES):
            try:
                if name == 'mailersend':
                    ok, err = _send_mailersend(pcfg, to, subject, html, plain)
                elif name == 'smtp':
                    ok, err = _send_smtp(pcfg, to, subject, html, plain)
                elif name == 'resend':
                    ok, err = _send_resend(pcfg, to, subject, html, plain)
                else:
                    ok, err = False, f'Unknown provider {name}'

                if ok:
                    logger.info('[SAEmail] Sent via %s to=%s subject=%r', name, to, subject)
                    return True, ''
                last_err = _safe_error(err)
                if 'invalid' in err.lower() or 'refused' in err.lower() or 'authentication' in err.lower():
                    break  # permanent — skip to next provider
            except (ConnectionError, OSError, TimeoutError) as e:
                last_err = f'{name}: {type(e).__name__}'
                logger.warning('[SAEmail] Transient %s attempt=%d: %s', name, attempt + 1, _safe_error(str(e), 80))
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF)
                continue
            break  # non-transient failure — try next provider
        # next provider

    logger.error('[SAEmail] All providers failed for to=%s: %s', to, _safe_error(last_err))
    return False, last_err


def send_otp(
    email: str,
    otp: str,
    portal: str = 'superadmin',
    ip_address: str = '',
    ttl_minutes: int = 10,
) -> tuple[bool, str]:
    """Send OTP email for superadmin or admin portal password reset."""
    portal_label = 'Superadmin' if portal == 'superadmin' else 'Admin'
    html = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px;background:#0f172a;color:#f1f5f9;border-radius:12px;">
      <div style="text-align:center;margin-bottom:28px;">
        <div style="display:inline-block;background:#1e293b;border-radius:8px;padding:12px 20px;border:1px solid #334155;">
          <span style="font-size:20px;font-weight:700;color:#38bdf8;letter-spacing:0.05em;">PORTFOLIO CMS</span>
        </div>
      </div>
      <h2 style="text-align:center;color:#f1f5f9;margin-bottom:8px;font-size:22px;">{portal_label} Verification Code</h2>
      <p style="text-align:center;color:#94a3b8;margin-bottom:28px;">Your one-time password for {portal_label.lower()} access</p>
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
      <p style="text-align:center;color:#475569;font-size:11px;">Portfolio CMS &bull; Security System</p>
    </div>
    """
    return send_email(email, f'Portfolio CMS — {portal_label} Verification Code', html)
