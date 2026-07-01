"""
app/superadmin/routes/media.py — Media library: list / delete / compress / orphan cleanup (Phase 4b, batch 1)

Moved here verbatim from the former monolithic app/superadmin/__init__.py.
No behavior, route, or endpoint-name changes — see blueprint split plan.
"""

import csv
import io
import logging
import re
import secrets
import string
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for,
    flash, request, session, current_app, Response,
)
from urllib.parse import urlparse
from flask_login import current_user, logout_user, login_required
from pathlib import Path
from sqlalchemy import or_, func
from werkzeug.utils import secure_filename

from app.auth import _handle_login
from app.forms import (
    ChangePasswordForm, SuperadminAccountForm,
    TenantForm, SuperadminMessageForm, PaymentInstructionForm, PaymentMethodForm,
)
from app import db, limiter
from app.repositories import (
    profile_repository,
    tenant_repository,
    user_repository,
    project_repository,
    testimonial_repository,
    inquiry_repository,
    activity_log_repository,
    subscription_repository,
    payment_method_repository,
    payment_submission_repository,
    subscription_notification_repository,
    webhook_event_repository,
    global_email_config_repository,
)
from app.services.manual_billing import (
    approve_payment_submission,
    reject_payment_submission,
    save_billing_upload,
    set_default_payment_method,
)
from app.services.tenant_admin import delete_tenant_completely
from app.utils import is_paymongo_enabled, set_paymongo_enabled
from app.models import User
from app.models.portfolio import (Profile, PaymentMethod, PaymentSubmission, Subscription, WebhookEvent,
                                   ActivityLog, Project, Inquiry, Tenant, PaymentInstruction, PAID_PLAN_NAMES,
                                   normalize_plan_name)


from app.utils import log_activity, BILLING_PLANS, YEARLY_DISCOUNT
from app.security import log_security_event
from app.tenant_security import RESERVED_SLUGS, validate_slug, stamp_session_tenant
from app.models.portfolio import TenantCommunicationSettings
from app.models.portfolio import _utcnow
from app.services.billing import (
    compute_billing_metrics,
    tenant_billing_summary,
    force_activate_subscription,
    sync_subscription_from_paymongo,
)


from app.superadmin.blueprint import superadmin, superadmin_required

logger = logging.getLogger(__name__)


def _sa_format_filesize(size) -> str:
    """Human-readable filesize for the superadmin media manager."""
    if size is None:
        return 'n/a'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'

