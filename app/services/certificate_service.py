"""
app/services/certificate_service.py — Certificates & Badges business logic
(v6.7)

CONTRACT
--------
Routes stay thin: parse the form, call one of these functions, flash/redirect.
This module owns:
  • image upload validation + save/delete (delegates to app.utils.save_image /
    delete_image — the existing, single upload pipeline; no parallel storage
    system is created here, per the architecture constraint)
  • skills-tag normalization
  • tenant-ownership enforcement on update/delete (defense in depth on top of
    the route-level `_require_tenant_object` check)
  • reorder / toggle-featured mutation logic

Upload PLAN-QUOTA gating (max_media_uploads) intentionally stays in the route
layer (app/admin/routes/certificates.py), matching the existing Testimonial/
Project call sites — it needs `_active_tenant_plan_features()` and
`_tenant_media_upload_count()` from app.admin.blueprint, and importing those
here would create a services→admin circular dependency the rest of this
codebase deliberately avoids (services/ has no admin/ imports anywhere else).

Every mutation still leaves `db.session.commit()` to the caller for the two
image-cleanup-then-create/update writes below — this matches the existing
codebase convention (testimonials.py, projects_uploads.py) where the admin
route owns the transaction boundary, not the service. Session-level tenant
isolation (which tenant_id/tenant_slug get stamped) is owned here so it can
never be forgotten at a new call site.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Optional

from app import db
from app.models.portfolio import Certificate
from app.repositories import certificate_repository
from app.utils import save_image, delete_image, is_upload_file

UPLOAD_SUBFOLDER = 'certificates'
IMAGE_MAX_SIZE = (800, 600)   # certificate image — landscape card
BADGE_MAX_SIZE = (300, 300)   # badge — square icon


class CertificateValidationError(Exception):
    """Raised for business-rule violations that aren't WTForms field errors
    (e.g. expiration_date before issue_date)."""


def _normalize_skills(raw: str | None) -> str:
    """Comma-separated skills string, deduplicated, trimmed, order-preserved."""
    if not raw:
        return ''
    seen = set()
    out = []
    for part in raw.split(','):
        s = part.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return ', '.join(out)


def _validate_dates(issue_date, expiration_date) -> None:
    if issue_date and expiration_date and expiration_date < issue_date:
        raise CertificateValidationError('Expiration date cannot be before the issue date.')


def _save_upload(file_storage, *, max_size) -> tuple[Optional[str], Optional[str]]:
    """Wraps app.utils.save_image with the certificate subfolder/size preset.
    Returns (filename, error) — same contract as save_image itself."""
    if not is_upload_file(file_storage):
        return None, None
    return save_image(file_storage, UPLOAD_SUBFOLDER, max_size=max_size)


def create_certificate(
    *,
    tenant_id: int,
    tenant_slug: str,
    form,
    image_file=None,
    badge_file=None,
) -> tuple[Optional[Certificate], Optional[str]]:
    """
    Build and stage (add, not commit) a new Certificate for the active tenant.

    Returns (certificate, error). On error, no image files were written to
    disk and nothing was added to the session.
    """
    issue_date = form.issue_date.data
    expiration_date = form.expiration_date.data
    try:
        _validate_dates(issue_date, expiration_date)
    except CertificateValidationError as exc:
        return None, str(exc)

    image_name, image_err = _save_upload(image_file, max_size=IMAGE_MAX_SIZE)
    if image_err:
        return None, image_err

    badge_name, badge_err = _save_upload(badge_file, max_size=BADGE_MAX_SIZE)
    if badge_err:
        if image_name:
            delete_image(image_name, UPLOAD_SUBFOLDER)
        return None, badge_err

    cert = Certificate(
        tenant_id=tenant_id,          # cross-DB: must be set explicitly, no FK
        tenant_slug=tenant_slug,
        title=form.title.data.strip(),
        issuer=form.issuer.data.strip(),
        description=(form.description.data or '').strip(),
        credential_id=(form.credential_id.data or '').strip(),
        verification_url=(form.verification_url.data or '').strip(),
        issue_date=issue_date,
        expiration_date=expiration_date,
        skills=_normalize_skills(form.skills.data),
        is_featured=form.is_featured.data,
        is_visible=form.is_visible.data,
        display_order=form.display_order.data or 0,
        image_path=image_name or '',
        badge_path=badge_name or '',
    )
    certificate_repository.add(cert)
    return cert, None


def update_certificate(
    *,
    certificate: Certificate,
    form,
    image_file=None,
    badge_file=None,
) -> tuple[bool, Optional[str]]:
    """
    Apply form data to an existing Certificate in place.

    Caller is responsible for the tenant-ownership check (route-level
    `_require_tenant_object`) before calling this — this function trusts the
    `certificate` instance it's given.
    """
    issue_date = form.issue_date.data
    expiration_date = form.expiration_date.data
    try:
        _validate_dates(issue_date, expiration_date)
    except CertificateValidationError as exc:
        return False, str(exc)

    new_image, image_err = _save_upload(image_file, max_size=IMAGE_MAX_SIZE)
    if image_err:
        return False, image_err

    new_badge, badge_err = _save_upload(badge_file, max_size=BADGE_MAX_SIZE)
    if badge_err:
        if new_image:
            delete_image(new_image, UPLOAD_SUBFOLDER)
        return False, badge_err

    certificate.title = form.title.data.strip()
    certificate.issuer = form.issuer.data.strip()
    certificate.description = (form.description.data or '').strip()
    certificate.credential_id = (form.credential_id.data or '').strip()
    certificate.verification_url = (form.verification_url.data or '').strip()
    certificate.issue_date = issue_date
    certificate.expiration_date = expiration_date
    certificate.skills = _normalize_skills(form.skills.data)
    certificate.is_featured = form.is_featured.data
    certificate.is_visible = form.is_visible.data
    certificate.display_order = form.display_order.data or 0

    if new_image:
        if certificate.image_path:
            delete_image(certificate.image_path, UPLOAD_SUBFOLDER)
        certificate.image_path = new_image

    if new_badge:
        if certificate.badge_path:
            delete_image(certificate.badge_path, UPLOAD_SUBFOLDER)
        certificate.badge_path = new_badge

    return True, None


def delete_certificate(certificate: Certificate) -> None:
    """Remove both associated images from disk, then stage the row for
    deletion. Caller still owns db.session.commit()."""
    if certificate.image_path:
        delete_image(certificate.image_path, UPLOAD_SUBFOLDER)
    if certificate.badge_path:
        delete_image(certificate.badge_path, UPLOAD_SUBFOLDER)
    certificate_repository.delete(certificate)


def reorder_certificates(tenant_slug: str, order_payload: list[dict]) -> int:
    """
    Apply a bulk reorder. `order_payload` is [{'id': int, 'order': int}, ...]
    from the admin drag-and-drop UI (same shape as reorder_testimonials).

    Tenant-scoped by construction — only certificates matching tenant_slug
    are ever touched, regardless of what ids the client sends.
    Returns the count of rows actually updated.
    """
    updated = 0
    for item in order_payload:
        cert_id = item.get('id')
        if cert_id is None:
            continue
        cert = certificate_repository.get_for_tenant(cert_id, tenant_slug)
        if cert:
            cert.display_order = item.get('order', 0)
            updated += 1
    return updated


def toggle_featured(certificate: Certificate) -> bool:
    """Flip is_featured. Returns the new value."""
    certificate.is_featured = not certificate.is_featured
    return certificate.is_featured


def toggle_visible(certificate: Certificate) -> bool:
    """Flip is_visible. Returns the new value."""
    certificate.is_visible = not certificate.is_visible
    return certificate.is_visible
