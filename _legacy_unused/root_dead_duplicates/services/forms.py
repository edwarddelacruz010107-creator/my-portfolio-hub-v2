"""
app/services/forms.py — Per-tenant contact form service (v4.2)

Architecture:
    Visitor → Tenant Contact Form → detect tenant → load TenantFormSettings
    → dispatch to provider → email delivered to tenant.receiver_email

Providers:
    basin     → HTTP POST to tenant's Basin endpoint (server-side, key never exposed)
    web3forms → HTTP POST to Web3Forms API with tenant's access_key
    disabled  → Store in Inquiry table (CMS inbox fallback)

Security:
    - API keys NEVER passed to frontend (resolved server-side from DB)
    - Rate limiting enforced upstream (Flask-Limiter on contact route)
    - CSRF enforced on all form endpoints
    - Tenant isolation: tenant_id from subdomain/path, NOT from user input

OWASP considerations:
    A01 — Tenant isolation: provider/key resolved from DB only, never client input
    A03 — Input validation: all fields truncated and sanitised before forwarding
    A07 — Logging without sensitive data (no API keys in logs)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from app.models.tenant_form_settings import (
    TenantFormSettings,
    BASIN_PREFIX,
    WEB3FORMS_URL,
)

logger = logging.getLogger(__name__)

_TIMEOUT       = 10     # seconds for outbound HTTP
_MAX_NAME      = 200
_MAX_EMAIL     = 200
_MAX_SUBJECT   = 500
_MAX_MSG       = 5_000
_EMAIL_RE      = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def send_contact_message(
    *,
    tenant_id: int,
    name:      str,
    email:     str,
    subject:   str,
    message:   str,
) -> tuple[bool, str]:
    """
    Main entry point. Detect tenant's provider and dispatch accordingly.

    Returns (success: bool, error_msg: str).
    error_msg is '' on success.

    Callers (contact form route) pass tenant_id resolved from subdomain —
    NEVER from POST body to prevent tenant spoofing.
    """
    # ── Sanitise inputs ───────────────────────────────────────────────────────
    name    = (name    or '').strip()[:_MAX_NAME]
    email   = (email   or '').strip()[:_MAX_EMAIL]
    subject = (subject or '').strip()[:_MAX_SUBJECT]
    message = (message or '').strip()[:_MAX_MSG]

    err = _validate_fields(name, email, message)
    if err:
        return False, err

    # ── Load settings (read-only, no mutation) ────────────────────────────────
    settings = TenantFormSettings.for_tenant(tenant_id)

    if not settings or settings.provider == 'disabled' or not settings.is_enabled:
        # Fallback: caller should store in Inquiry table
        logger.info('forms: tenant=%s provider=disabled → internal fallback', tenant_id)
        return False, 'INTERNAL_FALLBACK'

    if not settings.is_configured:
        logger.warning('forms: tenant=%s provider=%s not configured',
                       tenant_id, settings.provider)
        return False, f'Form provider ({settings.provider}) is not fully configured.'

    # ── Dispatch ──────────────────────────────────────────────────────────────
    if settings.provider == 'basin':
        return send_basin_message(
            endpoint=settings.form_endpoint,
            name=name, email=email, subject=subject, message=message,
        )

    if settings.provider == 'web3forms':
        return send_web3forms_message(
            access_key=settings.api_key,          # decrypted in-process, never logged
            receiver_email=settings.receiver_email or '',
            sender_name=settings.sender_name or name,
            name=name, email=email, subject=subject, message=message,
        )

    logger.error('forms: unknown provider %r for tenant=%s', settings.provider, tenant_id)
    return False, 'Unknown form provider.'


# ══════════════════════════════════════════════════════════════════════════════
# Provider implementations
# ══════════════════════════════════════════════════════════════════════════════

def send_basin_message(
    *,
    endpoint: str,
    name:     str,
    email:    str,
    subject:  str,
    message:  str,
    extra:    Optional[dict] = None,
) -> tuple[bool, str]:
    """
    POST to a Basin endpoint server-side.
    The endpoint URL comes from DB — never from client input.
    Basin accepts standard form fields; returns JSON {success: true}.
    """
    if not endpoint or not endpoint.startswith(BASIN_PREFIX):
        logger.error('forms/basin: invalid endpoint %r', endpoint)
        return False, 'Invalid Basin endpoint.'

    payload = {
        'name':    name,
        'email':   email,
        'subject': subject,
        'message': message,
    }
    if extra:
        for k, v in extra.items():
            if k not in payload:
                payload[k] = str(v)[:500]

    try:
        resp = requests.post(
            endpoint,
            data=payload,
            headers={
                'Accept':   'application/json',
                'X-Source': 'PortfolioCMS/4.2',
            },
            timeout=_TIMEOUT,
        )
        body = resp.json()
        if resp.status_code in (200, 201) and body.get('success'):
            logger.info('forms/basin: OK tenant endpoint=***%s', endpoint[-8:])
            return True, ''
        err = body.get('error') or body.get('message') or f'HTTP {resp.status_code}'
        logger.warning('forms/basin: rejected: %s', err)
        return False, err
    except requests.Timeout:
        logger.error('forms/basin: timeout after %ds', _TIMEOUT)
        return False, 'Basin service timed out.'
    except Exception as exc:
        logger.exception('forms/basin: unexpected error: %s', exc)
        return False, 'Submission error. Please try again.'


def send_web3forms_message(
    *,
    access_key:     str,
    receiver_email: str,
    sender_name:    str,
    name:           str,
    email:          str,
    subject:        str,
    message:        str,
) -> tuple[bool, str]:
    """
    POST to Web3Forms API.
    access_key resolved from encrypted DB field — NEVER from client.
    Web3Forms delivers email to receiver_email (configured per-tenant).
    """
    if not access_key:
        return False, 'Web3Forms API key not configured.'

    payload = {
        'access_key':    access_key,
        'subject':       subject or f'New message from {name}',
        'name':          name,
        'email':         email,
        'message':       message,
        'from_name':     sender_name,
        'replyto':       email,
    }
    if receiver_email:
        payload['to'] = receiver_email

    try:
        resp = requests.post(
            WEB3FORMS_URL,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=_TIMEOUT,
        )
        body = resp.json()
        if body.get('success'):
            logger.info('forms/web3forms: OK')
            return True, ''
        err = body.get('message', f'HTTP {resp.status_code}')
        logger.warning('forms/web3forms: rejected: %s', err)
        return False, err
    except requests.Timeout:
        logger.error('forms/web3forms: timeout after %ds', _TIMEOUT)
        return False, 'Web3Forms service timed out.'
    except Exception as exc:
        logger.exception('forms/web3forms: unexpected error: %s', exc)
        return False, 'Submission error. Please try again.'


# ══════════════════════════════════════════════════════════════════════════════
# Validation & Testing
# ══════════════════════════════════════════════════════════════════════════════

def validate_provider(settings: TenantFormSettings) -> tuple[bool, str]:
    """
    Validate provider config without making a live submission.
    Returns (valid: bool, message: str).
    """
    if settings.provider == 'disabled':
        return False, 'Provider is disabled.'

    if settings.provider == 'basin':
        ep = settings.form_endpoint or ''
        if not ep.startswith(BASIN_PREFIX):
            return False, f'Basin endpoint must start with {BASIN_PREFIX}'
        if len(ep.removeprefix(BASIN_PREFIX)) < 4:
            return False, 'Basin form ID is too short.'
        return True, 'Basin endpoint looks valid.'

    if settings.provider == 'web3forms':
        if not settings.api_key:
            return False, 'Web3Forms API key is not set.'
        if len(settings.api_key) < 16:
            return False, 'Web3Forms API key appears too short.'
        return True, 'Web3Forms key is set.'

    return False, f'Unknown provider: {settings.provider!r}'


def test_provider(settings: TenantFormSettings) -> tuple[bool, str]:
    """
    Send a live test message through the tenant's configured provider.
    Uses receiver_email as the destination.
    Billing note: Basin charges per submission; treat test as real.
    """
    valid, msg = validate_provider(settings)
    if not valid:
        return False, msg

    receiver = settings.receiver_email or 'test@portfoliocms.dev'

    return send_contact_message(
        tenant_id=settings.tenant_id,
        name='Portfolio CMS',
        email='noreply@portfoliocms.dev',
        subject='[Test] Contact Form Configuration',
        message=(
            'This is a test message from Portfolio CMS to verify your '
            f'contact form provider ({settings.provider}) is correctly configured. '
            f'Submissions will be delivered to: {receiver}'
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _validate_fields(name: str, email: str, message: str) -> str:
    """
    Basic server-side field validation.
    Returns '' if valid, error string otherwise.
    """
    if not name:
        return 'Name is required.'
    if not email or not _EMAIL_RE.match(email):
        return 'A valid email address is required.'
    if not message or len(message) < 10:
        return 'Message must be at least 10 characters.'
    return ''
