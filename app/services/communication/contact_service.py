"""
app/services/contact_service.py — Centralized Contact Form Dispatch (v5.8)

PURPOSE:
    Single authoritative entry point for ALL tenant contact form submissions.
    Replaces the fragmented inline logic scattered across:
        app/tenant/__init__.py  (contact route)
        app/main/__init__.py    (legacy /contact route)
        app/services/forms.py   (provider dispatch — now wrapped here)

DELIVERY PIPELINE:
    Visitor → contact route → resolve_tenant() → this module
        ├─ Step 1: validate inputs
        ├─ Step 2: persist Inquiry (ALWAYS — zero message loss guarantee)
        ├─ Step 3: resolve provider from TenantFormSettings
        ├─ Step 4: dispatch to provider (basin | web3forms | email_only | disabled)
        ├─ Step 5: on external failure → internal inbox fallback + admin notify
        └─ Step 6: update delivery metadata

PROVIDER ROUTING (in priority order):
    1. TenantFormSettings.provider == 'basin'      → Basin endpoint (server-side POST)
    2. TenantFormSettings.provider == 'web3forms'  → Web3Forms API
    3. TenantFormSettings.provider == 'email_only' → MailerSend to receiver_email
    4. TenantFormSettings.provider == 'disabled'   → CMS inbox only
    5. No TenantFormSettings / unconfigured        → CMS inbox + admin notify

DEFAULT TENANT BEHAVIOUR (Obj #1):
    When tenant_slug == 'default', receiver_email is resolved from:
        1. TenantFormSettings.receiver_email (if set)
        2. Administrator user's email (first admin/superadmin for default tenant)
        3. ADMIN_EMAIL env var
    This guarantees the default portfolio contact form ALWAYS reaches the admin.

SUPERADMIN VISIBILITY:
    All contact form submissions are ALWAYS persisted as Inquiry records:
        - Landing page submissions: stored with tenant_slug='default', sender='visitor'
        - Tenant portfolio submissions: stored with their tenant_slug, sender='visitor'
    These appear in the superadmin Messages inbox (/superadmin/messages) in the 
    'From Tenants' tab or 'All' tab, enabling the superadmin to view and respond
    to all platform contact submissions.

SECURITY (OWASP):
    A01 — Tenant isolation: provider/key from DB, never client input
    A03 — Input sanitation: all fields stripped, truncated, HTML-escaped
    A07 — Structured logs: no API keys, passwords, or full email bodies in logs

ENTERPRISE OBSERVABILITY:
    Every step emits a structured log line:
        contact[<provider>]: tenant=<slug> inquiry_id=<id> status=<ok|fail> ...
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

from app.services.email_dispatcher import (
    build_landing_admin_email,
    build_landing_auto_reply_email,
    send_email as dispatch_email,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MAX_NAME    = 200
_MAX_EMAIL   = 200
_MAX_SUBJECT = 500
_MAX_MSG     = 5_000
_EMAIL_RE    = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ─────────────────────────────────────────────────────────────────────────────
# Delivery result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ContactResult:
    """Structured result returned by process_contact_submission()."""
    success: bool
    inquiry_id: Optional[int] = None
    provider_used: str = 'internal'
    delivery_status: str = 'pending'          # delivered | failed | skipped | fallback
    delivery_error: str = ''
    user_message: str = "Your message has been sent. I'll get back to you soon!"
    fallback_activated: bool = False
    log_context: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def process_contact_submission(
    *,
    tenant_slug: str,
    name: str,
    email: str,
    subject: str,
    message: str,
    phone: str = '',
    company: str = '',
    source: str = 'contact_form',
    ip_address: str = '',
    user_agent: str = '',
    submission_id: Optional[str] = None,
) -> ContactResult:
    """
    Full contact form submission pipeline.

    Guaranteed side-effects:
      • Inquiry is ALWAYS persisted to DB before any external call.
      • Delivery metadata (provider_used, delivery_status, delivery_error)
        is written back to the Inquiry row after dispatch.
      • On any external provider failure the internal inbox acts as fallback.

    Returns:
        ContactResult with success=True even on external delivery failure
        (because the message IS saved internally and the visitor should not
        retry and create duplicates).
    """
    # ── 1. Sanitize inputs ────────────────────────────────────────────────────
    name       = _sanitize(name,    _MAX_NAME)
    email      = _sanitize(email,   _MAX_EMAIL)
    subject    = _sanitize(subject, _MAX_SUBJECT)
    message    = _sanitize(message, _MAX_MSG)
    ip_address = (ip_address or '').strip()[:45]
    user_agent = (user_agent or '').strip()[:300]

    validation_error = _validate(name, email, message)
    if validation_error:
        logger.info(
            'contact[validate]: tenant=%s REJECT reason=%r',
            tenant_slug, validation_error,
        )
        return ContactResult(
            success=False,
            delivery_status='rejected',
            delivery_error=validation_error,
            user_message=validation_error,
        )

    # ── 2. Load tenant and provider config ────────────────────────────────────
    from app.models.portfolio import Tenant, Inquiry
    from app.models.tenant_form_settings import TenantFormSettings

    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    tenant_id = tenant.id if tenant else None

    form_settings: Optional[TenantFormSettings] = None
    if tenant_id:
        form_settings = TenantFormSettings.for_tenant(tenant_id)

    logger.info(
        'contact[init]: tenant=%s tenant_id=%s provider=%s enabled=%s configured=%s',
        tenant_slug, tenant_id,
        form_settings.provider if form_settings else 'none',
        form_settings.is_enabled if form_settings else False,
        form_settings.is_configured if form_settings else False,
    )

    # ── 3. Persist Inquiry (MUST precede any external call) ──────────────────
    from app import db

    # Idempotency check
    if submission_id:
        existing = Inquiry.query.filter_by(
            tenant_slug=tenant_slug,
            submission_id=submission_id,
        ).first()
        if existing:
            logger.info(
                'contact[idempotent]: tenant=%s submission_id=%s already recorded',
                tenant_slug, submission_id,
            )
            return ContactResult(
                success=True,
                inquiry_id=existing.id,
                provider_used=getattr(existing, 'provider_used', 'internal'),
                delivery_status='duplicate_skipped',
                user_message="Your message has been sent. I'll get back to you soon!",
            )

    inquiry = Inquiry(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        name=name,
        email=email,
        subject=subject,
        message=message,
        phone=phone or None,
        company=company or None,
        ip_address=ip_address,
        user_agent=user_agent,
        submission_id=submission_id or None,
        sender='visitor',
        is_read=False,
    )
    # Set delivery fields defensively (columns added in migration 0027)
    if hasattr(inquiry, 'provider_used'):
        inquiry.provider_used = 'pending'
    if hasattr(inquiry, 'delivery_status'):
        inquiry.delivery_status = 'pending'

    db.session.add(inquiry)
    try:
        db.session.commit()
        logger.info(
            'contact[persist]: tenant=%s inquiry_id=%s OK',
            tenant_slug, inquiry.id,
        )
    except Exception as exc:
        db.session.rollback()
        logger.error(
            'contact[persist]: tenant=%s DB commit FAILED: %s',
            tenant_slug, exc, exc_info=True,
        )
        return ContactResult(
            success=False,
            delivery_status='db_error',
            delivery_error='Unable to save your message. Please try again.',
            user_message='Unable to save your message. Please try again.',
        )

    # ── 4. Determine provider and dispatch ────────────────────────────────────
    result = _dispatch(
        tenant_slug=tenant_slug,
        tenant_id=tenant_id,
        tenant=tenant,
        form_settings=form_settings,
        inquiry=inquiry,
        name=name,
        email=email,
        subject=subject,
        message=message,
        phone=phone,
        company=company,
        source=source,
    )

    # ── 5. Write delivery metadata back to Inquiry ────────────────────────────
    try:
        if hasattr(inquiry, 'provider_used'):
            inquiry.provider_used = result.provider_used
        if hasattr(inquiry, 'delivery_status'):
            inquiry.delivery_status = result.delivery_status
        if hasattr(inquiry, 'delivery_error') and result.delivery_error:
            inquiry.delivery_error = result.delivery_error[:500]
        db.session.commit()
    except Exception as exc:
        logger.warning(
            'contact[metadata]: tenant=%s inquiry_id=%s metadata update failed: %s',
            tenant_slug, inquiry.id, exc,
        )
        db.session.rollback()

    result.inquiry_id = inquiry.id
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Provider dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch(
    *,
    tenant_slug: str,
    tenant_id: Optional[int],
    tenant,
    form_settings,
    inquiry,
    name: str,
    email: str,
    subject: str,
    message: str,
    phone: str,
    company: str,
    source: str,
) -> ContactResult:
    """
    Select and execute the correct delivery provider.
    Returns ContactResult with delivery outcome.
    """
    # Determine active provider
    # Self-healing: if form_settings is still disabled but the tenant has active
    # email providers configured via Email Services, auto-upgrade to email_only.
    # This covers tenants who configured SMTP/Resend/MailerSend in Email Services
    # but never explicitly set the contact form provider in Settings.
    if not form_settings or not form_settings.is_enabled or form_settings.provider in ('disabled', ''):
        if tenant and tenant.id:
            try:
                from app.models.core import TenantEmailProvider
                has_active = TenantEmailProvider.get_ordered_active(tenant.id)
                if has_active:
                    # Tenant has live providers — upgrade silently to email_only
                    if form_settings:
                        if not form_settings.receiver_email:
                            from app.models.core import User
                            au = (User.query
                                  .filter_by(tenant_id=tenant.id, is_admin=True)
                                  .order_by(User.id.asc())
                                  .first())
                            if au and au.email:
                                form_settings.receiver_email = au.email
                        form_settings.provider   = 'email_only'
                        form_settings.is_enabled = True
                        try:
                            from app import db as _db
                            _db.session.commit()
                        except Exception:
                            pass
                        provider = 'email_only'
                        logger.info(
                            'contact[dispatch]: tenant=%s — auto-upgraded disabled form_settings '
                            'to email_only (active providers exist)', tenant_slug
                        )
                    else:
                        provider = 'email_only'
                else:
                    provider = 'disabled'
            except Exception as _he:
                logger.warning('contact[dispatch]: self-heal check failed (non-fatal): %s', _he)
                provider = 'disabled' if (not form_settings or not form_settings.is_enabled) else (form_settings.provider or 'disabled')
        else:
            provider = 'disabled'
    else:
        provider = form_settings.provider or 'disabled'

    logger.info(
        'contact[dispatch]: tenant=%s inquiry_id=%s → provider=%s',
        tenant_slug, inquiry.id, provider,
    )

    # ── Public landing page → SuperAdmin inbox + SuperAdmin Email & Forms ────
    # Landing page submissions are platform leads, not tenant-owned portfolio
    # messages. They are already persisted as Inquiry rows above so they appear
    # in the SuperAdmin Messages inbox; this branch guarantees an email is also
    # sent through the SuperAdmin provider chain when a receiver is configured.
    if source == 'landing_page':
        return _dispatch_landing_platform(
            tenant_slug=tenant_slug,
            tenant=tenant,
            inquiry=inquiry,
            name=name,
            email=email,
            subject=subject,
            message=message,
            phone=phone,
            company=company,
            source=source,
        )

    # ── Basin ──────────────────────────────────────────────────────────────────
    if provider == 'basin':
        return _dispatch_basin(
            tenant_slug=tenant_slug,
            form_settings=form_settings,
            inquiry=inquiry,
            name=name, email=email, subject=subject, message=message,
        )

    # ── Web3Forms ─────────────────────────────────────────────────────────────
    if provider == 'web3forms':
        return _dispatch_web3forms(
            tenant_slug=tenant_slug,
            form_settings=form_settings,
            inquiry=inquiry,
            name=name, email=email, subject=subject, message=message,
        )

    # ── Email Only (MailerSend) ───────────────────────────────────────────────
    if provider == 'email_only':
        return _dispatch_email_only(
            tenant_slug=tenant_slug,
            tenant=tenant,
            form_settings=form_settings,
            inquiry=inquiry,
            name=name,
            email=email,
            subject=subject,
            message=message,
            phone=phone,
            company=company,
            source=source,
        )

    # ── Disabled / Internal inbox only ────────────────────────────────────────
    if provider in ('disabled', 'internal'):
        logger.info(
            'contact[internal]: tenant=%s inquiry_id=%s → CMS inbox only (provider=%s)',
            tenant_slug, inquiry.id, provider,
        )
        return ContactResult(
            success=True,
            provider_used='internal',
            delivery_status='delivered',
            user_message="Your message has been sent. I'll get back to you soon!",
        )

    # ── Unknown provider — treat as internal ─────────────────────────────────
    logger.error(
        'contact[dispatch]: tenant=%s inquiry_id=%s unknown provider=%r → fallback to internal',
        tenant_slug, inquiry.id, provider,
    )
    return ContactResult(
        success=True,
        provider_used='internal',
        delivery_status='fallback',
        delivery_error=f'Unknown provider {provider!r} — saved to inbox',
        fallback_activated=True,
    )


def _dispatch_basin(*, tenant_slug, form_settings, inquiry, name, email, subject, message) -> ContactResult:
    endpoint = (form_settings.form_endpoint or '').strip()

    if not endpoint:
        logger.error(
            'contact[basin]: tenant=%s inquiry_id=%s MISSING endpoint → inbox fallback',
            tenant_slug, inquiry.id,
        )
        return _inbox_fallback(tenant_slug, inquiry.id, 'basin', 'Basin endpoint not configured')

    logger.info(
        'contact[basin]: tenant=%s inquiry_id=%s → endpoint=***%s',
        tenant_slug, inquiry.id, endpoint[-8:],
    )

    from app.services.basin_service import submit_to_basin
    ok, err = submit_to_basin(
        basin_endpoint=endpoint,
        name=name,
        email=email,
        subject=subject or f'Contact from {name}',
        message=message,
    )

    if ok:
        logger.info(
            'contact[basin]: tenant=%s inquiry_id=%s DELIVERED',
            tenant_slug, inquiry.id,
        )
        return ContactResult(
            success=True,
            provider_used='basin',
            delivery_status='delivered',
        )

    logger.warning(
        'contact[basin]: tenant=%s inquiry_id=%s FAILED err=%r → inbox fallback',
        tenant_slug, inquiry.id, err,
    )
    return _inbox_fallback(tenant_slug, inquiry.id, 'basin', err)


def _dispatch_web3forms(*, tenant_slug, form_settings, inquiry, name, email, subject, message) -> ContactResult:
    api_key = form_settings.api_key or ''  # decrypted in-process

    if not api_key:
        logger.error(
            'contact[web3forms]: tenant=%s inquiry_id=%s MISSING api_key → inbox fallback',
            tenant_slug, inquiry.id,
        )
        return _inbox_fallback(tenant_slug, inquiry.id, 'web3forms', 'Web3Forms API key not configured')

    receiver = (form_settings.receiver_email or '').strip()

    logger.info(
        'contact[web3forms]: tenant=%s inquiry_id=%s receiver=%s',
        tenant_slug, inquiry.id, _mask_email(receiver),
    )

    from app.services.forms import send_web3forms_message
    ok, err = send_web3forms_message(
        access_key=api_key,
        receiver_email=receiver,
        sender_name=form_settings.sender_name or name,
        name=name,
        email=email,
        subject=subject,
        message=message,
    )

    if ok:
        logger.info(
            'contact[web3forms]: tenant=%s inquiry_id=%s DELIVERED',
            tenant_slug, inquiry.id,
        )
        return ContactResult(
            success=True,
            provider_used='web3forms',
            delivery_status='delivered',
        )

    logger.warning(
        'contact[web3forms]: tenant=%s inquiry_id=%s FAILED err=%r → inbox fallback',
        tenant_slug, inquiry.id, err,
    )
    return _inbox_fallback(tenant_slug, inquiry.id, 'web3forms', err)



def _dispatch_landing_platform(
    *,
    tenant_slug,
    tenant,
    inquiry,
    name,
    email,
    subject,
    message,
    phone,
    company,
    source,
) -> ContactResult:
    """Notify platform/SuperAdmin recipients for public landing inquiries."""
    recipients = _resolve_landing_recipients(tenant_slug, tenant)
    if not recipients:
        logger.error(
            'contact[landing]: tenant=%s inquiry_id=%s NO platform receiver configured → inbox only',
            tenant_slug,
            inquiry.id,
        )
        return ContactResult(
            success=True,
            provider_used='superadmin_chain',
            delivery_status='fallback',
            delivery_error='No landing receiver email configured — saved to SuperAdmin inbox',
            fallback_activated=True,
            user_message="Thanks — your message has been received.",
        )

    admin_subject, admin_text, admin_html = build_landing_admin_email(
        name=name,
        email_address=email,
        subject=subject,
        message=message,
        company=company,
        phone=phone,
        source=source,
    )

    try:
        from app.services.email.superadmin_email_service import send_email as send_superadmin_email
    except Exception as exc:  # pragma: no cover
        logger.exception('contact[landing]: could not import SuperAdmin email service: %s', exc)
        return _inbox_fallback(tenant_slug, inquiry.id, 'superadmin_chain', 'SuperAdmin email service unavailable')

    delivered = []
    failures = []
    for recipient in recipients:
        ok, err = send_superadmin_email(
            to=recipient,
            subject=admin_subject,
            html=admin_html,
            text=admin_text,
        )
        if ok:
            delivered.append(recipient)
        else:
            failures.append((recipient, err or 'send failed'))
            logger.warning(
                'contact[landing]: inquiry_id=%s notify failed recipient=%s err=%s',
                inquiry.id,
                _mask_email(recipient),
                str(err or 'send failed')[:160],
            )

    if delivered:
        inquiry.admin_notified = True
        logger.info(
            'contact[landing]: inquiry_id=%s delivered to %s platform recipient(s)',
            inquiry.id,
            len(delivered),
        )

        # Visitor confirmation. Failure should not make the lead submission fail.
        try:
            reply_subject, reply_text, reply_html = build_landing_auto_reply_email(
                name=name,
                subject=subject,
            )
            reply_ok, reply_err = send_superadmin_email(
                to=email,
                subject=reply_subject,
                html=reply_html,
                text=reply_text,
            )
            inquiry.auto_reply_sent = bool(reply_ok)
            if not reply_ok:
                logger.warning(
                    'contact[landing]: inquiry_id=%s auto-reply failed %s',
                    inquiry.id,
                    str(reply_err or 'send failed')[:160],
                )
        except Exception:  # noqa: BLE001
            logger.exception('contact[landing]: inquiry_id=%s auto-reply unexpected error', inquiry.id)
            inquiry.auto_reply_sent = False

        status = 'delivered' if not failures else 'partial'
        return ContactResult(
            success=True,
            provider_used='superadmin_chain',
            delivery_status=status,
            delivery_error='; '.join(f'{_mask_email(r)}: {e}' for r, e in failures)[:500] if failures else '',
            user_message='Thanks — your message has been received. We will reply within one business day.',
        )

    # No email delivered, but the Inquiry row is in SuperAdmin inbox.
    return ContactResult(
        success=True,
        provider_used='superadmin_chain',
        delivery_status='fallback',
        delivery_error='All landing email notifications failed — saved to SuperAdmin inbox',
        fallback_activated=True,
        user_message='Thanks — your message has been received. We will reply within one business day.',
    )


def _dispatch_email_only(
    *,
    tenant_slug,
    tenant,
    form_settings,
    inquiry,
    name,
    email,
    subject,
    message,
    phone,
    company,
    source,
) -> ContactResult:
    # Resolve recipient — critical for default tenant (Obj #1)
    recipient = _resolve_receiver_email(tenant_slug, tenant, form_settings)

    if not recipient:
        logger.error(
            'contact[email_only]: tenant=%s inquiry_id=%s NO receiver_email configured → inbox fallback',
            tenant_slug, inquiry.id,
        )
        return _inbox_fallback(
            tenant_slug, inquiry.id, 'email_only',
            'No receiver email configured — message saved to inbox',
        )

    logger.info(
        'contact[email_only]: tenant=%s inquiry_id=%s → recipient=%s',
        tenant_slug, inquiry.id, _mask_email(recipient),
    )

    admin_subject, admin_text, admin_html = build_landing_admin_email(
        name=name,
        email_address=email,
        subject=subject,
        message=message,
        company=company,
        phone=phone,
        source=source,
    )

    tenant_id = tenant.id if tenant else None
    ok, err = dispatch_email(
        category='landing_admin',
        provider='auto',
        tenant_id=tenant_id,
        to=recipient,
        subject=admin_subject,
        html_content=admin_html,
        text_content=admin_text,
        reply_to=email,
        to_name=tenant.slug if tenant and tenant.slug else None,
    )

    if ok:
        inquiry.admin_notified = True
        logger.info(
            'contact[email_only]: tenant=%s inquiry_id=%s admin notification delivered',
            tenant_slug, inquiry.id,
        )
    else:
        logger.warning(
            'contact[email_only]: tenant=%s inquiry_id=%s admin notify FAILED %s',
            tenant_slug, inquiry.id, err,
        )

    # v4.0 FIX: Commit admin notification state immediately to ensure atomicity
    # If the auto-reply fails, we at least have the admin notification recorded
    try:
        from app import db
        db.session.commit()
    except Exception as e:
        logger.error('contact[email_only]: failed to commit admin notification state: %s', e)
        db.session.rollback()

    # Attempt visitor auto-reply regardless of admin notification outcome.
    try:
        reply_subject, reply_text, reply_html = build_landing_auto_reply_email(
            name=name,
            subject=subject,
        )
        reply_ok, reply_err = dispatch_email(
            category='landing_auto_reply',
            provider='auto',
            tenant_id=tenant_id,
            to=email,
            subject=reply_subject,
            html_content=reply_html,
            text_content=reply_text,
            reply_to=recipient,
            to_name=name,
        )
        inquiry.auto_reply_sent = bool(reply_ok)
        if reply_ok:
            logger.info(
                'contact[email_only]: tenant=%s inquiry_id=%s auto-reply delivered to visitor',
                tenant_slug, inquiry.id,
            )
        else:
            logger.warning(
                'contact[email_only]: tenant=%s inquiry_id=%s auto-reply FAILED %s',
                tenant_slug, inquiry.id, reply_err,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            'contact[email_only]: tenant=%s inquiry_id=%s auto-reply unexpected error',
            tenant_slug, inquiry.id,
        )
        inquiry.auto_reply_sent = False

    # v4.0 FIX: Commit auto-reply state immediately for atomicity
    try:
        from app import db
        db.session.commit()
    except Exception as e:
        logger.error('contact[email_only]: failed to commit auto-reply state: %s', e)
        db.session.rollback()

    if ok:
        logger.info(
            'contact[email_only]: tenant=%s inquiry_id=%s DELIVERED to %s',
            tenant_slug, inquiry.id, _mask_email(recipient),
        )
        return ContactResult(
            success=True,
            provider_used='email_only',
            delivery_status='delivered',
        )

    logger.warning(
        'contact[email_only]: tenant=%s inquiry_id=%s FAILED (%s) → inbox fallback',
        tenant_slug, inquiry.id, err,
    )
    return _inbox_fallback(
        tenant_slug, inquiry.id, 'email_only',
        err or 'Email delivery failed — message saved to inbox',
    )



def _valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or '').strip()))


def _append_recipient(recipients: list[str], value: str | None) -> None:
    email = (value or '').strip().lower()
    if email and _valid_email(email) and email not in recipients:
        recipients.append(email)


def _resolve_landing_recipients(tenant_slug: str, tenant) -> list[str]:
    """Resolve recipients for public landing page contact submissions."""
    import os

    recipients: list[str] = []

    try:
        from app.models.core import PlatformSetting, User
        _append_recipient(recipients, PlatformSetting.get_string('landing_contact_receiver_email', default=''))

        if tenant is not None:
            _append_recipient(recipients, getattr(tenant, 'contact_email', '') or '')
            tenant_admins = (
                User.query
                .filter_by(tenant_id=getattr(tenant, 'id', None), is_admin=True)
                .order_by(User.id.asc())
                .limit(3)
                .all()
            )
            for admin in tenant_admins:
                _append_recipient(recipients, getattr(admin, 'email', '') or '')

        # Platform-level fallbacks.
        for key in ('ADMIN_EMAIL', 'SUPERADMIN_EMAIL', 'SUPPORT_EMAIL'):
            _append_recipient(recipients, os.environ.get(key, ''))

        superadmins = (
            User.query
            .filter_by(is_superadmin=True)
            .order_by(User.id.asc())
            .limit(5)
            .all()
        )
        for user in superadmins:
            _append_recipient(recipients, getattr(user, 'email', '') or '')
    except Exception as exc:
        logger.warning('contact[landing]: recipient resolution failed for tenant=%s: %s', tenant_slug, exc)
        for key in ('ADMIN_EMAIL', 'SUPERADMIN_EMAIL', 'SUPPORT_EMAIL'):
            _append_recipient(recipients, os.environ.get(key, ''))

    logger.info(
        'contact[landing]: resolved %d platform recipient(s) for tenant=%s',
        len(recipients),
        tenant_slug,
    )
    return recipients


# ─────────────────────────────────────────────────────────────────────────────
# Default tenant receiver_email resolution (Obj #1 + #2)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_receiver_email(tenant_slug: str, tenant, form_settings) -> str:
    """
    Resolve the email address that should receive contact form submissions.

    Resolution order:
      1. TenantFormSettings.receiver_email  (explicit per-tenant config)
      2. Tenant.contact_email               (legacy field)
      3. Admin user email for this tenant   (first admin user — critical for default)
      4. ADMIN_EMAIL env var                (system-level fallback)

    For the 'default' tenant (#3) guarantees submissions reach the administrator
    even when TenantFormSettings has no receiver_email set.
    """
    import os

    # 1. Explicit TenantFormSettings receiver_email
    if form_settings:
        r = (form_settings.receiver_email or '').strip()
        if r:
            logger.debug('_resolve_receiver_email: tenant=%s → TenantFormSettings.receiver_email', tenant_slug)
            return r

    # 2. Tenant.contact_email
    if tenant:
        r = (tenant.contact_email or '').strip()
        if r:
            logger.debug('_resolve_receiver_email: tenant=%s → Tenant.contact_email', tenant_slug)
            return r

    # 3. First admin User for this tenant (especially important for 'default')
    if tenant:
        try:
            from app.models import User
            admin_user = (
                User.query
                .filter_by(tenant_id=tenant.id, is_admin=True)
                .order_by(User.id.asc())
                .first()
            )
            if admin_user and admin_user.email:
                admin_email = str(admin_user.email).strip()
                logger.info(
                    '_resolve_receiver_email: tenant=%s → admin user email %s',
                    tenant_slug, _mask_email(admin_email),
                )
                return admin_email
        except Exception as exc:
            logger.warning(
                '_resolve_receiver_email: tenant=%s admin lookup failed: %s',
                tenant_slug, exc,
            )

    # 4. ADMIN_EMAIL env var
    r = os.environ.get('ADMIN_EMAIL', '').strip()
    if r:
        logger.info(
            '_resolve_receiver_email: tenant=%s → ADMIN_EMAIL env var',
            tenant_slug,
        )
        return r

    logger.error(
        '_resolve_receiver_email: tenant=%s NO receiver email found in any source',
        tenant_slug,
    )
    return ''


def bootstrap_default_tenant_form_settings(db) -> None:
    """
    Called from _ensure_default_tenant() to auto-configure TenantFormSettings
    for the 'default' tenant (Obj #2).

    Sets:
      - provider = 'email_only'
      - is_enabled = True
      - receiver_email = first admin user's email (or ADMIN_EMAIL env)
      - sender_email = MAILERSEND_FROM_EMAIL env

    Safe to call multiple times (idempotent — only updates if receiver_email is empty).
    """
    import os
    from app.models.portfolio import Tenant
    from app.models.tenant_form_settings import TenantFormSettings

    try:
        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            return

        settings = TenantFormSettings.get_or_create(tenant.id)

        # Only bootstrap if receiver_email is not already set
        if not settings.receiver_email:
            receiver = _resolve_receiver_email('default', tenant, settings)
            if receiver:
                settings.receiver_email = receiver
                logger.info(
                    'bootstrap_default_tenant_form_settings: set receiver_email=%s',
                    _mask_email(receiver),
                )

        # Ensure provider is active (default to email_only if still disabled)
        if settings.provider == 'disabled' or not settings.is_enabled:
            settings.provider = 'email_only'
            settings.is_enabled = True
            logger.info(
                'bootstrap_default_tenant_form_settings: activated email_only provider',
            )

        # Ensure sender_name is set
        if not settings.sender_name:
            settings.sender_name = os.environ.get('MAILERSEND_FROM_NAME', 'Portfolio CMS')

        db.session.commit()
        logger.info(
            'bootstrap_default_tenant_form_settings: complete — '
            'provider=%s enabled=%s receiver=%s',
            settings.provider, settings.is_enabled,
            _mask_email(settings.receiver_email or ''),
        )
    except Exception as exc:
        db.session.rollback()
        logger.warning(
            'bootstrap_default_tenant_form_settings: failed (non-fatal): %s', exc,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fallback helpers
# ─────────────────────────────────────────────────────────────────────────────

def _inbox_fallback(
    tenant_slug: str,
    inquiry_id: int,
    provider_attempted: str,
    error: str,
) -> ContactResult:
    """
    Called when an external provider fails.
    The Inquiry is already persisted. We just record the fallback.
    Optionally notify admin via system notification.
    """
    logger.warning(
        'contact[fallback]: tenant=%s inquiry_id=%s provider=%s stored_to_inbox err=%r',
        tenant_slug, inquiry_id, provider_attempted, error,
    )

    # Non-blocking admin notify — failure here must NOT surface to visitor
    try:
        _notify_admin_of_fallback(tenant_slug, inquiry_id, provider_attempted, error)
    except Exception as exc:
        logger.debug(
            'contact[fallback_notify]: admin notify failed (non-critical): %s', exc,
        )

    return ContactResult(
        success=True,   # Message IS saved; visitor should not retry
        provider_used=provider_attempted,
        delivery_status='fallback',
        delivery_error=error,
        fallback_activated=True,
        user_message="Your message has been sent. I'll get back to you soon!",
    )


def _notify_admin_of_fallback(
    tenant_slug: str,
    inquiry_id: int,
    provider: str,
    error: str,
) -> None:
    """
    Send a system notification to the admin when external delivery fails.
    Non-critical — logged on failure but does not affect the user response.
    """
    import os
    from flask import current_app

    dest = os.environ.get('ADMIN_EMAIL', '').strip()
    if not dest:
        dest = current_app.config.get('ADMIN_EMAIL', '').strip()
    if not dest:
        return

    try:
        from app.services.mailersend_service import send_system_notification
        send_system_notification(
            dest,
            f'[Portfolio CMS] Contact delivery fallback — tenant={tenant_slug}',
            (
                f'A contact form submission on tenant "{tenant_slug}" could not be '
                f'delivered via {provider}.\n\n'
                f'Inquiry ID: {inquiry_id}\n'
                f'Error: {error}\n\n'
                f'The message has been saved to the CMS inbox. '
                f'Please check the admin dashboard.'
            ),
        )
    except Exception as exc:
        logger.debug('_notify_admin_of_fallback: send failed: %s', exc)


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize(value: str, max_len: int) -> str:
    """Strip whitespace, truncate, and HTML-escape for safe downstream use."""
    cleaned = (value or '').strip()[:max_len]
    return html.escape(cleaned, quote=False)


def _validate(name: str, email: str, message: str) -> str:
    """Return '' if valid, error string otherwise."""
    if not name:
        return 'Name is required.'
    if not email or not _EMAIL_RE.match(email):
        return 'A valid email address is required.'
    if not message or len(message) < 10:
        return 'Message must be at least 10 characters.'
    return ''


def _mask_email(email: str) -> str:
    """Mask email for logging: user@domain → us**@domain."""
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    return f'{local[:2]}**@{domain}'


def _build_html_body(name: str, email: str, subject: str, message: str) -> str:
    """Reusable HTML email template for contact submissions."""
    safe_name    = html.escape(name)
    safe_email   = html.escape(email)
    safe_subject = html.escape(subject or '(no subject)')
    safe_message = html.escape(message).replace('\n', '<br>')

    return (
        '<div style="font-family:sans-serif;max-width:600px;margin:auto;'
        'padding:1.5rem;border:1px solid #e5e7eb;border-radius:8px;">'
        '<h3 style="margin-top:0;color:#1f2937;">New Contact Form Message</h3>'
        f'<p><strong>From:</strong> {safe_name} &lt;{safe_email}&gt;</p>'
        f'<p><strong>Subject:</strong> {safe_subject}</p>'
        '<hr style="border:none;border-top:1px solid #e5e7eb;">'
        f'<p style="white-space:pre-wrap;color:#374151;">{safe_message}</p>'
        '<hr style="border:none;border-top:1px solid #e5e7eb;">'
        '<p style="font-size:.8rem;color:#9ca3af;">Delivered by Portfolio CMS</p>'
        '</div>'
    )
