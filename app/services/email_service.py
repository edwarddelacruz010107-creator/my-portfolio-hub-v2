"""
app/services/email_service.py — Production Email Service (v5.7.1)

SCOPE: This module serves the TENANT and ADMIN reset/notification flows
ONLY. Superadmin OTP delivery does NOT use this module — see
app/services/smtp_service.py for the fully isolated superadmin path.

Architecture (v5.7.1 — corrected provider order):
  PRIMARY  → MailerSend   (existing mailersend_service, untouched)
  FALLBACK → SMTP         (smtplib — zero external dependency)

  NOTE: v5.7 shipped with this order reversed (SMTP primary / MailerSend
  fallback). That was inconsistent with the platform's email architecture
  decision (MailerSend is the managed, deliverability-monitored provider
  for tenant/admin; SMTP is the resilience fallback, not the default). This
  revision restores MailerSend-primary / SMTP-fallback. If your environment
  has SMTP_ENABLED=true and was relying on the old SMTP-first behavior,
  see the v5.7.1 migration note in the deployment changelog before deploying.

Delivery flow per send attempt:
  1. MailerSend configured? → attempt MailerSend
       Success → return True
       Failure → log, fall through to SMTP
  2. SMTP enabled & configured? → attempt SMTP
       Success → return True
       Failure → log, return False
  3. Neither configured → log critical, return False

All public functions maintain backward-compatible signatures with the
old email_service.py so zero call-site changes are required.

Security properties:
  • Credentials resolved server-side only (DB priority → env fallback)
  • SMTP password never logged or included in error messages
  • TLS enforced; STARTTLS supported via SMTP_USE_TLS env flag
  • Connection timeout hardened (default 10 s)
  • HTML + plain-text dual-body for all outbound emails

Observability:
  • Structured log lines: provider, portal, success/failure, latency
  • health_check() returns provider status dict for /health/email endpoint
  • validate_configuration() at startup; logs warnings on misconfiguration
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SMTP configuration helpers
# ─────────────────────────────────────────────────────────────────────────────

def _smtp_enabled() -> bool:
    """Return True when SMTP is switched on via environment variable."""
    return os.environ.get('SMTP_ENABLED', 'false').lower() in ('1', 'true', 'yes')


def _smtp_config() -> dict:
    """
    Resolve SMTP configuration from environment variables.

    Returns a dict with all required keys; empty strings where unset so
    callers can do a simple truthiness check on 'host' and 'username'.
    """
    return {
        'host':       os.environ.get('SMTP_HOST', '').strip(),
        'port':       int(os.environ.get('SMTP_PORT', '587')),
        'username':   os.environ.get('SMTP_USERNAME', '').strip(),
        'password':   os.environ.get('SMTP_PASSWORD', '').strip(),
        'from_email': os.environ.get('SMTP_FROM_EMAIL', '').strip(),
        'from_name':  os.environ.get('SMTP_FROM_NAME', 'Portfolio CMS').strip(),
        'use_tls':    os.environ.get('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes'),
        'timeout':    int(os.environ.get('SMTP_TIMEOUT', '10')),
    }


def _smtp_is_configured() -> bool:
    """Return True only when all mandatory SMTP fields are present."""
    cfg = _smtp_config()
    return bool(cfg['host'] and cfg['username'] and cfg['password'] and cfg['from_email'])


def _html_to_text(html: str) -> str:
    """Minimal HTML → plain-text strip (no external deps)."""
    text = re.sub(r'<[^>]+>', '', html)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Core SMTP send primitive
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_smtp(
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Send a single email via SMTP.

    Supports:
      • STARTTLS (port 587, recommended)
      • TLS/SSL  (port 465, enabled when SMTP_USE_TLS=true AND port=465)
      • Plaintext (SMTP_USE_TLS=false — NOT recommended for production)

    Args:
        to:       Recipient email address.
        subject:  Email subject line.
        text:     Plain-text body (required; generated from html if not supplied).
        html:     Optional HTML body.
        to_name:  Optional display name for the recipient.
        reply_to: Optional Reply-To header.

    Returns:
        (True, 'delivered') on success, (False, error_message) on failure.
        SMTP password is never included in error_message.
    """
    if not _smtp_enabled():
        return False, 'SMTP_ENABLED is not set to True'

    if not _smtp_is_configured():
        return False, 'SMTP credentials incomplete (check SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL)'

    cfg = _smtp_config()
    t0  = time.monotonic()

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject']  = subject
        msg['From']     = f"{cfg['from_name']} <{cfg['from_email']}>"
        msg['To']       = f"{to_name} <{to}>" if to_name else to
        if reply_to:
            msg['Reply-To'] = reply_to

        # Attach plain text first (lowest priority part per RFC 2045)
        plain = text or (html and _html_to_text(html)) or ''
        msg.attach(MIMEText(plain, 'plain', 'utf-8'))

        if html:
            msg.attach(MIMEText(html, 'html', 'utf-8'))

        # Choose connection strategy: SSL (465) vs STARTTLS (587/25)
        use_ssl      = cfg['use_tls'] and cfg['port'] == 465
        use_starttls = cfg['use_tls'] and cfg['port'] != 465

        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=cfg['timeout'], context=context) as server:
                server.login(cfg['username'], cfg['password'])
                server.sendmail(cfg['from_email'], [to], msg.as_string())
        else:
            with smtplib.SMTP(cfg['host'], cfg['port'], timeout=cfg['timeout']) as server:
                if use_starttls:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(cfg['username'], cfg['password'])
                server.sendmail(cfg['from_email'], [to], msg.as_string())

        elapsed = (time.monotonic() - t0) * 1000
        logger.info(
            'email.smtp: delivered to=%s subject="%s" latency=%.0fms',
            to, subject[:60], elapsed,
        )
        return True, 'delivered'

    except smtplib.SMTPAuthenticationError:
        logger.error('email.smtp: AUTH FAILED — check SMTP_USERNAME / SMTP_PASSWORD')
        return False, 'SMTP authentication failed'
    except smtplib.SMTPRecipientsRefused as exc:
        logger.error('email.smtp: recipient refused: %s → %s', to, exc)
        return False, f'Recipient refused: {to}'
    except smtplib.SMTPException as exc:
        logger.error('email.smtp: SMTPException for %s: %s', to, exc)
        return False, f'SMTP error: {type(exc).__name__}'
    except TimeoutError:
        logger.error('email.smtp: timeout connecting to %s:%s', cfg['host'], cfg['port'])
        return False, f'SMTP connection timed out ({cfg["timeout"]}s)'
    except OSError as exc:
        logger.error('email.smtp: network error: %s', exc)
        return False, f'SMTP network error: {type(exc).__name__}'
    except Exception as exc:  # noqa: BLE001
        logger.exception('email.smtp: unexpected error sending to %s', to)
        return False, f'Unexpected SMTP error: {type(exc).__name__}'


