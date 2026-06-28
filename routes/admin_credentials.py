"""
routes/admin_credentials.py
Admin CRUD routes for Certificates and Badges.
Blueprint: admin_credentials
URL prefix: /admin/credentials
"""

import os
import uuid
from datetime import date
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from extensions import db
from models.certificates import Badge, Certificate
from services.tenant_resolver import resolve_active_tenant
from services.storage import validate_image_magic_bytes, save_tenant_upload

admin_credentials_bp = Blueprint("admin_credentials", __name__, url_prefix="/admin/credentials")

# ── Constants ────────────────────────────────────────────────────────────────
ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "svg", "webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


# ── Auth guard ────────────────────────────────────────────────────────────────
def tenant_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        tenant = resolve_active_tenant()
        if tenant is None or current_user.tenant_id != tenant.id:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Helpers ───────────────────────────────────────────────────────────────────
def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTS


def _save_credential_image(file_obj, tenant_id, subfolder):
    """Validate and persist an uploaded credential image.
    Returns filename string or raises ValueError."""
    if not _allowed_file(file_obj.filename):
        raise ValueError("File type not allowed. Use PNG, JPG, GIF, SVG, or WebP.")
    data = file_obj.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 5 MB limit.")
    if not validate_image_magic_bytes(data, file_obj.filename):
        raise ValueError("File content does not match declared extension.")
    file_obj.seek(0)
    ext = secure_filename(file_obj.filename).rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    save_tenant_upload(file_obj, tenant_id, subfolder, filename)
    return filename


# ═══════════════════════════════════════════════════════════════════════════════
# CERTIFICATES
# ═══════════════════════════════════════════════════════════════════════════════

@admin_credentials_bp.route("/certificates")
@tenant_admin_required
def certificates_index():
    tenant = resolve_active_tenant()
    certs = (
        Certificate.query
        .filter_by(tenant_id=tenant.id)
        .order_by(Certificate.sort_order.asc(), Certificate.issue_date.desc())
        .all()
    )
    return render_template("admin/credentials/certificates.html", certificates=certs)


@admin_credentials_bp.route("/certificates/new", methods=["GET", "POST"])
@tenant_admin_required
def certificate_new():
    tenant = resolve_active_tenant()
    if request.method == "POST":
        return _certificate_save(tenant_id=tenant.id, cert=None)
    return render_template("admin/credentials/certificate_form.html", cert=None)


@admin_credentials_bp.route("/certificates/<int:cert_id>/edit", methods=["GET", "POST"])
@tenant_admin_required
def certificate_edit(cert_id):
    tenant = resolve_active_tenant()
    cert = Certificate.query.filter_by(id=cert_id, tenant_id=tenant.id).first_or_404()
    if request.method == "POST":
        return _certificate_save(tenant_id=tenant.id, cert=cert)
    return render_template("admin/credentials/certificate_form.html", cert=cert)


@admin_credentials_bp.route("/certificates/<int:cert_id>/delete", methods=["POST"])
@tenant_admin_required
def certificate_delete(cert_id):
    tenant = resolve_active_tenant()
    cert = Certificate.query.filter_by(id=cert_id, tenant_id=tenant.id).first_or_404()
    _delete_credential_image(cert.tenant_id, "certificates", cert.image_filename)
    db.session.delete(cert)
    db.session.commit()
    flash("Certificate deleted.", "success")
    return redirect(url_for("admin_credentials.certificates_index"))


@admin_credentials_bp.route("/certificates/reorder", methods=["POST"])
@tenant_admin_required
def certificates_reorder():
    """Drag-reorder: POST JSON {order: [id, id, ...]}"""
    tenant = resolve_active_tenant()
    data = request.get_json(force=True)
    order = data.get("order", [])
    for idx, cert_id in enumerate(order):
        Certificate.query.filter_by(id=cert_id, tenant_id=tenant.id).update({"sort_order": idx})
    db.session.commit()
    return jsonify({"ok": True})


def _certificate_save(tenant_id, cert):
    is_new = cert is None
    if is_new:
        cert = Certificate(tenant_id=tenant_id)

    cert.title = request.form.get("title", "").strip()
    cert.issuer = request.form.get("issuer", "").strip()
    cert.description = request.form.get("description", "").strip() or None
    cert.credential_id = request.form.get("credential_id", "").strip() or None
    cert.credential_url = request.form.get("credential_url", "").strip() or None
    cert.is_featured = bool(request.form.get("is_featured"))
    cert.is_visible = bool(request.form.get("is_visible", True))

    raw_issue = request.form.get("issue_date", "").strip()
    cert.issue_date = date.fromisoformat(raw_issue) if raw_issue else None
    raw_expiry = request.form.get("expiry_date", "").strip()
    cert.expiry_date = date.fromisoformat(raw_expiry) if raw_expiry else None

    # Validate required fields
    errors = []
    if not cert.title:
        errors.append("Title is required.")
    if not cert.issuer:
        errors.append("Issuer is required.")
    if errors:
        for e in errors:
            flash(e, "danger")
        return render_template("admin/credentials/certificate_form.html", cert=cert)

    # Image upload
    file_obj = request.files.get("image")
    if file_obj and file_obj.filename:
        try:
            old_filename = cert.image_filename
            cert.image_filename = _save_credential_image(file_obj, tenant_id, "certificates")
            if old_filename:
                _delete_credential_image(tenant_id, "certificates", old_filename)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("admin/credentials/certificate_form.html", cert=cert)

    if is_new:
        db.session.add(cert)
    db.session.commit()
    flash("Certificate saved.", "success")
    return redirect(url_for("admin_credentials.certificates_index"))


