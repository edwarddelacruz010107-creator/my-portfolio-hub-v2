"""
app/superadmin/themes.py — SuperAdmin Theme Catalog Management (v6.5)

Routes on the existing `superadmin` blueprint:

  GET            /superadmin/themes                       — catalog list
  POST           /superadmin/themes/sync                  — sync filesystem
  GET/POST       /superadmin/themes/new                   — create entry (no fs theme required)
  GET/POST       /superadmin/themes/<id>/edit             — full editor
  POST           /superadmin/themes/<id>/toggle-active    — activate / deactivate
  POST           /superadmin/themes/<id>/toggle-featured  — featured spotlight
  POST           /superadmin/themes/<id>/upload-thumbnail — image upload
  POST           /superadmin/themes/<id>/upload-preview   — add screenshot
  POST           /superadmin/themes/<id>/delete-preview   — remove screenshot
  POST           /superadmin/themes/<id>/delete           — remove catalog row
  GET            /superadmin/themes/analytics             — install analytics JSON

No existing routes, models, or the theme engine are modified beyond adding
catalog rows and reading the new extended columns added by migration 0035.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    abort, current_app, flash, jsonify, redirect,
    render_template, request, url_for,
)
from werkzeug.utils import secure_filename

from app import db
from app.superadmin import superadmin, superadmin_required
from app.theme_engine import get_theme_engine
from app.models.core import ThemeCatalogEntry, VALID_REQUIRED_PLANS
from app.utils import log_activity

logger = logging.getLogger(__name__)

# ── Image upload config ───────────────────────────────────────────────────────

_ALLOWED_EXTS   = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
_MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
_MAX_PREVIEWS   = 6                  # max screenshots per theme


def _allowed_image(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in _ALLOWED_EXTS


def _theme_upload_dir() -> Path:
    """Return (and create) the upload directory for theme images."""
    base = Path(current_app.static_folder) / 'uploads' / 'themes'
    base.mkdir(parents=True, exist_ok=True)
    return base


def _save_uploaded_image(file_storage, prefix: str) -> str | None:
    """Validate, sanitize, and save an uploaded image. Returns public URL or None."""
    if not file_storage or not file_storage.filename:
        return None
    if not _allowed_image(file_storage.filename):
        return None
    # Read into memory to check size before writing
    data = file_storage.read()
    if len(data) > _MAX_IMAGE_SIZE:
        return None
    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    fname = f'{prefix}_{uuid.uuid4().hex[:12]}.{ext}'
    dest = _theme_upload_dir() / fname
    dest.write_bytes(data)
    return url_for('static', filename=f'uploads/themes/{fname}', _external=False)


# ── Theme list ────────────────────────────────────────────────────────────────

@superadmin.route('/themes')
@superadmin_required
def theme_catalog():
    engine = get_theme_engine()
    themes = engine.get_all_themes(include_inactive=True)
    # Sort: featured first, then sort_order, then name
    themes.sort(key=lambda t: (
        not t.get('is_featured', False),
        t.get('sort_order', 0),
        (t.get('name') or t.get('id') or '').lower(),
    ))
    # Analytics summary
    total_installs = sum(t.get('install_count', 0) for t in themes)
    most_popular = max(themes, key=lambda t: t.get('install_count', 0), default=None)
    return render_template(
        'superadmin/themes.html',
        themes=themes,
        page_title='Theme Catalog',
        total_installs=total_installs,
        most_popular=most_popular,
    )


# ── Sync from disk ────────────────────────────────────────────────────────────

@superadmin.route('/themes/sync', methods=['POST'])
@superadmin_required
def theme_catalog_sync():
    from sqlalchemy.exc import OperationalError

    engine = get_theme_engine()

    def _run_sync():
        """Returns count of newly created entries. Raises OperationalError
        unchanged if the schema is still out of sync after a repair attempt."""
        created = 0
        for meta in engine.registry.all(include_inactive=True):
            slug = meta.get('id')
            if not slug or ThemeCatalogEntry.get_by_slug(slug):
                continue
            entry = ThemeCatalogEntry(
                slug=slug,
                name=meta.get('name') or slug,
                description=meta.get('description') or '',
                category=(meta.get('tags') or [None])[0],
                is_active=True,
                is_premium=bool(meta.get('premium', False)),
                required_plan=None,
                sort_order=0,
            )
            db.session.add(entry)
            created += 1
        return created

    try:
        created = _run_sync()
    except OperationalError as exc:
        # Schema mismatch (e.g. migration 0035 not yet applied) — this is the
        # exact failure this route used to crash on. Roll back the half-built
        # session, self-repair the schema once, and retry a single time.
        db.session.rollback()
        logger.warning('Theme catalog sync hit a schema mismatch, attempting self-repair: %s', exc)
        try:
            from app import _ensure_theme_catalog_columns
            _ensure_theme_catalog_columns()
            created = _run_sync()
            flash('Database schema mismatch detected and repaired.', 'warning')
        except Exception:
            logger.exception('Theme catalog sync: self-repair failed, schema mismatch persists')
            flash('Theme system requires administrator migration review.', 'danger')
            return redirect(url_for('superadmin.theme_catalog'))
    except Exception as exc:
        db.session.rollback()
        logger.exception('Theme catalog sync failed: %s', exc)
        flash('Database error while syncing themes.', 'danger')
        return redirect(url_for('superadmin.theme_catalog'))

    if created:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception('Theme catalog sync failed: %s', exc)
            flash('Database error while syncing themes.', 'danger')
            return redirect(url_for('superadmin.theme_catalog'))
        engine.clear_cache()
        log_activity('create', 'theme_catalog', 'sync', f'Synced {created} theme(s) into the catalog')
        flash(f'Registered {created} new theme(s).', 'success')
    else:
        flash('Theme catalog already up to date.', 'info')
    return redirect(url_for('superadmin.theme_catalog'))


# ── Analytics JSON ────────────────────────────────────────────────────────────

@superadmin.route('/themes/analytics')
@superadmin_required
def theme_analytics():
    entries = ThemeCatalogEntry.query.order_by(ThemeCatalogEntry.install_count.desc()).all()
    data = [
        {
            'slug': e.slug,
            'name': e.name or e.slug,
            'install_count': e.install_count or 0,
            'is_featured': bool(e.is_featured),
            'is_active': bool(e.is_active),
        }
        for e in entries
    ]
    return jsonify({'ok': True, 'themes': data})


# ── Full editor (GET/POST) ────────────────────────────────────────────────────

def _get_entry_or_404(entry_id):
    entry = db.session.get(ThemeCatalogEntry, entry_id)
    if entry is None:
        abort(404)
    return entry


@superadmin.route('/themes/<int:entry_id>/edit', methods=['GET', 'POST'])
@superadmin_required
def theme_catalog_edit(entry_id):
    entry = _get_entry_or_404(entry_id)
    engine = get_theme_engine()
    fs_meta = engine.get_theme_meta(entry.slug) or {}

    if request.method == 'POST':
        form = request.form
        # ── Basic fields ──────────────────────────────────────────────────
        name          = (form.get('name') or '').strip()
        description   = (form.get('description') or '').strip()
        category      = (form.get('category') or '').strip()
        theme_author  = (form.get('theme_author') or '').strip()
        theme_version = (form.get('theme_version') or '').strip()
        required_plan = (form.get('required_plan') or '').strip().lower()
        is_premium_raw = form.get('is_premium', 'defer')
        is_active     = form.get('is_active') == 'on'
        is_featured   = form.get('is_featured') == 'on'
        thumbnail_url = (form.get('thumbnail_url') or '').strip()
        banner_url    = (form.get('banner_url') or '').strip()
        try:
            sort_order = int(form.get('sort_order') or 0)
        except (TypeError, ValueError):
            sort_order = 0

        # Tags: comma-separated
        raw_tags = (form.get('theme_tags') or '').strip()
        tags = [t.strip() for t in raw_tags.split(',') if t.strip()]

        # Feature matrix: one checkbox per feature key
        _FEATURES = ['hero', 'about', 'projects', 'skills', 'services',
                     'testimonials', 'timeline', 'resume', 'contact',
                     'gallery', 'blog', 'shop']
        matrix = {f: (form.get(f'feature_{f}') == 'on') for f in _FEATURES}

        if required_plan and required_plan not in VALID_REQUIRED_PLANS:
            flash('Invalid required plan selection.', 'danger')
        else:
            entry.name          = name or None
            entry.description   = description or None
            entry.category      = category or None
            entry.theme_author  = theme_author or None
            entry.theme_version = theme_version or None
            entry.required_plan = required_plan or None
            entry.is_active     = is_active
            entry.is_featured   = is_featured
            entry.sort_order    = sort_order
            if thumbnail_url:
                entry.thumbnail_url = thumbnail_url
            if banner_url:
                entry.banner_url = banner_url
            entry.set_tags(tags)
            entry.set_feature_matrix(matrix)
            if is_premium_raw == 'defer':
                entry.is_premium = None
            else:
                entry.is_premium = (is_premium_raw == 'true')

            try:
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                logger.exception('Failed to update theme catalog entry %s: %s', entry.slug, exc)
                flash('Database error while saving. Please try again.', 'danger')
                return render_template(
                    'superadmin/theme_form.html', entry=entry, fs_meta=fs_meta,
                    page_title=f'Edit Theme — {entry.slug}',
                )
            engine.clear_cache()
            log_activity('update', 'theme_catalog', entry.slug, f'Updated theme catalog entry "{entry.slug}"')
            flash(f'Theme "{entry.slug}" updated successfully.', 'success')
            return redirect(url_for('superadmin.theme_catalog'))

    return render_template(
        'superadmin/theme_form.html', entry=entry, fs_meta=fs_meta,
        page_title=f'Edit Theme — {entry.slug}',
    )


# ── Image upload endpoints ────────────────────────────────────────────────────

@superadmin.route('/themes/<int:entry_id>/upload-thumbnail', methods=['POST'])
@superadmin_required
def theme_upload_thumbnail(entry_id):
    entry = _get_entry_or_404(entry_id)
    f = request.files.get('thumbnail')
    if not f:
        return jsonify({'ok': False, 'message': 'No file uploaded.'})
    if not _allowed_image(f.filename):
        return jsonify({'ok': False, 'message': 'Allowed types: PNG, JPG, JPEG, WEBP, GIF'})
    url = _save_uploaded_image(f, f'thumb_{entry.slug}')
    if not url:
        return jsonify({'ok': False, 'message': 'Upload failed — file too large or invalid.'})
    entry.thumbnail_url = url
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to save thumbnail for %s: %s', entry.slug, exc)
        return jsonify({'ok': False, 'message': 'Database error saving thumbnail.'})
    get_theme_engine().clear_cache()
    return jsonify({'ok': True, 'url': url})


@superadmin.route('/themes/<int:entry_id>/upload-preview', methods=['POST'])
@superadmin_required
def theme_upload_preview(entry_id):
    entry = _get_entry_or_404(entry_id)
    existing = entry.get_preview_images()
    if len(existing) >= _MAX_PREVIEWS:
        return jsonify({'ok': False, 'message': f'Maximum {_MAX_PREVIEWS} preview images allowed.'})
    f = request.files.get('preview')
    if not f:
        return jsonify({'ok': False, 'message': 'No file uploaded.'})
    if not _allowed_image(f.filename):
        return jsonify({'ok': False, 'message': 'Allowed types: PNG, JPG, JPEG, WEBP, GIF'})
    url = _save_uploaded_image(f, f'prev_{entry.slug}')
    if not url:
        return jsonify({'ok': False, 'message': 'Upload failed — file too large or invalid.'})
    existing.append(url)
    entry.set_preview_images(existing)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to save preview for %s: %s', entry.slug, exc)
        return jsonify({'ok': False, 'message': 'Database error saving preview image.'})
    get_theme_engine().clear_cache()
    return jsonify({'ok': True, 'url': url, 'previews': existing})


@superadmin.route('/themes/<int:entry_id>/delete-preview', methods=['POST'])
@superadmin_required
def theme_delete_preview(entry_id):
    entry = _get_entry_or_404(entry_id)
    url_to_remove = (request.form.get('url') or '').strip()
    existing = [u for u in entry.get_preview_images() if u != url_to_remove]
    entry.set_preview_images(existing)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({'ok': False, 'message': 'Database error.'})
    get_theme_engine().clear_cache()
    return jsonify({'ok': True, 'previews': existing})


# ── Toggle active ─────────────────────────────────────────────────────────────

@superadmin.route('/themes/<int:entry_id>/toggle-active', methods=['POST'])
@superadmin_required
def theme_catalog_toggle_active(entry_id):
    entry = _get_entry_or_404(entry_id)
    entry.is_active = not entry.is_active
    status = 'activated' if entry.is_active else 'deactivated'
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to toggle theme %s: %s', entry.slug, exc)
        flash('Unable to update theme status.', 'danger')
        return redirect(url_for('superadmin.theme_catalog'))
    get_theme_engine().clear_cache()
    log_activity('update', 'theme_catalog', entry.slug, f'Theme "{entry.slug}" {status}')
    flash(f'Theme "{entry.slug}" has been {status}.', 'success')
    return redirect(url_for('superadmin.theme_catalog'))


# ── Toggle featured ───────────────────────────────────────────────────────────

@superadmin.route('/themes/<int:entry_id>/toggle-featured', methods=['POST'])
@superadmin_required
def theme_catalog_toggle_featured(entry_id):
    entry = _get_entry_or_404(entry_id)
    try:
        entry.is_featured = not entry.is_featured
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({'ok': False, 'message': str(exc)[:100]})
    get_theme_engine().clear_cache()
    return jsonify({'ok': True, 'is_featured': entry.is_featured})


# ── Delete catalog row ────────────────────────────────────────────────────────

@superadmin.route('/themes/<int:entry_id>/delete', methods=['POST'])
@superadmin_required
def theme_catalog_delete(entry_id):
    entry = _get_entry_or_404(entry_id)
    slug = entry.slug
    db.session.delete(entry)
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('Failed to delete theme catalog entry %s: %s', slug, exc)
        flash('Unable to delete catalog override.', 'danger')
        return redirect(url_for('superadmin.theme_catalog'))
    get_theme_engine().clear_cache()
    log_activity('delete', 'theme_catalog', slug,
                 f'Removed catalog override for theme "{slug}" (reverted to theme.json defaults)')
    flash(f'Catalog override removed for "{slug}" — it now uses its theme.json defaults.', 'success')
    return redirect(url_for('superadmin.theme_catalog'))