# ─────────────────────────────────────────────────────────────────────────────
# MailerSend fallback wrapper
# ─────────────────────────────────────────────────────────────────────────────

def _send_via_mailersend(
    to: str,
    subject: str,
    text: str,
    html: Optional[str] = None,
    to_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    portal: str = 'tenant',
) -> tuple[bool, str]:
    """
    Delegate to the existing mailersend_service.send_email().

    Preserves all per-portal key/sender resolution already implemented in v5.6.
    This function is the fallback tier — called only when SMTP fails or is disabled.
    """
    try:
        from app.services.mailersend_service import send_email as ms_send
        t0 = time.monotonic()
        ok, msg = ms_send(
            to=to,
            subject=subject,
            text=text,
            html=html,
            reply_to=reply_to,
            to_name=to_name,
            portal=portal,
        )
        elapsed = (time.monotonic() - t0) * 1000
        if ok:
            logger.info(
                'email.mailersend: delivered portal=%s to=%s latency=%.0fms',
                portal, to, elapsed,
            )
            return True, 'delivered'
        logger.error(
            'email.mailersend: FAILED portal=%s to=%s reason=%s',
            portal, to, msg,
        )
        return False, msg
    except Exception as exc:  # noqa: BLE001
        logger.exception('email.mailersend: unexpected error sending to %s', to)
        return False, f'MailerSend error: {type(exc).__name__}'


# ─────────────────────────────────────────────────────────────────────────────
# EmailService class — primary public interface
# ─────────────────────────────────────────────────────────────────────────────

