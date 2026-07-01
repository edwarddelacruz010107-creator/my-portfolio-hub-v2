"""
app/admin/routes/certificates.py — Certificates & Badges CRUD + reorder

Follows the exact route/blueprint pattern established in
app/admin/routes/testimonials.py, with one deliberate difference: mutation
logic (image handling, date validation, tenant stamping) lives in
app/services/certificate_service.py instead of inline in the route, per the
Routes → Services → Repositories → Models layering used for this feature.
"""
import logging

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import current_user

from app import db
from app.repositories import certificate_repository
from app.models.portfolio import Certificate
from app.forms import CertificateForm
from app.utils import log_activity
from app import limiter  # Flask-Limiter instance

from app.admin.blueprint import (
    admin, admin_required, _active_tenant_slug, _tenant_slug_filter,
    _require_tenant_object, _tenant_media_upload_count,
    _active_tenant_plan_features, _active_tenant_plan_name,
)
from app.services import certificate_service

logger = logging.getLogger(__name__)


def _certificate_upload_slots_used() -> int:
    """Extends the existing plan-quota counter (_tenant_media_upload_count)
    with certificate image/badge uploads, so the per-plan media cap can't be
    bypassed through this new upload path. Counts each certificate's image
    and badge as separate slots, matching how Testimonial.author_avatar and
    Project.image are counted today."""
    base = _tenant_media_upload_count()
    q = _tenant_slug_filter(certificate_repository.query)
    image_count = q.filter(Certificate.image_path != None).filter(Certificate.image_path != '').count()
    badge_count = _tenant_slug_filter(certificate_repository.query) \
        .filter(Certificate.badge_path != None).filter(Certificate.badge_path != '').count()
    return base + image_count + badge_count


def _check_upload_quota(*, replacing_image: bool, replacing_badge: bool) -> str | None:
    """Returns a warning message if the plan's max_media_uploads would be
    exceeded by this create/edit, else None. Mirrors the inline check in
    testimonials.py / projects_uploads.py."""
    plan_features = _active_tenant_plan_features()
    max_uploads = plan_features.get('max_media_uploads')
    if max_uploads is None:
        return None
    current = _certificate_upload_slots_used()
    # Replacing an existing image/badge doesn't add a new slot.
    new_slots = (0 if replacing_image else 1) + (0 if replacing_badge else 1)
    if current + new_slots > max_uploads:
        return (
            f'Your current plan ({_active_tenant_plan_name()}) allows up to '
            f'{max_uploads} uploads. Remove an existing asset or upgrade.'
        )
    return None


@admin.route('/certificates')
@admin_required
def certificates():
    all_certs = certificate_repository.list_for_tenant(_active_tenant_slug()).all()
    return render_template('admin/certificates.html', certificates=all_certs)


@admin.route('/certificates/new', methods=['GET', 'POST'])
@admin_required
def new_certificate():
    form = CertificateForm()
    if form.validate_on_submit():
        quota_warning = _check_upload_quota(
            replacing_image=not bool(form.image_file.data and form.image_file.data.filename),
            replacing_badge=not bool(form.badge_file.data and form.badge_file.data.filename),
        )
        # Quota is evaluated pre-write; if it fails, skip both uploads entirely
        # rather than partially saving one file then rejecting the row.
        if quota_warning:
            flash(quota_warning, 'warning')
            return render_template('admin/certificate_form.html', form=form, certificate=None)

        cert, error = certificate_service.create_certificate(
            tenant_id=current_user.tenant_id,
            tenant_slug=_active_tenant_slug(),
            form=form,
            image_file=form.image_file.data,
            badge_file=form.badge_file.data,
        )
        if error:
            flash(error, 'danger')
            return render_template('admin/certificate_form.html', form=form, certificate=None)

        db.session.commit()
        log_activity('create', 'certificate', cert.title)
        flash('Certificate added!', 'success')
        return redirect(url_for('admin.certificates'))
    return render_template('admin/certificate_form.html', form=form, certificate=None)


@admin.route('/certificates/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_certificate(id: int):
    cert = _require_tenant_object(certificate_repository.get(id))
    if cert is None:
        flash('Certificate not found.', 'warning')
        return redirect(url_for('admin.certificates'))

    form = CertificateForm(obj=cert)
    if request.method == 'GET':
        form.skills.data = cert.skills

    if form.validate_on_submit():
        quota_warning = None
        replacing_image = not (cert.image_path and not (form.image_file.data and form.image_file.data.filename))
        replacing_badge = not (cert.badge_path and not (form.badge_file.data and form.badge_file.data.filename))
        # Only re-check quota if a *new* image/badge is actually being uploaded
        # for a slot that wasn't already filled — matches create-path semantics.
        adding_image = bool(form.image_file.data and form.image_file.data.filename) and not cert.image_path
        adding_badge = bool(form.badge_file.data and form.badge_file.data.filename) and not cert.badge_path
        if adding_image or adding_badge:
            quota_warning = _check_upload_quota(
                replacing_image=not adding_image,
                replacing_badge=not adding_badge,
            )
        if quota_warning:
            flash(quota_warning, 'warning')
            return render_template('admin/certificate_form.html', form=form, certificate=cert)

        ok, error = certificate_service.update_certificate(
            certificate=cert,
            form=form,
            image_file=form.image_file.data,
            badge_file=form.badge_file.data,
        )
        if not ok:
            flash(error, 'danger')
            return render_template('admin/certificate_form.html', form=form, certificate=cert)

        db.session.commit()
        log_activity('update', 'certificate', cert.title)
        flash('Certificate updated!', 'success')
        return redirect(url_for('admin.certificates'))

    return render_template('admin/certificate_form.html', form=form, certificate=cert)


@admin.route('/certificates/<int:id>/delete', methods=['POST'])
@admin_required
@limiter.limit('30 per minute')
def delete_certificate(id: int):
    cert = _require_tenant_object(certificate_repository.get(id))
    if cert is None:
        flash('Certificate not found.', 'warning')
        return redirect(url_for('admin.certificates'))
    title = cert.title
    certificate_service.delete_certificate(cert)
    db.session.commit()
    log_activity('delete', 'certificate', title)
    flash(f'Certificate "{title}" deleted.', 'success')
    return redirect(url_for('admin.certificates'))


@admin.route('/certificates/<int:id>/toggle-featured', methods=['POST'])
@admin_required
def toggle_certificate_featured(id: int):
    cert = _require_tenant_object(certificate_repository.get(id))
    if cert is None:
        return jsonify(status='error', message='Certificate not found.'), 404
    new_state = certificate_service.toggle_featured(cert)
    db.session.commit()
    log_activity('update', 'certificate', cert.title, description='toggled featured')
    return jsonify(status='ok', is_featured=new_state)


@admin.route('/certificates/<int:id>/toggle-visible', methods=['POST'])
@admin_required
def toggle_certificate_visible(id: int):
    cert = _require_tenant_object(certificate_repository.get(id))
    if cert is None:
        return jsonify(status='error', message='Certificate not found.'), 404
    new_state = certificate_service.toggle_visible(cert)
    db.session.commit()
    log_activity('update', 'certificate', cert.title, description='toggled visibility')
    return jsonify(status='ok', is_visible=new_state)


@admin.route('/certificates/reorder', methods=['POST'])
@admin_required
def reorder_certificates():
    data = request.get_json(force=True, silent=True) or {}
    updated = certificate_service.reorder_certificates(_active_tenant_slug(), data.get('order', []))
    db.session.commit()
    return jsonify(status='ok', updated=updated)