@superadmin.route('/media')
@superadmin_required
def media():
    """Cross-tenant image & upload manager — list, filter, delete, compress."""
    import os
    from app.models.portfolio import Testimonial

    asset_type    = request.args.get('asset_type', 'all')
    tenant_filter = request.args.get('tenant', 'all')
    allowed_types = {'all', 'profile', 'project', 'testimonial', 'billing', 'proof'}
    if asset_type not in allowed_types:
        asset_type = 'all'

    all_profiles     = profile_repository.query.order_by(Profile.tenant_slug).all()
    all_projects     = (project_repository.query
                        .filter(Project.image != None, Project.image != '')
                        .order_by(Project.tenant_slug, Project.created_at.desc())
                        .all())
    all_testimonials = (testimonial_repository.query
                        .filter(Testimonial.author_avatar != None,
                                Testimonial.author_avatar != '')
                        .order_by(Testimonial.tenant_slug, Testimonial.created_at.desc())
                        .all())
    all_billing = (payment_method_repository.query
                   .filter(PaymentMethod.qr_image != None,
                           PaymentMethod.qr_image != '')
                   .order_by(PaymentMethod.id)
                   .all())

    assets = []

    for p in all_profiles:
        if p.profile_image:
            assets.append({
                'id': p.id, 'type': 'profile', 'label': 'Profile Image',
                'tenant': p.tenant_slug,
                'filename': p.profile_image, 'folder': 'profiles',
                'description': p.name or p.tenant_slug,
                'url': url_for('static', filename=f'uploads/profiles/{p.profile_image}'),
            })

    for proj in all_projects:
        assets.append({
            'id': proj.id, 'type': 'project', 'label': 'Project Image',
            'tenant': proj.tenant_slug,
            'filename': proj.image, 'folder': 'projects',
            'description': proj.title,
            'url': url_for('static', filename=f'uploads/projects/{proj.image}'),
        })

    for t in all_testimonials:
        assets.append({
            'id': t.id, 'type': 'testimonial', 'label': 'Testimonial Avatar',
            'tenant': t.tenant_slug,
            'filename': t.author_avatar, 'folder': 'profiles',
            'description': t.author_name,
            'url': url_for('static', filename=f'uploads/profiles/{t.author_avatar}'),
        })

    for pm in all_billing:
        assets.append({
            'id': pm.id, 'type': 'billing', 'label': 'Payment QR Code',
            'tenant': '(superadmin)',
            'filename': pm.qr_image, 'folder': 'billing',
            'description': pm.name,
            'url': url_for('static', filename=f'uploads/billing/{pm.qr_image}'),
        })

    # ── Payment submission proof images ───────────────────────────────
    all_proofs = (payment_submission_repository.query
                  .filter(PaymentSubmission.payment_proof != None,
                          PaymentSubmission.payment_proof != '')
                  .order_by(PaymentSubmission.submitted_at.desc())
                  .all())
    for sub in all_proofs:
        tenant_slug = sub.tenant.slug if sub.tenant else '(unknown)'
        assets.append({
            'id': sub.id, 'type': 'proof', 'label': 'Payment Proof',
            'tenant': tenant_slug,
            'filename': sub.payment_proof, 'folder': 'billing',
            'description': f'{sub.payment_method} — {sub.plan} — {sub.status}',
            'url': url_for('static', filename=f'uploads/billing/{sub.payment_proof}'),
        })

    for asset in assets:
        path = os.path.join(current_app.static_folder, 'uploads', asset['folder'], asset['filename'])
        try:
            asset['size_bytes'] = os.path.getsize(path)
        except OSError:
            asset['size_bytes'] = None
        asset['size_text'] = _sa_format_filesize(asset['size_bytes'])
        asset['exists']    = asset['size_bytes'] is not None

    all_tenant_slugs = sorted({a['tenant'] for a in assets})

    filtered = assets
    if asset_type != 'all':
        filtered = [a for a in filtered if a['type'] == asset_type]
    if tenant_filter != 'all':
        filtered = [a for a in filtered if a['tenant'] == tenant_filter]

    counts = {
        'all':         len(assets),
        'profile':     sum(1 for a in assets if a['type'] == 'profile'),
        'project':     sum(1 for a in assets if a['type'] == 'project'),
        'testimonial': sum(1 for a in assets if a['type'] == 'testimonial'),
        'billing':     sum(1 for a in assets if a['type'] == 'billing'),
        'proof':       sum(1 for a in assets if a['type'] == 'proof'),
    }
    total_bytes  = sum(a['size_bytes'] for a in assets if a['size_bytes'])
    orphan_count = sum(1 for a in assets if not a['exists'])

    return render_template(
        'superadmin/media.html',
        assets=filtered,
        asset_type=asset_type,
        tenant_filter=tenant_filter,
        all_tenant_slugs=all_tenant_slugs,
        counts=counts,
        total_assets=counts['all'],
        total_size=_sa_format_filesize(total_bytes),
        orphan_count=orphan_count,
    )

@superadmin.route('/media/delete', methods=['POST'])
@superadmin_required
@limiter.limit('30 per minute')
def media_delete():
    """Delete a single uploaded file and clear the DB reference."""
    import os
    from app.models.portfolio import Testimonial

    asset_type   = request.form.get('asset_type')
    try:
        asset_id = int(request.form.get('asset_id') or 0) or None
    except (TypeError, ValueError):
        asset_id = None

    def _rm(folder: str, filename: str) -> None:
        if not filename:
            return
        path = os.path.join(current_app.static_folder, 'uploads', folder, filename)
        try:
            os.remove(path)
        except OSError:
            pass

    if asset_type == 'profile':
        p = db.session.get(Profile, asset_id)
        if p and p.profile_image:
            _rm('profiles', p.profile_image)
            p.profile_image = None
            db.session.commit()
            flash(f'Profile image deleted for tenant "{p.tenant_slug}".', 'success')
        else:
            flash('Profile image not found.', 'warning')
    elif asset_type == 'project':
        proj = db.session.get(Project, asset_id)
        if proj and proj.image:
            _rm('projects', proj.image)
            proj.image = None
            db.session.commit()
            flash(f'Project image deleted: "{proj.title}".', 'success')
        else:
            flash('Project image not found.', 'warning')
    elif asset_type == 'testimonial':
        t = db.session.get(Testimonial, asset_id)
        if t and t.author_avatar:
            _rm('profiles', t.author_avatar)
            t.author_avatar = None
            db.session.commit()
            flash(f'Testimonial avatar deleted: "{t.author_name}".', 'success')
        else:
            flash('Testimonial avatar not found.', 'warning')
    elif asset_type == 'billing':
        pm = db.session.get(PaymentMethod, asset_id)
        if pm and pm.qr_image:
            _rm('billing', pm.qr_image)
            pm.qr_image = ''
            db.session.commit()
            flash(f'QR code deleted for payment method "{pm.name}".', 'success')
        else:
            flash('Payment QR image not found.', 'warning')
    elif asset_type == 'proof':
        sub = db.session.get(PaymentSubmission, asset_id)
        if sub and sub.payment_proof:
            _rm('billing', sub.payment_proof)
            sub.payment_proof = ''
            db.session.commit()
            flash(f'Payment proof deleted for submission #{sub.id} ({sub.tenant.slug if sub.tenant else "?"}).', 'success')
        else:
            flash('Payment proof not found.', 'warning')
    else:
        flash('Unknown asset type.', 'danger')

    return redirect(url_for('superadmin.media',
                            asset_type=request.form.get('asset_type', 'all'),
                            tenant=request.form.get('tenant_filter', 'all')))

