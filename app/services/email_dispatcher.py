from __future__ import annotations

import html
import logging
import re
from typing import Optional

from app.services.email.email_service import EmailService
from app.services.tenant.tenant_email_service import send_tenant_email

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://[\w\-.]+', re.IGNORECASE)


def _escape(value: Optional[str]) -> str:
    return html.escape((value or '').strip(), quote=False)


def _normalize_text(value: Optional[str]) -> str:
    return (value or '').strip()


def _url_count(text: str) -> int:
    return len(_URL_RE.findall(text or ''))


def send_email(
    *,
    category: str = 'general',
    provider: str = 'auto',
    tenant_id: Optional[int] = None,
    to: str,
    subject: str,
    html_content: str,
    text_content: str,
    reply_to: Optional[str] = None,
    to_name: Optional[str] = None,
) -> tuple[bool, str]:
    """Send email using tenant provider chain or the global provider chain."""
    if provider in ('auto', 'tenant') and tenant_id is not None:
        ok, err = send_tenant_email(
            tenant_id=tenant_id,
            to=to,
            subject=subject,
            html=html_content,
            text=text_content,
            reply_to=reply_to,
        )
        if ok:
            logger.info(
                'email_dispatch: category=%s tenant=%s delivered via tenant chain to=%s',
                category,
                tenant_id,
                _escape(to),
            )
            return True, ''
        logger.warning(
            'email_dispatch: category=%s tenant=%s tenant chain failed %s',
            category,
            tenant_id,
            err,
        )
        if provider == 'tenant':
            return False, err

    try:
        svc = EmailService()
        ok, err = svc.send_email(
            to=to,
            subject=subject,
            text=text_content,
            html=html_content,
            reply_to=reply_to,
            to_name=to_name,
            portal='tenant',
        )
        if ok:
            logger.info(
                'email_dispatch: category=%s global delivered to=%s',
                category,
                _escape(to),
            )
            return True, ''
        logger.error(
            'email_dispatch: category=%s global send failed to=%s error=%s',
            category,
            _escape(to),
            err,
        )
        return False, err
    except Exception as exc:  # noqa: BLE001
        logger.exception('email_dispatch: unexpected error sending %s email to %s', category, to)
        return False, str(exc)


def build_landing_admin_email(
    name: str,
    email_address: str,
    subject: str,
    message: str,
    company: str = '',
    phone: str = '',
    source: str = 'landing_page',
) -> tuple[str, str, str]:
    """Return subject, text body, html body for landing page admin notification."""
    safe_name = _escape(name)
    safe_email = _escape(email_address)
    safe_company = _escape(company)
    safe_phone = _escape(phone)
    safe_subject = _escape(subject or f'Contact from {name}')
    safe_message = _escape(message).replace('\n', '<br>')

    subject_line = f'[Portfolio] New landing inquiry from {safe_name}'
    text_body = (
        f'New landing page inquiry:\n'
        f'Name: {safe_name}\n'
        f'Email: {safe_email}\n'
        f'Company: {safe_company or "(not provided)"}\n'
        f'Phone: {safe_phone or "(not provided)"}\n'
        f'Source: {source}\n'
        f'Subject: {safe_subject}\n\n'
        f'{message}\n'
    )
    html_body = f'''
<div style="font-family:Inter,system-ui,sans-serif;margin:0;padding:0;background:#0f172a;color:#e2e8f0;">
  <div style="max-width:720px;margin:0 auto;padding:24px;">
    <div style="background:#111827;border:1px solid #1f2937;border-radius:18px;overflow:hidden;">
      <div style="background:linear-gradient(135deg,#6366f1,#22d3ee);padding:24px;">
        <h1 style="margin:0;font-size:1.7rem;font-weight:700;">New landing page inquiry</h1>
        <p style="margin:.75rem 0 0;color:#cbd5e1;line-height:1.6;">A visitor submitted the public landing page contact form.</p>
      </div>
      <div style="padding:24px;background:#0f172a;color:#e2e8f0;">
        <p style="margin:0 0 1rem;color:#94a3b8;">Source: <strong>{source}</strong></p>
        <p style="margin:0 0 .5rem;"><strong>Name:</strong> {safe_name}</p>
        <p style="margin:0 0 .5rem;"><strong>Email:</strong> <a href="mailto:{safe_email}" style="color:#38bdf8;text-decoration:none;">{safe_email}</a></p>
        <p style="margin:0 0 .5rem;"><strong>Company:</strong> {safe_company or '<span style="color:#94a3b8;">(not provided)</span>'}</p>
        <p style="margin:0 0 1rem;"><strong>Phone:</strong> {safe_phone or '<span style="color:#94a3b8;">(not provided)</span>'}</p>
        <p style="margin:0 0 .75rem;"><strong>Subject:</strong> {safe_subject}</p>
        <div style="background:#111827;border:1px solid #1f2937;border-radius:14px;padding:18px;margin-top:1rem;line-height:1.7;color:#e2e8f0;">{safe_message}</div>
        <p style="margin:2rem 0 0;color:#94a3b8;font-size:.95rem;line-height:1.6;">This message was received via the Portfolio Hub landing page contact form. Check the SuperAdmin Messages inbox for the full conversation thread.</p>
      </div>
    </div>
  </div>
</div>
'''
    return subject_line, text_body, html_body


def build_landing_auto_reply_email(
    name: str,
    subject: str,
) -> tuple[str, str, str]:
    """Return subject, text body, html body for landing page visitor confirmation."""
    safe_name = _escape(name) or 'there'
    safe_subject = _escape(subject or 'your message')
    subject_line = 'Thanks for contacting Portfolio Hub'
    text_body = (
        f'Hi {safe_name},\n\n'
        f'Thank you for reaching out to Portfolio Hub. We received your message about "{safe_subject}" and will respond within one business day.\n\n'
        'If you need to update your request, just reply to this email.\n\n'
        'Thanks again for contacting us.\n\n'
        '— Portfolio Hub Team\n'
    )
    html_body = f'''
<div style="font-family:Inter,system-ui,sans-serif;margin:0;padding:0;background:#0f172a;color:#e2e8f0;">
  <div style="max-width:680px;margin:0 auto;padding:24px;">
    <div style="background:#111827;border:1px solid #1f2937;border-radius:18px;overflow:hidden;">
      <div style="background:linear-gradient(135deg,#6366f1,#22d3ee);padding:24px;">
        <h1 style="margin:0;font-size:1.8rem;font-weight:700;">Thanks for reaching out, {safe_name}</h1>
      </div>
      <div style="padding:24px;background:#0f172a;color:#e2e8f0;line-height:1.7;">
        <p style="margin:0 0 1rem;color:#cbd5e1;">We received your inquiry about <strong>{safe_subject}</strong>.</p>
        <p style="margin:0 0 1rem;">A member of our team will review your message and respond within one business day.</p>
        <div style="background:#111827;border:1px solid #1f2937;border-radius:14px;padding:18px;margin:1.5rem 0;">
          <p style="margin:0;color:#e2e8f0;">If you need to update your request, simply reply to this email and we’ll pick it back up from there.</p>
        </div>
        <p style="margin:0;color:#94a3b8;">Thanks again for contacting Portfolio Hub.</p>
        <p style="margin:1.5rem 0 0;color:#94a3b8;font-size:.9rem;">Portfolio Hub Team</p>
      </div>
    </div>
  </div>
</div>
'''
    return subject_line, text_body, html_body
