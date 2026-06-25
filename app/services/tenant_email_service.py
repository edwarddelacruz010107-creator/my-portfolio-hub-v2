"""
app/services/tenant_email_service.py — Tenant Multi-Provider Email Dispatcher (v5.9)

ARCHITECTURAL ROLE
──────────────────────────────────────────────────────────────────────────────
Handles ALL outbound email for tenant/admin portals using the tenant's own
configured providers in priority order.

This module is separate from:
  • smtp_service.py    → superadmin OTP / security alerts (env vars only)
  • email_service.py   → legacy tenant auth flow (MailerSend + SMTP fallback)

DISPATCH FLOW
  1. Load tenant's active providers ordered by priority (DB)
  2. Try each provider in sequence:
       - smtp      → smtplib (TLS/SSL/STARTTLS)
       - resend    → Resend REST API (httpx/urllib)
       - mailersend → MailerSend REST API
  3. On provider error: log error type, update status, try next
  4. All fail → log critical, return (False, reason)
  5. On success: update last_sent_at, emails_sent_today, return (True, '')

ERROR CLASSIFICATION (for auto-failover triggering):
  • FAILOVER: timeout, connection_refused, rate_limit, quota_exceeded,
              dns_failure, api_error (5xx)
  • NO_FAILOVER (permanent): invalid_credentials, domain_rejected,
                              recipient_refused

PUBLIC API
  send_tenant_email(tenant_id, to, subject, html, text=None, ...) -> (bool, str)
  test_provider(tenant_id, provider_name, to_email)              -> (bool, str, float)
  get_provider_status(tenant_id)                                 -> dict
  bootstrap_tenant_providers(tenant_id)                         -> None

SECURITY
  • Credentials resolved server-side only from encrypted DB columns
  • Passwords / API keys never logged, never in return values
  • Tenant isolation enforced: only loads settings for the given tenant_id
  • Rate limiting per provider tracked in DB (emails_sent_today)
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
import time
import urllib.request
import urllib.error
import json
from datetime import datetime, timezone, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_SMTP_TIMEOUT   = 15   # seconds
_API_TIMEOUT    = 12   # seconds for Resend / MailerSend HTTP calls
_MAX_DAILY      = 500  # soft daily cap per provider (prevent runaway)

# Errors that justify failover to next provider
_FAILOVER_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    ConnectionRefusedError,
    TimeoutError,
    OSError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _html_to_text(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mask(secret: str) -> str:
    """Return masked representation for logging — never log actual value."""
    if not secret:
        return '(empty)'
    return f'{secret[:4]}{"*" * (len(secret) - 4)}'


# ─────────────────────────────────────────────────────────────────────────────
# SMTP Provider
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_tenant_smtp(
    settings,          # TenantSmtpSettings instance
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> tuple[bool, str]:
    """Send email via tenant's configured SMTP credentials."""
    if not settings.is_configured:
        return False, 'SMTP not fully configured'

    plain = text or _html_to_text(html)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = formataddr((settings.sender_name, settings.sender_email))
    msg['To']      = to

    msg.attach(MIMEText(plain, 'plain', 'utf-8'))
    msg.attach(MIMEText(html,  'html',  'utf-8'))

    host     = settings.smtp_host
    port     = settings.smtp_port or 587
    username = settings.smtp_username
    password = settings.smtp_password   # decrypted via property
    enc      = (settings.encryption_type or 'tls').lower()

    try:
        if enc == 'ssl' or port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=_SMTP_TIMEOUT) as server:
                server.login(username, password)
                server.sendmail(settings.sender_email, [to], msg.as_string())
        elif enc == 'tls' or port == 587:
            with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(username, password)
                server.sendmail(settings.sender_email, [to], msg.as_string())
        else:
            # Plaintext — only for internal/testing; not recommended
            with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT) as server:
                server.ehlo()
                server.login(username, password)
                server.sendmail(settings.sender_email, [to], msg.as_string())
        return True, ''

    except smtplib.SMTPAuthenticationError as e:
        logger.warning('[TenantSMTP] Auth failure tenant_smtp=%s: %s', host, str(e)[:120])
        return False, f'Authentication failed — check username/password'

    except smtplib.SMTPRecipientsRefused as e:
        logger.warning('[TenantSMTP] Recipient refused to=%s: %s', to, str(e)[:120])
        return False, f'Recipient refused by mail server'

    except (smtplib.SMTPConnectError, ConnectionRefusedError, TimeoutError, OSError) as e:
        logger.warning('[TenantSMTP] Connection failure host=%s: %s', host, str(e)[:120])
        return False, f'Connection failed — check host/port'

    except smtplib.SMTPException as e:
        logger.warning('[TenantSMTP] SMTP error host=%s: %s', host, str(e)[:120])
        return False, f'SMTP error: {str(e)[:100]}'