class EmailService:
    """
    Stateless service class encapsulating the dual-provider email architecture.

    Usage:
        svc = EmailService()
        ok, err = svc.send_email(to='user@example.com', subject='Hi', text='Hello')

    All methods are safe to call without an active app context from the class
    itself; the underlying helpers resolve config at call time.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_email(
        self,
        to: str,
        subject: str,
        text: str,
        html: Optional[str] = None,
        to_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        portal: str = 'tenant',
    ) -> tuple[bool, str]:
        """
        Send an email via the active provider chain: SMTP → MailerSend.

        Args:
            to:       Recipient address.
            subject:  Email subject.
            text:     Plain-text body.
            html:     Optional HTML body.
            to_name:  Optional recipient display name.
            reply_to: Optional Reply-To address.
            portal:   'tenant' | 'admin' | 'superadmin' — governs MailerSend
                      key and sender identity selection when falling back.

        Returns:
            (True, 'delivered') on success, (False, error_message) on failure.
        """
        # ── Tier 1: MailerSend (primary) ────────────────────────────────
        ms_ok, ms_err = _send_via_mailersend(
            to, subject, text, html=html, to_name=to_name, reply_to=reply_to, portal=portal,
        )
        if ms_ok:
            return True, 'delivered'

        logger.warning(
            'email: MailerSend failed portal=%s (%s) — falling through to SMTP fallback',
            portal, ms_err,
        )

        # ── Tier 2: SMTP (fallback) ──────────────────────────────────────
        if _smtp_enabled() and _smtp_is_configured():
            sm_ok, sm_err = _send_via_smtp(to, subject, text, html=html, to_name=to_name, reply_to=reply_to)
            if sm_ok:
                logger.info(
                    'email: SMTP fallback succeeded portal=%s to=%s (MailerSend had failed: %s)',
                    portal, to, ms_err,
                )
                return True, 'delivered'
            final_err = sm_err
        else:
            final_err = f'{ms_err}; SMTP fallback unavailable (SMTP_ENABLED unset or incomplete config)'

        # ── Tier 3: Both providers exhausted ─────────────────────────────
        logger.critical(
            'email: ALL PROVIDERS FAILED — portal=%s to=%s subject="%s" last_error=%s',
            portal, to, subject[:60], final_err,
        )
        return False, final_err

    def _send_via_smtp(self, *args, **kwargs) -> tuple[bool, str]:
        """Expose internal SMTP primitive (used by health_check)."""
        return _send_via_smtp(*args, **kwargs)

    def _send_via_mailersend(self, *args, **kwargs) -> tuple[bool, str]:
        """Expose internal MailerSend primitive (used by health_check)."""
        return _send_via_mailersend(*args, **kwargs)

    def health_check(self) -> dict:
        """
        Return a status dict for each configured provider without sending real email.

        Used by the /health/email endpoint.
        """
        smtp_status: dict = {'enabled': _smtp_enabled(), 'configured': False, 'status': 'disabled'}
        ms_status:   dict = {'configured': False, 'status': 'unknown'}

        # SMTP probe — TCP connection test only (no auth, no mail)
        if _smtp_enabled():
            cfg = _smtp_config()
            if _smtp_is_configured():
                smtp_status['configured'] = True
                smtp_status['host'] = cfg['host']
                smtp_status['port'] = cfg['port']
                try:
                    use_ssl = cfg['use_tls'] and cfg['port'] == 465
                    if use_ssl:
                        ctx = ssl.create_default_context()
                        with smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=5, context=ctx):
                            pass
                    else:
                        with smtplib.SMTP(cfg['host'], cfg['port'], timeout=5) as s:
                            s.ehlo()
                    smtp_status['status'] = 'ok'
                except Exception as exc:
                    smtp_status['status'] = f'error: {type(exc).__name__}'
            else:
                smtp_status['status'] = 'incomplete_configuration'

        # MailerSend probe — key resolution only (no HTTP request)
        try:
            from app.services.mailersend_service import get_mailersend_key
            key = get_mailersend_key()
            if key:
                ms_status['configured'] = True
                ms_status['key_length']  = len(key)
                ms_status['status']      = 'configured'
            else:
                ms_status['status'] = 'not_configured'
        except Exception as exc:
            ms_status['status'] = f'error: {exc}'

        return {
            'smtp':        smtp_status,
            'mailersend':  ms_status,
            'primary':     'mailersend' if ms_status['configured'] else ('smtp' if (_smtp_enabled() and _smtp_is_configured()) else 'none'),
            'fallback':    'smtp' if (_smtp_enabled() and _smtp_is_configured()) else 'none',
        }

    def validate_configuration(self) -> list[str]:
        """
        Return a list of warning strings for misconfigured email settings.

        Called at Flask startup. Empty list = fully configured.
        """
        warnings: list[str] = []
        smtp_ok = _smtp_enabled() and _smtp_is_configured()
        ms_ok   = False

        try:
            from app.services.mailersend_service import get_mailersend_key
            ms_ok = bool(get_mailersend_key())
        except Exception:
            pass

        if not ms_ok:
            warnings.append('MailerSend not configured — set MAILERSEND_API_KEY or configure via Superadmin → Email Settings (this is the PRIMARY provider for tenant/admin)')

        if not smtp_ok:
            if _smtp_enabled():
                cfg = _smtp_config()
                missing = [k for k in ('host', 'username', 'password', 'from_email') if not cfg[k]]
                warnings.append(f'SMTP fallback enabled but missing: {", ".join(missing).upper()}')
            else:
                warnings.append('SMTP_ENABLED is not set — fallback delivery unavailable if MailerSend fails')

        if not smtp_ok and not ms_ok:
            warnings.append('CRITICAL: No email provider is configured — OTP/password-reset delivery will fail')

        return warnings


# Module-level singleton for import convenience
_service = EmailService()


# ─────────────────────────────────────────────────────────────────────────────
# Public backward-compatible API
# (Drop-in replacements for the old email_service.py re-exports)
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
    Module-level send_email() — backward-compatible with the old mailersend_service.send_email().

    Now routes through the full SMTP → MailerSend provider chain.
    """
    return _service.send_email(
        to=to, subject=subject, text=text, html=html,
        to_name=to_name, reply_to=reply_to, portal=portal,
    )


