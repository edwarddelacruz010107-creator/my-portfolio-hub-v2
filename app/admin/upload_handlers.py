"""
app/admin/upload_handlers.py — Quota-Enforced Upload Handlers (v6.0)

Drop this module into app/admin/ and register its blueprint in app/__init__.py
(or integrate into the existing admin blueprint via import).

This replaces any existing ad-hoc upload handler in admin/__init__.py.

INTEGRATION:
    In app/admin/__init__.py, import and call:

        from app.admin.upload_handlers import register_upload_routes
        register_upload_routes(admin_bp)

    Or register the sub-blueprint:
        from app.admin.upload_handlers import uploads_bp
        app.register_blueprint(uploads_bp, url_prefix='/studio')
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    request,
)
from flask_login import current_user, login_required

from app import db, limiter
from app.models.core import Tenant
from app.models.core_additions import MediaUpload, PlanUsageLog
from app.services.storage_service import (
    InvalidFileError,
    FileSizeError,
    QuotaExceededError,
    delete_upload,
    get_quota_summary,
    save_upload,
)
from app.middleware.subscription_guard import require_active_subscription

logger = logging.getLogger(__name__)

uploads_bp = Blueprint('uploads', __name__)


def _get_tenant() -> Tenant:
    tenant = Tenant.query.get(current_user.tenant_id)
    if not tenant:
        abort(403)
    return tenant


@uploads_bp.route('/upload', methods=['POST'])
@login_required
@require_active_subscription
@limiter.limit('20 per minute')
def upload_file():
    """
    Generic file upload endpoint.

    Form fields:
        file     — the binary file
        category — 'project' | 'page' | 'general' (default: 'general')

    Returns JSON:
        { success: true,  file_path, thumb_path, file_size, used_mb, limit_mb, pct }
        { success: false, error: str }
    """
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file attached.'}), 400

    f        = request.files['file']
    category = request.form.get('category', 'general')

    if f.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename.'}), 400

    # Resolve MIME from the browser-provided content-type (accept as hint only;
    # actual bytes are validated by Pillow / path guard inside save_upload)
    mime_type = f.content_type or 'application/octet-stream'

    tenant = _get_tenant()

    try:
        meta = save_upload(
            tenant=tenant,
            file_obj=f.stream,
            filename=f.filename,
            mime_type=mime_type,
            category=category,
            generate_thumb=True,
        )
    except (InvalidFileError, FileSizeError) as exc:
        logger.warning('[upload] REJECTED tenant_id=%s reason=%s', tenant.id, exc)
        return jsonify({'success': False, 'error': str(exc)}), 422
    except QuotaExceededError as exc:
        PlanUsageLog.record(tenant.id, 'upload_denied', value=0, reason=str(exc))
        return jsonify({'success': False, 'error': str(exc), 'quota_exceeded': True}), 422
    except Exception as exc:
        logger.exception('[upload] Unexpected error tenant_id=%s: %s', tenant.id, exc)
        return jsonify({'success': False, 'error': 'Upload failed due to a server error.'}), 500

    # Persist MediaUpload record
    record = MediaUpload(
        tenant_id=tenant.id,
        file_path=meta['file_path'],
        thumb_path=meta.get('thumb_path'),
        file_size=meta['file_size'],
        original_size=meta['original_size'],
        mime_type=meta['mime_type'],
        category=meta['category'],
        original_name=meta.get('original_name', ''),
    )
    db.session.add(record)
    PlanUsageLog.record(tenant.id, 'upload_saved', value=meta['file_size'],
                        category=category, mime=meta['mime_type'])
    db.session.commit()

    quota = get_quota_summary(tenant)

    response = {
        'success':    True,
        'file_path':  meta['file_path'],
        'thumb_path': meta.get('thumb_path'),
        'file_size':  meta['file_size'],
        'upload_id':  record.id,
        'quota': {
            'used_mb':  quota['used_mb'],
            'limit_mb': quota['limit_mb'],
            'pct':      quota['pct'],
            'warning':  quota['warning'],
        },
    }

    if quota['warning']:
        response['quota_warning'] = (
            f"⚠️ You're using {quota['pct']}% of your storage ({quota['used_mb']} MB / "
            f"{quota['limit_mb']} MB). Upgrade your plan to continue uploading."
        )

    return jsonify(response), 201


@uploads_bp.route('/upload/<int:upload_id>', methods=['DELETE'])
@login_required
@limiter.limit('30 per minute')
def delete_file(upload_id: int):
    """
    Delete an uploaded file and reclaim quota.

    Returns JSON: { success: bool, reclaimed_bytes: int }
    """
    tenant = _get_tenant()
    record = MediaUpload.query.filter_by(
        id=upload_id, tenant_id=tenant.id, is_deleted=False,
    ).first_or_404()

    reclaimed = record.file_size or 0
    delete_upload(tenant, record)
    db.session.commit()

    return jsonify({'success': True, 'reclaimed_bytes': reclaimed})


@uploads_bp.route('/quota', methods=['GET'])
@login_required
def quota_status():
    """Return current quota summary for the authenticated tenant."""
    tenant = _get_tenant()
    return jsonify(get_quota_summary(tenant))


def register_upload_routes(admin_bp):
    """
    Alternative to sub-blueprint: registers upload routes directly on admin_bp.
    Call this at the bottom of app/admin/__init__.py.
    """
    admin_bp.add_url_rule('/upload',
        view_func=login_required(upload_file), methods=['POST'])
    admin_bp.add_url_rule('/upload/<int:upload_id>',
        view_func=login_required(delete_file), methods=['DELETE'])
    admin_bp.add_url_rule('/quota',
        view_func=login_required(quota_status), methods=['GET'])