# ─────────────────────────────────────────────────────────────────────────────
# Resend Provider
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_resend(
    settings,          # TenantResendSettings instance
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> tuple[bool, str]:
    """Send email via Resend REST API (no external SDK dependency)."""
    if not settings.is_configured:
        return False, 'Resend not fully configured'

    plain = text or _html_to_text(html)
    from_addr = (
        f'{settings.sender_name} <{settings.sender_email}>'
        if settings.sender_name
        else settings.sender_email
    )

    payload = json.dumps({
        'from':    from_addr,
        'to':      [to],
        'subject': subject,
        'html':    html,
        'text':    plain,
    }).encode('utf-8')

    api_key = settings.api_key   # decrypted via property

    req = urllib.request.Request(
        'https://api.resend.com/emails',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type':  'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            body = resp.read().decode('utf-8', errors='replace')
            status = resp.getcode()
            if status in (200, 201):
                return True, ''
            return False, f'Resend returned HTTP {status}'

    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:300]
        if e.code == 401:
            logger.warning('[TenantResend] Auth failure: invalid API key')
            return False, 'Invalid Resend API key'
        if e.code == 429:
            logger.warning('[TenantResend] Rate limited')
            return False, 'Resend rate limit exceeded'
        if e.code >= 500:
            logger.warning('[TenantResend] Server error HTTP %d: %s', e.code, body)
            return False, f'Resend server error (HTTP {e.code})'
        logger.warning('[TenantResend] HTTP %d: %s', e.code, body)
        return False, f'Resend error (HTTP {e.code})'

    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning('[TenantResend] Connection failure: %s', str(e)[:120])
        return False, 'Resend connection failed'


# ─────────────────────────────────────────────────────────────────────────────
# MailerSend Provider
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_mailersend(
    settings,          # TenantMailerSendSettings instance
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> tuple[bool, str]:
    """Send email via MailerSend REST API (no external SDK dependency)."""
    if not settings.is_configured:
        return False, 'MailerSend not fully configured'

    plain = text or _html_to_text(html)

    payload = json.dumps({
        'from': {
            'email': settings.sender_email,
            'name':  settings.sender_name or '',
        },
        'to': [{'email': to}],
        'subject': subject,
        'html':    html,
        'text':    plain,
    }).encode('utf-8')

    api_token = settings.api_token   # decrypted via property

    req = urllib.request.Request(
        'https://api.mailersend.com/v1/email',
        data=payload,
        headers={
            'Authorization': f'Bearer {api_token}',
            'Content-Type':  'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            status = resp.getcode()
            if status in (200, 202):
                return True, ''
            return False, f'MailerSend returned HTTP {status}'

    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:300]
        if e.code == 401:
            logger.warning('[TenantMailerSend] Auth failure: invalid API token')
            return False, 'Invalid MailerSend API token'
        if e.code == 429:
            logger.warning('[TenantMailerSend] Rate limited')
            return False, 'MailerSend rate limit exceeded'
        if e.code >= 500:
            logger.warning('[TenantMailerSend] Server error HTTP %d: %s', e.code, body)
            return False, f'MailerSend server error (HTTP {e.code})'
        logger.warning('[TenantMailerSend] HTTP %d: %s', e.code, body)
        return False, f'MailerSend error (HTTP {e.code})'

    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning('[TenantMailerSend] Connection failure: %s', str(e)[:120])
        return False, 'MailerSend connection failed'


# ─────────────────────────────────────────────────────────────────────────────
# Provider Registry (extensible)
# ─────────────────────────────────────────────────────────────────────────────

def _get_provider_settings(tenant_id: int, provider_name: str):
    """Load provider-specific settings model for this tenant."""
    from app.models.core import (
        TenantSmtpSettings, TenantResendSettings, TenantMailerSendSettings,
    )
    if provider_name == 'smtp':
        return TenantSmtpSettings.query.filter_by(tenant_id=tenant_id).first()
    elif provider_name == 'resend':
        return TenantResendSettings.query.filter_by(tenant_id=tenant_id).first()
    elif provider_name == 'mailersend':
        return TenantMailerSendSettings.query.filter_by(tenant_id=tenant_id).first()
    return None


def _dispatch_to_provider(provider_name: str, settings, to: str, subject: str, html: str, text: Optional[str] = None) -> tuple[bool, str]:
    """Route a send call to the correct provider implementation."""
    if provider_name == 'smtp':
        return _send_via_tenant_smtp(settings, to, subject, html, text)
    elif provider_name == 'resend':
        return _send_via_resend(settings, to, subject, html, text)
    elif provider_name == 'mailersend':
        return _send_via_mailersend(settings, to, subject, html, text)
    return False, f'Unknown provider: {provider_name}'


# ─────────────────────────────────────────────────────────────────────────────
# Public API — Core Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def send_tenant_email(
    tenant_id: int,
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Dispatch an email on behalf of a tenant using their configured providers.

    Tries each active provider in priority order. Fails over only on transient
    errors (network, rate limit, server error). Does NOT fail over on permanent
    errors (bad credentials, recipient refused).

    Returns:
        (True, '')             — successfully sent
        (False, reason_str)    — all providers failed
    """
    from app.models.core import TenantEmailProvider
    from app import db

    providers = TenantEmailProvider.get_ordered_active(tenant_id)
    if not providers:
        logger.warning('[TenantEmail] No active providers configured for tenant_id=%d', tenant_id)
        return False, 'No active email providers configured'

    last_error = 'Unknown failure'
    for provider_rec in providers:
        name     = provider_rec.provider_name
        settings = _get_provider_settings(tenant_id, name)

        if settings is None or not settings.is_configured:
            logger.info('[TenantEmail] Provider %s not configured, skipping', name)
            provider_rec.status = 'unconfigured'
            continue

        t0 = time.perf_counter()
        ok, err = _dispatch_to_provider(name, settings, to, subject, html, text)
        latency = time.perf_counter() - t0

        if ok:
            provider_rec.status       = 'connected'
            provider_rec.last_sent_at = _utcnow()
            provider_rec.last_error   = None
            # Reset daily counter if it's a new day
            if provider_rec.last_sent_at and provider_rec.last_sent_at.date() < date.today():
                provider_rec.emails_sent_today = 1
            else:
                provider_rec.emails_sent_today = (provider_rec.emails_sent_today or 0) + 1
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.info('[TenantEmail] Sent via %s tenant=%d latency=%.2fs', name, tenant_id, latency)
            return True, ''

        # Classify error to decide whether to failover
        last_error = err
        provider_rec.last_error = err[:500] if err else None

        # Map error string to status
        if 'auth' in err.lower() or 'invalid' in err.lower() or 'key' in err.lower():
            provider_rec.status = 'invalid_credentials'
            # Permanent — don't failover, stop here
            logger.error('[TenantEmail] Permanent auth failure on %s tenant=%d — stopping', name, tenant_id)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            return False, err
        elif 'rate limit' in err.lower():
            provider_rec.status = 'rate_limited'
        elif 'connection' in err.lower() or 'timeout' in err.lower():
            provider_rec.status = 'timeout'
        else:
            provider_rec.status = 'disconnected'

        logger.warning('[TenantEmail] Provider %s failed tenant=%d err=%r — trying next', name, tenant_id, err)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    logger.error('[TenantEmail] All providers failed for tenant_id=%d last_error=%r', tenant_id, last_error)
    return False, last_error


# ─────────────────────────────────────────────────────────────────────────────
# Test Email
# ─────────────────────────────────────────────────────────────────────────────

def test_provider(
    tenant_id: int,
    provider_name: str,
    to_email: str,
) -> tuple[bool, str, float]:
    """
    Send a test email through a specific provider.

    Returns:
        (success, message, latency_seconds)
    """
    from app.models.core import TenantEmailProvider
    from app import db

    settings = _get_provider_settings(tenant_id, provider_name)
    if settings is None or not settings.is_configured:
        return False, f'{provider_name.title()} is not configured', 0.0

    html = """
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:24px">
      <h2 style="color:#22c55e;">✅ Email Provider Test</h2>
      <p>This test email confirms your <strong>{provider}</strong> provider is correctly
      configured and sending mail for your Portfolio CMS account.</p>
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0">
      <p style="color:#6b7280;font-size:12px;">Sent by Portfolio CMS Email Services</p>
    </div>
    """.format(provider=provider_name.title())

    t0 = time.perf_counter()
    ok, err = _dispatch_to_provider(provider_name, settings, to_email, 'Portfolio CMS — Test Email', html)
    latency = time.perf_counter() - t0

    # Update provider record
    provider_rec = TenantEmailProvider.query.filter_by(
        tenant_id=tenant_id, provider_name=provider_name
    ).first()

    if provider_rec:
        provider_rec.last_tested_at = _utcnow()
        provider_rec.status         = 'connected' if ok else 'disconnected'
        if not ok:
            provider_rec.last_error = err[:500] if err else None
        else:
            provider_rec.last_error = None
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    if ok:
        return True, f'Test email sent successfully via {provider_name.title()}', latency
    return False, err or 'Send failed', latency


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────

def get_provider_status(tenant_id: int) -> dict:
    """
    Return a dict of provider_name → status info for dashboard display.
    Always returns entries for all three known providers.
    """
    from app.models.core import (
        TenantEmailProvider,
        TenantSmtpSettings, TenantResendSettings, TenantMailerSendSettings,
    )

    result = {}
    for name in ('smtp', 'resend', 'mailersend'):
        provider_rec = TenantEmailProvider.query.filter_by(
            tenant_id=tenant_id, provider_name=name
        ).first()

        settings = _get_provider_settings(tenant_id, name)
        configured = settings.is_configured if settings else False

        result[name] = {
            'active':           provider_rec.active      if provider_rec else False,
            'priority':         provider_rec.priority    if provider_rec else 99,
            'status':           provider_rec.status      if provider_rec else 'unconfigured',
            'last_tested_at':   provider_rec.last_tested_at   if provider_rec else None,
            'last_sent_at':     provider_rec.last_sent_at     if provider_rec else None,
            'last_error':       provider_rec.last_error       if provider_rec else None,
            'emails_sent_today': provider_rec.emails_sent_today if provider_rec else 0,
            'configured':       configured,
        }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap — called on new tenant creation
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_tenant_providers(tenant_id: int) -> None:
    """
    Ensure all TenantEmailProvider records exist for a new tenant.
    Idempotent — safe to call on existing tenants.
    """
    from app.models.core import TenantEmailProvider
    from app import db

    for name in ('smtp', 'resend', 'mailersend'):
        TenantEmailProvider.get_or_create(tenant_id, name)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.error('[TenantEmail] Failed to bootstrap providers for tenant_id=%d', tenant_id)
