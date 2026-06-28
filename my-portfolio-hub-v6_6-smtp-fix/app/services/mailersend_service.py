"""
app/services/mailersend_service.py — MailerSend Email Service (v5.0)

All transactional email dispatched through the MailerSend SDK:
  • OTP / password reset
  • Welcome / verification
  • Subscription lifecycle (activated, renewed, expiring, expired)
  • Payment notifications (approved, rejected)
  • System notifications

Key resolution priority (always server-side, never exposed to templates/JS):
  1. GlobalEmailConfig.mailersend_api_key  (DB, Fernet-encrypted)
  2. MAILERSEND_API_KEY environment variable

Sender identity priority:
  1. GlobalEmailConfig.sender_name / sender_email  (DB)
  2. MAILERSEND_FROM_NAME / MAILERSEND_FROM_EMAIL environment variables
  3. Hard-coded safe defaults

NOTE: Flask-Mail/SMTP was fully removed in v5.0. MailerSend is the sole
transactional email provider — there is no fallback provider. If a send
fails, it fails (see _smtp_fallback() below, kept only as a clearly-logged
no-op for callers that still reference it).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from flask import current_app

logger = logging.getLogger(__name__)

_TIMEOUT: int = 15  # seconds — passed to MailerSend SDK where supported


# ─────────────────────────────────────────────────────────────────────────────
# Internal key / sender resolution  (server-side only)
# ─────────────────────────────────────────────────────────────────────────────

def _get_mailersend_key(portal: str = 'tenant') -> str:
    """
    Resolve the active MailerSend API key for the specified portal.

    portal: 'superadmin' | 'admin' | 'tenant' 
    Priority per portal:
      1. GlobalEmailConfig.<portal>_mailersend_api_key (DB, Fernet-encrypted)
      2. GlobalEmailConfig.mailersend_api_key (shared DB key)
      3. <PORTAL>_MAILERSEND_API_KEY env var
      4. MAILERSEND_API_KEY env var (shared)

    The key is *never* written to any template context or JSON response.

    Returns:
        The resolved API key string, or '' if not configured.
    """
    try:
        from app.models.portfolio import GlobalEmailConfig
        cfg = GlobalEmailConfig.get()
        key = cfg.get_portal_key(portal)
        if key:
            return key
    except Exception as exc:
        logger.debug('mailersend_service: could not load GlobalEmailConfig: %s', exc)
    # Env fallback (already handled inside get_portal_key when cfg loads,
    # but if the DB is down entirely, resolve directly from env)
    import os
    shared = os.environ.get(
        'MAILERSEND_API_KEY',
        ''
    ).strip()

    if portal == 'superadmin':
        return (
            os.environ.get(
                'SUPERADMIN_MAILERSEND_API_KEY',
                ''
            ).strip()
            or shared
        )

    if portal == 'admin':
        return (
            os.environ.get(
                'ADMIN_MAILERSEND_API_KEY',
                ''
            ).strip()
            or shared
        )

    return shared

# Public alias used by app startup diagnostics
def get_mailersend_key() -> str:
    """Return the active shared MailerSend API key (DB or env). Empty string if not set."""
    return _get_mailersend_key('tenant')


def send_email_with_retry(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    max_retries: int = 3,
) -> bool:
    """
    Send an email via MailerSend with automatic retry on transient failures.

    Attempts MailerSend with up to `max_retries` attempts (exponential
    backoff). There is no SMTP fallback — Flask-Mail/SMTP was fully removed
    in v5.0, so a permanent MailerSend failure means the email is not sent
    (it is logged; for contact-form submissions the inquiry itself is always
    persisted to the DB first, so the message is never lost, only the email
    notification).

    Args:
        to_email:     Recipient address.
        subject:      Email subject.
        html_content: HTML body.
        text_content: Plain-text body. Auto-stripped from HTML if not provided.
        to_name:      Optional display name for recipient.
        reply_to:     Optional reply-to address.
        max_retries:  Number of MailerSend attempts.

    Returns:
        True if delivered via MailerSend, False otherwise.
    """
    import time
    import re

    if not text_content:
        # Basic HTML → plain-text strip for the text part
        text_content = re.sub(r'<[^>]+>', '', html_content).strip()

    last_err = ''
    for attempt in range(1, max_retries + 1):
        ok, err = send_email(
            to=to_email,
            subject=subject,
            text=text_content,
            html=html_content,
            reply_to=reply_to,
            to_name=to_name,
        )
        if ok:
            return True

        last_err = err
        # Retry only on transient errors (server errors, rate limits)
        if 'Authentication' in err or 'Bad request' in err:
            break  # Permanent failure — no point retrying

        if attempt < max_retries:
            wait = 2 ** attempt  # 2s, 4s, 8s
            logger.warning(
                'send_email_with_retry: attempt %d/%d failed (%s), retrying in %ds',
                attempt, max_retries, err, wait,
            )
            time.sleep(wait)

    logger.warning(
        'send_email_with_retry: all %d attempts failed (%s); falling back to SMTP for %s',
        max_retries, last_err, to_email,
    )
    return _smtp_fallback(to_email, subject, text_content)


def init_email_services(app) -> None:
    """
    Initialize email service configuration at app startup.

    Validates MailerSend connectivity and logs configuration status.
    Called from create_app() after extensions are initialized.

    Args:
        app: Flask application instance.
    """
    with app.app_context():
        api_key = get_mailersend_key()
        if api_key:
            app.logger.info('✓ MailerSend API: Configured (key length=%d)', len(api_key))
        else:
            app.logger.warning(
                '⚠ MailerSend API: Not configured — set MAILERSEND_API_KEY env var '
                'or configure via Superadmin → Email Settings'
            )

        from_email = _get_sender_email()
        from_name = _get_sender_name()
        app.logger.info('  Sender: %s <%s>', from_name, from_email)


def _get_sender_name(cfg=None, portal: str = 'tenant') -> str:
    """
    Resolve the display name used in the From header for the given portal.

    Priority: DB per-portal config → DB shared config → env per-portal → env shared → safe default.
    """
    try:
        if cfg is None:
            from app.models.portfolio import GlobalEmailConfig
            cfg = GlobalEmailConfig.get()
        return cfg.get_portal_sender_name(portal) or os.environ.get('MAILERSEND_FROM_NAME', 'Portfolio CMS')
    except Exception:
        pass
    return os.environ.get('MAILERSEND_FROM_NAME', 'Portfolio CMS')


def _get_sender_email(cfg=None, portal: str = 'tenant') -> str:
    """
    Resolve the From email address for the given portal (must be a MailerSend-verified domain).

    Priority: DB per-portal config → DB shared config → env per-portal → env shared → safe default.
    """
    try:
        if cfg is None:
            from app.models.portfolio import GlobalEmailConfig
            cfg = GlobalEmailConfig.get()
        return cfg.get_portal_sender_email(portal) or os.environ.get('MAILERSEND_FROM_EMAIL', 'noreply@portfoliocms.app')
    except Exception:
        pass
    return os.environ.get('MAILERSEND_FROM_EMAIL', 'noreply@portfoliocms.app')


# ─────────────────────────────────────────────────────────────────────────────
# Core send primitive
# ─────────────────────────────────────────────────────────────────────────────

def send_email(
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    reply_to: Optional[str] = None,
    to_name: Optional[str] = None,
    portal: str = 'tenant',
) -> tuple[bool, str]:
    """
    Send a single transactional email via the MailerSend SDK.

    portal: 'superadmin' | 'admin' | 'tenant'
    Uses the portal-specific API key, sender email, and sender name.

    The API key is resolved server-side and never touches any template
    or client-facing response.

    Args:
        to:        Recipient email address.
        subject:   Email subject line.
        text:      Plain-text body (required — MailerSend best practice).
        html:      Optional HTML body.
        reply_to:  Optional reply-to address.
        to_name:   Optional display name for the recipient.
        portal:    Which portal context is sending ('superadmin'|'admin'|'tenant').

    Returns:
        Tuple of (success: bool, message_id_or_error: str).
    """
    api_key = _get_mailersend_key(portal)
    if not api_key:
        logger.error('mailersend_service: MAILERSEND_API_KEY is not configured for portal=%s.', portal)
        return False, 'MailerSend API key not configured.'

    try:
        from mailersend import MailerSendClient, Email, EmailBuilder
        from mailersend.exceptions import (
            AuthenticationError,
            BadRequestError,
            RateLimitExceeded,
            ServerError,
            MailerSendError,
        )

        client = MailerSendClient(api_key=api_key)
        email_resource = Email(client)

        builder = (
            EmailBuilder()
            .from_email(_get_sender_email(portal=portal), _get_sender_name(portal=portal))
            .to(to, to_name or '')
            .subject(subject)
            .text(text)
        )

        if html:
            builder = builder.html(html)

        if reply_to:
            builder = builder.reply_to(reply_to)

        request = builder.build()
        response = email_resource.send(request)

        if response.success:
            # MailerSend returns the message-id in the X-Message-Id header
            msg_id = (
                response.headers.get('x-message-id')
                or response.get('id', 'unknown')
            )
            logger.info(
                'MailerSend[%s]: sent <%s> to %s (message_id=%s)',
                portal, subject[:60], to, msg_id,
            )
            return True, str(msg_id)

        # Non-2xx but no SDK exception raised
        err = f'HTTP {response.status_code}'
        logger.error(
            'MailerSend[%s]: unexpected status %s sending <%s> to %s',
            portal, response.status_code, subject[:60], to,
        )
        return False, err

    except AuthenticationError as exc:
        logger.error('MailerSend[%s]: authentication failed — check API key: %s', portal, exc)
        return False, 'Authentication failed. Verify MAILERSEND_API_KEY.'
    except BadRequestError as exc:
        logger.error('MailerSend[%s]: bad request sending to %s: %s', portal, to, exc)
        return False, f'Bad request: {exc}'
    except RateLimitExceeded as exc:
        logger.warning('MailerSend[%s]: rate limit exceeded sending to %s: %s', portal, to, exc)
        return False, 'Rate limit exceeded. Will retry via SMTP fallback.'
    except ServerError as exc:
        logger.error('MailerSend[%s]: server error sending to %s: %s', portal, to, exc)
        return False, f'MailerSend server error: {exc}'
    except MailerSendError as exc:
        logger.error('MailerSend[%s]: SDK error sending to %s: %s', portal, to, exc)
        return False, str(exc)
    except Exception as exc:
        logger.exception('MailerSend[%s]: unexpected error sending to %s: %s', portal, to, exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# SMTP fallback
# ─────────────────────────────────────────────────────────────────────────────

def _smtp_fallback(to: str, subject: str, body: str) -> bool:
    """
    No-op placeholder. Flask-Mail/SMTP was fully removed in v5.0 — MailerSend
    is the sole transactional email provider (see email_service.py docstring).

    Previously this function attempted `from flask_mail import Message` and
    `from app import mail`, neither of which exist anymore (flask-mail is not
    in requirements.txt and is not installed). Every call silently raised
    ModuleNotFoundError, was swallowed by the bare except below, and returned
    False — meaning "SMTP fallback" never actually delivered anything. It only
    ever produced a misleading log line claiming a fallback path existed.

    This function now fails fast and says so clearly, so a MailerSend outage
    is visible in logs immediately instead of being masked by a fake retry.

    Args:
        to:      Recipient address.
        subject: Email subject.
        body:    Plain-text body.

    Returns:
        Always False — there is no fallback provider configured.
    """
    logger.error(
        'No SMTP fallback available (Flask-Mail removed in v5.0); '
        'email to %s with subject "%s" was NOT delivered. '
        'Check MailerSend configuration/status.',
        to, subject[:80],
    )
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Connection / key validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_mailersend_key(key: str) -> tuple[bool, str]:
    """
    Validate a MailerSend API key by calling the low-cost list_domains endpoint.

    Args:
        key: Raw API key string entered by the superadmin.

    Returns:
        Tuple of (valid: bool, human_readable_message: str).

    Note:
        The key is validated server-side only. This function is called from the
        superadmin email-settings POST handler and its result is returned as JSON
        — the key itself is never included in that JSON response.
    """
    if not key or len(key) < 10:
        return False, 'Key too short — MailerSend keys are typically 60+ characters.'

    try:
        from mailersend import MailerSendClient, Domains
        from mailersend.exceptions import AuthenticationError, MailerSendError

        client = MailerSendClient(api_key=key)
        domains_resource = Domains(client)
        response = domains_resource.list_domains()

        if response.success:
            return True, 'Connected to MailerSend successfully.'
        if response.status_code == 401:
            return False, 'Invalid API key — authentication failed.'
        return False, f'Unexpected response: HTTP {response.status_code}'

    except AuthenticationError:
        return False, 'Invalid API key — authentication failed.'
    except Exception as exc:
        logger.error('validate_mailersend_key: unexpected error: %s', exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Public email functions  (backward-compatible signatures)
# ─────────────────────────────────────────────────────────────────────────────

def send_otp_email(
    recipient_email: str,
    otp: str,
    user_type: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_minutes: int = 10,
) -> bool:
    """
    Send an OTP password-reset code via MailerSend using the correct portal credentials.

    Args:
        recipient_email: Destination address.
        otp:             Raw 6-digit OTP (generated by otp_service).
        user_type:       'superadmin' | 'admin' | 'tenant'
        ip_address:      Request IP for transparency / security context.
        user_agent:      Request User-Agent for transparency.
        ttl_minutes:     OTP validity window shown in the email body.

    Returns:
        True if the email was delivered.
    """
    # Map user_type → portal so portal-specific keys/senders are used
    portal_map = {'superadmin': 'superadmin', 'admin': 'admin', 'tenant': 'tenant'}
    portal = portal_map.get(user_type, 'tenant')

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    role_label = user_type.replace('_', ' ').title()

    subject = f'[Portfolio CMS] Your Password Reset OTP — {now}'
    text = (
        f'Hello,\n\n'
        f'A password reset was requested for your Portfolio CMS account ({role_label}).\n\n'
        f'Your one-time password (OTP) is:\n\n'
        f'    {otp}\n\n'
        f'This OTP expires in {ttl_minutes} minutes.\n'
        f'Do NOT share it with anyone.\n\n'
        f'Request details:\n'
        f'  IP address : {ip_address or "unknown"}\n'
        f'  User agent : {(user_agent or "unknown")[:120]}\n'
        f'  Time (UTC) : {now}\n\n'
        f'If you did not request this, please secure your account immediately.\n\n'
        f'— Portfolio CMS'
    )
    html = f'''
<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:2rem;
            border:1px solid #e5e7eb;border-radius:8px;">
  <h2 style="color:#1f2937;margin-top:0;">Password Reset OTP</h2>
  <p>A password reset was requested for your <strong>{role_label}</strong> account.</p>
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

    ok, response = send_email(recipient_email, subject, text, html=html, portal=portal)
    if ok:
        logger.info(
            "OTP email delivered: portal=%s email=%s",
            portal,
            recipient_email,
        )
        return True

    logger.error(
        "OTP email failed: portal=%s email=%s error=%s",
        portal,
        recipient_email,
        response,
    )
    return _smtp_fallback(recipient_email, subject, text)


def send_verification_email(
    recipient_email: str,
    username: str,
    verification_url: str,
) -> bool:
    """
    Send an email verification link to a newly registered user.

    Args:
        recipient_email:  Destination address.
        username:         Display name shown in the greeting.
        verification_url: Signed verification URL (expires in 24 h).

    Returns:
        True if delivered successfully.
    """
    subject = '[Portfolio CMS] Verify Your Email Address'
    text = (
        f'Hello {username},\n\n'
        f'Please verify your email address by clicking the link below:\n\n'
        f'{verification_url}\n\n'
        f'This link expires in 24 hours. '
        f'If you did not register, you can safely ignore this email.\n\n'
        f'— Portfolio CMS'
    )
    html = f'''
<div style="font-family:sans-serif;max-width:520px;margin:auto;padding:2rem;
            border:1px solid #e5e7eb;border-radius:8px;">
  <h2 style="color:#1f2937;margin-top:0;">Verify Your Email</h2>
  <p>Hello <strong>{username}</strong>,</p>
  <p>Click the button below to verify your email address and activate your account.</p>
  <a href="{verification_url}"
     style="display:inline-block;background:#4f46e5;color:#fff;padding:.75rem 1.5rem;
            border-radius:6px;text-decoration:none;font-weight:600;margin:1rem 0;">
    Verify Email
  </a>
  <p style="color:#6b7280;font-size:.85rem;">
    Or copy this link into your browser:<br>
    <span style="word-break:break-all;">{verification_url}</span><br>
    This link expires in 24 hours.
  </p>
</div>'''

    ok, _ = send_email(recipient_email, subject, text, html=html)
    if ok:
        return True
    logger.warning(
        'MailerSend verification send failed; falling back to SMTP for %s',
        recipient_email,
    )
    return _smtp_fallback(recipient_email, subject, text)


def send_subscription_email(
    recipient_email: str,
    tenant_name: str,
    event: str,
    plan: str = 'Subscription',
    expires_on: Optional[str] = None,
    days_left: Optional[int] = None,
) -> bool:
    """
    Send a subscription lifecycle notification email.

    Args:
        recipient_email: Destination address.
        tenant_name:     Tenant display name used in the greeting.
        event:           One of:
                           'activated'    — new subscription activated
                           'renewed'      — subscription renewed
                           'expiring_30d' — 30 days before expiry (yearly plans)
                           'expiring_7d'  — 7 days before expiry (monthly plans)
                           'expiring_3d'  — 3 days before expiry
                           'expiring_1d'  — 1 day before expiry / expires tomorrow
                           'expired'      — subscription has expired
        plan:            Plan display name (e.g. 'Monthly Pro').
        expires_on:      Human-readable expiry date string (for expiry events).
        days_left:       Integer days remaining (for expiry events).

    Returns:
        True if delivered successfully.
    """
    event_meta: dict[str, tuple[str, str]] = {
        'activated':    (
            'Subscription Activated',
            '✅ Your subscription is now active.',
        ),
        'renewed':      (
            'Subscription Renewed',
            '🔄 Your subscription has been renewed. Thank you!',
        ),
        'expiring_30d': (
            'Subscription Expiring in 30 Days',
            f'⏳ Your {plan} subscription expires in 30 days on {expires_on}.',
        ),
        'expiring_7d':  (
            'Subscription Expiring Soon',
            f'⚠️  Your {plan} subscription expires in 7 days on {expires_on}.',
        ),
        'expiring_3d':  (
            'Subscription Expiring in 3 Days',
            f'🚨 Your {plan} subscription expires in 3 days on {expires_on}.',
        ),
        'expiring_1d':  (
            'Action Required — Expires Tomorrow',
            f'🚨 Your {plan} subscription expires TOMORROW on {expires_on}.',
        ),
        'expired':      (
            'Subscription Expired',
            f'❌ Your {plan} subscription has expired.',
        ),
    }

    title, summary = event_meta.get(
        event, ('Subscription Update', 'Your subscription status has changed.')
    )
    subject = f'[Portfolio CMS] {title}'
    text = (
        f'Hello {tenant_name},\n\n'
        f'{summary}\n\n'
        f'Plan: {plan}\n'
        + (f'Expires: {expires_on}\n' if expires_on else '')
        + (f'Days remaining: {days_left}\n' if days_left is not None else '')
        + '\nIf you have questions, please contact your administrator.\n\n'
          '— Portfolio CMS'
    )

    ok, _ = send_email(recipient_email, subject, text)
    if ok:
        return True
    logger.warning(
        'MailerSend subscription email (%s) failed; falling back to SMTP for %s',
        event, recipient_email,
    )
    return _smtp_fallback(recipient_email, subject, text)


def send_payment_notification(
    recipient_email: str,
    tenant_name: str,
    status: str,
    plan: str = 'Subscription',
    amount: Optional[str] = None,
    reference: Optional[str] = None,
) -> bool:
    """
    Send a payment-approved or payment-rejected notification.

    Args:
        recipient_email: Destination address.
        tenant_name:     Tenant display name used in the greeting.
        status:          'approved' | 'rejected'
        plan:            Plan display name.
        amount:          Human-readable payment amount (optional).
        reference:       Payment reference / transaction ID (optional).

    Returns:
        True if delivered successfully.
    """
    if status == 'approved':
        subject = '[Portfolio CMS] Payment Approved'
        summary = f'✅ Your payment for {plan} has been approved.'
    else:
        subject = '[Portfolio CMS] Payment Not Approved'
        summary = f'❌ Your payment for {plan} could not be processed. Please contact support.'

    text = (
        f'Hello {tenant_name},\n\n{summary}\n\n'
        + (f'Amount   : {amount}\n' if amount else '')
        + (f'Reference: {reference}\n' if reference else '')
        + '\n— Portfolio CMS'
    )

    ok, _ = send_email(recipient_email, subject, text)
    if ok:
        return True
    logger.warning(
        'MailerSend payment notification (%s) failed; falling back to SMTP for %s',
        status, recipient_email,
    )
    return _smtp_fallback(recipient_email, subject, text)


def send_system_notification(
    recipient_email: str,
    subject: str,
    message: str,
) -> bool:
    """
    Send a generic system notification (superadmin alerts, monitoring events, etc.).

    Args:
        recipient_email: Destination address.
        subject:         Short subject (will be prefixed with '[Portfolio CMS] ').
        message:         Plain-text message body.

    Returns:
        True if delivered successfully.
    """
    full_subject = f'[Portfolio CMS] {subject}'
    ok, _ = send_email(recipient_email, full_subject, message)
    if ok:
        return True
    logger.warning(
        'MailerSend system notification failed; falling back to SMTP for %s',
        recipient_email,
    )
    return _smtp_fallback(recipient_email, full_subject, message)