def send_otp_email(
    recipient_email: str,
    otp: str,
    user_type: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    ttl_minutes: int = 10,
) -> bool:
    """
    Send an OTP password-reset code.

    Backward-compatible with the signature in mailersend_service.send_otp_email().
    Routes: MailerSend (primary) → SMTP (fallback). Used by tenant + admin
    flows only — superadmin uses smtp_service.send_superadmin_otp() exclusively.

    Args:
        recipient_email: Destination address.
        otp:             Raw 6-digit OTP generated by otp_service.
        user_type:       'superadmin' | 'admin' | 'tenant'
        ip_address:      Request IP for security context in email body.
        user_agent:      Request User-Agent for security context.
        ttl_minutes:     OTP validity window shown in the email body.

    Returns:
        True if delivered by any provider, False if all providers failed.
    """
    portal_map = {'superadmin': 'superadmin', 'admin': 'admin', 'tenant': 'tenant'}
    portal     = portal_map.get(user_type, 'tenant')
    role_label = user_type.replace('_', ' ').title()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

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

    ok, err = _service.send_email(
        to=recipient_email, subject=subject, text=text, html=html, portal=portal
    )

    if ok:
        logger.info('otp_email: delivered portal=%s to=%s', portal, recipient_email)
        return True

    logger.error(
        'otp_email: ALL PROVIDERS FAILED portal=%s to=%s error=%s',
        portal, recipient_email, err,
    )
    return False


def send_verification_email(
    recipient_email: str,
    username: str,
    verification_url: str,
) -> bool:
    """Send email verification link. Delegates to mailersend_service for template parity."""
    try:
        from app.services.mailersend_service import send_verification_email as _ms_ve
        return _ms_ve(recipient_email, username, verification_url)
    except Exception as exc:
        logger.error('send_verification_email: mailersend error: %s — falling back to SMTP', exc)

    subject = '[Portfolio CMS] Verify Your Email Address'
    text = (
        f'Hello {username},\n\n'
        f'Please verify your email address by visiting:\n\n'
        f'{verification_url}\n\n'
        f'This link expires in 24 hours.\n\n'
        f'— Portfolio CMS'
    )
    ok, _ = _service.send_email(to=recipient_email, subject=subject, text=text)
    return ok