# ═══════════════════════════════════════════════════════════════════════════════
# BADGES
# ═══════════════════════════════════════════════════════════════════════════════

@admin_credentials_bp.route("/badges")
@tenant_admin_required
def badges_index():
    tenant = resolve_active_tenant()
    badges = (
        Badge.query
        .filter_by(tenant_id=tenant.id)
        .order_by(Badge.display_order.asc(), Badge.issued_date.desc())
        .all()
    )
    # Collect unique skill tags for filter UI
    skill_tags = sorted({b.skill_tag for b in badges if b.skill_tag})
    return render_template("admin/credentials/badges.html", badges=badges, skill_tags=skill_tags)


@admin_credentials_bp.route("/badges/new", methods=["GET", "POST"])
@tenant_admin_required
def badge_new():
    tenant = resolve_active_tenant()
    if request.method == "POST":
        return _badge_save(tenant_id=tenant.id, badge=None)
    return render_template("admin/credentials/badge_form.html", badge=None)


@admin_credentials_bp.route("/badges/<int:badge_id>/edit", methods=["GET", "POST"])
@tenant_admin_required
def badge_edit(badge_id):
    tenant = resolve_active_tenant()
    badge = Badge.query.filter_by(id=badge_id, tenant_id=tenant.id).first_or_404()
    if request.method == "POST":
        return _badge_save(tenant_id=tenant.id, badge=badge)
    return render_template("admin/credentials/badge_form.html", badge=badge)


@admin_credentials_bp.route("/badges/<int:badge_id>/delete", methods=["POST"])
@tenant_admin_required
def badge_delete(badge_id):
    tenant = resolve_active_tenant()
    badge = Badge.query.filter_by(id=badge_id, tenant_id=tenant.id).first_or_404()
    _delete_credential_image(badge.tenant_id, "badges", badge.image_filename)
    db.session.delete(badge)
    db.session.commit()
    flash("Badge deleted.", "success")
    return redirect(url_for("admin_credentials.badges_index"))


@admin_credentials_bp.route("/badges/reorder", methods=["POST"])
@tenant_admin_required
def badges_reorder():
    tenant = resolve_active_tenant()
    data = request.get_json(force=True)
    order = data.get("order", [])
    for idx, badge_id in enumerate(order):
        Badge.query.filter_by(id=badge_id, tenant_id=tenant.id).update({"display_order": idx})
    db.session.commit()
    return jsonify({"ok": True})


def _badge_save(tenant_id, badge):
    is_new = badge is None
    if is_new:
        badge = Badge(tenant_id=tenant_id)

    badge.name = request.form.get("name", "").strip()
    badge.provider = request.form.get("provider", "").strip()
    badge.skill_tag = request.form.get("skill_tag", "").strip() or None
    badge.verification_url = request.form.get("verification_url", "").strip() or None
    badge.image_url_external = request.form.get("image_url_external", "").strip() or None
    badge.is_visible = bool(request.form.get("is_visible", True))

    raw_issued = request.form.get("issued_date", "").strip()
    badge.issued_date = date.fromisoformat(raw_issued) if raw_issued else None

    errors = []
    if not badge.name:
        errors.append("Badge name is required.")
    if not badge.provider:
        errors.append("Provider is required.")
    if errors:
        for e in errors:
            flash(e, "danger")
        return render_template("admin/credentials/badge_form.html", badge=badge)

    file_obj = request.files.get("image")
    if file_obj and file_obj.filename:
        try:
            old_filename = badge.image_filename
            badge.image_filename = _save_credential_image(file_obj, tenant_id, "badges")
            if old_filename:
                _delete_credential_image(tenant_id, "badges", old_filename)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("admin/credentials/badge_form.html", badge=badge)

    if is_new:
        db.session.add(badge)
    db.session.commit()
    flash("Badge saved.", "success")
    return redirect(url_for("admin_credentials.badges_index"))


# ── Utility ───────────────────────────────────────────────────────────────────
def _delete_credential_image(tenant_id, subfolder, filename):
    """Best-effort delete of a stored credential image."""
    if not filename:
        return
    upload_root = current_app.config.get("UPLOAD_FOLDER", "uploads")
    path = os.path.join(upload_root, str(tenant_id), subfolder, filename)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass  # Non-fatal; log in production