@superadmin.route('/media/compress', methods=['POST'])
@superadmin_required
def media_compress():
    """Re-compress a JPEG/PNG in-place to reduce file size."""
    import os
    from pathlib import Path
    from PIL import Image as PilImage

    folder   = request.form.get('folder', '')
    filename = request.form.get('filename', '')

    # ── Security: folder allowlist + filename traversal guard ─────────────────
    ALLOWED_COMPRESS_FOLDERS = {'profiles', 'projects', 'billing'}

    if (
        not filename
        or folder not in ALLOWED_COMPRESS_FOLDERS
        or '/' in filename
        or '..' in filename
    ):
        flash('Invalid compress request.', 'danger')
        return redirect(url_for('superadmin.media'))

    upload_root = Path(current_app.static_folder) / 'uploads'
    candidate   = (upload_root / folder / filename).resolve()

    # Containment check: resolved path must remain inside upload_root
    try:
        candidate.relative_to(upload_root.resolve())
    except ValueError:
        flash('Path traversal detected — request rejected.', 'danger')
        return redirect(url_for('superadmin.media'))

    path = str(candidate)
    if not os.path.exists(path):
        flash('File not found on disk.', 'warning')
        return redirect(url_for('superadmin.media'))

    try:
        before = os.path.getsize(path)
        img = PilImage.open(path).convert('RGB')
        max_side = 1200
        w, h = img.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), PilImage.LANCZOS)
        img.save(path, 'JPEG', quality=80, optimize=True)
        after = os.path.getsize(path)
        saved = before - after
        pct   = (saved / before * 100) if before else 0
        flash(
            f'Compressed "{filename}": {_sa_format_filesize(before)} → '
            f'{_sa_format_filesize(after)} (saved {_sa_format_filesize(saved)}, {pct:.0f}%).',
            'success',
        )
    except Exception as exc:
        flash(f'Compression failed: {exc}', 'danger')

    return redirect(url_for('superadmin.media',
                            asset_type=request.form.get('asset_type', 'all'),
                            tenant=request.form.get('tenant_filter', 'all')))

@superadmin.route('/media/delete-orphans', methods=['POST'])
@superadmin_required
@limiter.limit('10 per minute')
def media_delete_orphans():
    """Delete files in upload folders that have no matching DB record."""
    import os
    from app.models.portfolio import Testimonial

    upload_root = os.path.join(current_app.static_folder, 'uploads')
    known = {'profiles': set(), 'projects': set(), 'billing': set()}

    for p in profile_repository.query.all():
        if p.profile_image:
            known['profiles'].add(p.profile_image)
    for t in testimonial_repository.query.all():
        if t.author_avatar:
            known['profiles'].add(t.author_avatar)
    for proj in project_repository.query.all():
        if proj.image:
            known['projects'].add(proj.image)
    for pm in payment_method_repository.query.all():
        if pm.qr_image:
            known['billing'].add(pm.qr_image)

    deleted = errors = 0
    for folder, known_files in known.items():
        folder_path = os.path.join(upload_root, folder)
        if not os.path.isdir(folder_path):
            continue
        for fname in os.listdir(folder_path):
            if fname not in known_files:
                try:
                    os.remove(os.path.join(folder_path, fname))
                    deleted += 1
                except OSError:
                    errors += 1

    if errors:
        flash(f'Deleted {deleted} orphan file(s). {errors} could not be removed.', 'warning')
    else:
        flash(f'Deleted {deleted} orphan file(s) with no database records.', 'success')

    return redirect(url_for('superadmin.media'))