def send_subscription_email(
    recipient_email: str,
    tenant_name: str,
    event: str,
    plan_name: str = '',
    expires_at: str = '',
) -> bool:
    """Send subscription lifecycle emails. Falls through to mailersend_service."""
    try:
        from app.services.mailersend_service import send_subscription_email as _ms_sub
        return _ms_sub(recipient_email, tenant_name, event, plan_name=plan_name, expires_at=expires_at)
    except Exception as exc:
        logger.error('send_subscription_email: error: %s', exc)
        return False


def send_payment_notification(
    recipient_email: str,
    tenant_name: str,
    amount: str,
    status: str,
    reference: str = '',
) -> bool:
    """Send payment notification emails. Falls through to mailersend_service."""
    try:
        from app.services.mailersend_service import send_payment_notification as _ms_pay
        return _ms_pay(recipient_email, tenant_name, amount, status, reference=reference)
    except Exception as exc:
        logger.error('send_payment_notification: error: %s', exc)
        return False


def send_system_notification(
    recipient_email: str,
    subject: str,
    message: str,
    portal: str = 'superadmin',
) -> bool:
    """Send system-level notification. Falls through to mailersend_service."""
    try:
        from app.services.mailersend_service import send_system_notification as _ms_sys
        return _ms_sys(recipient_email, subject, message, portal=portal)
    except Exception as exc:
        logger.error('send_system_notification: mailersend error: %s — falling back to SMTP', exc)

    ok, _ = _service.send_email(to=recipient_email, subject=subject, text=message, portal=portal)
    return ok


def validate_mailersend_key(key: str) -> tuple[bool, str]:
    """Delegate to mailersend_service — signature unchanged."""
    from app.services.mailersend_service import validate_mailersend_key as _ms_vk
    return _ms_vk(key)


# ─────────────────────────────────────────────────────────────────────────────
# Flask startup integration
# ─────────────────────────────────────────────────────────────────────────────

def init_email_services(app) -> None:
    """
    Validate email configuration at Flask startup and log structured status.

    Call this from create_app() AFTER extensions are initialized:

        from app.services.email_service import init_email_services
        init_email_services(app)
    """
    with app.app_context():
        svc = EmailService()
        warnings = svc.validate_configuration()
        health   = svc.health_check()

        smtp_s = health['smtp']
        ms_s   = health['mailersend']

        app.logger.info('─── Email Service (v5.7.1) ─────────────────────────')
        app.logger.info(
            '  MailerSend: %s  key_len=%s',
            ms_s['status'].upper(),
            ms_s.get('key_length', 'N/A'),
        )
        app.logger.info(
            '  SMTP      : %s  host=%s port=%s',
            smtp_s['status'].upper(),
            smtp_s.get('host', 'N/A'),
            smtp_s.get('port', 'N/A'),
        )
        app.logger.info(
            '  Primary=%s  Fallback=%s',
            health['primary'], health['fallback'],
        )

        for w in warnings:
            app.logger.warning('  ⚠ %s', w)

        app.logger.info('────────────────────────────────────────────────────')


# ─────────────────────────────────────────────────────────────────────────────
# Deprecated shims (preserved from v5.0 for backward compat)
# ─────────────────────────────────────────────────────────────────────────────

def validate_resend_key(key: str) -> tuple[bool, str]:
    """Deprecated. Resend removed in v5.0."""
    logger.warning('validate_resend_key() called — Resend removed in v5.0.')
    return False, 'Resend is no longer used. Configure MAILERSEND_API_KEY instead.'


def validate_web3forms_key(key: str) -> tuple[bool, str]:
    """Deprecated. Web3Forms removed in v4.1."""
    logger.warning('validate_web3forms_key() called — Web3Forms is deprecated.')
    return False, 'Web3Forms is no longer used.'


def send_contact_form_web3forms(*args, **kwargs) -> bool:
    """Deprecated shim."""
    logger.warning('send_contact_form_web3forms() called — Web3Forms is deprecated.')
    return False