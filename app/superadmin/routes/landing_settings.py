"""
app/superadmin/routes/landing_settings.py — Superadmin landing page content editor.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from flask import render_template, redirect, url_for, flash, request, abort, jsonify, current_app
from flask_wtf.csrf import validate_csrf
from werkzeug.datastructures import MultiDict

from app import db, limiter
from app.forms import LandingContactForm, LandingPageSettingsForm
from app.models.core import PlatformSetting
from app.public.services.creator_service import get_featured_creators
from app.public.services.feed_service import get_trending_projects
from app.public.services.landing_service import get_landing_stats, get_administrator_card, get_community_stats
from app.superadmin.blueprint import superadmin, superadmin_required
from app.utils import log_activity, BILLING_PLANS, is_paymongo_enabled

logger = logging.getLogger(__name__)

_PUBLISHED_LANDING_KEYS = {
    'hero_badge': 'landing_hero_badge',
    'hero_title': 'landing_hero_title',
    'hero_subtitle': 'landing_hero_subtitle',
    'hero_cta_primary_text': 'landing_hero_cta_primary_text',
    'hero_cta_primary_url': 'landing_hero_cta_primary_url',
    'hero_cta_secondary_text': 'landing_hero_cta_secondary_text',
    'hero_cta_secondary_url': 'landing_hero_cta_secondary_url',
    'hero_image_url': 'landing_hero_image_url',
    'hero_preview_name': 'landing_hero_preview_name',
    'hero_preview_role': 'landing_hero_preview_role',
    'hero_preview_url_text': 'landing_hero_preview_url_text',
    'hero_stat_badge_text': 'landing_hero_stat_badge_text',
    'hero_stat_likes': 'landing_hero_stat_likes',
    'hero_stat_views': 'landing_hero_stat_views',
    'hero_stat_comments': 'landing_hero_stat_comments',
    'features_heading': 'landing_features_heading',
    'features_subtitle': 'landing_features_subtitle',
    'contact_heading': 'landing_contact_heading',
    'contact_subtitle': 'landing_contact_subtitle',
    'contact_receiver_email': 'landing_contact_receiver_email',
    'contact_email': 'landing_contact_email',
    'contact_phone': 'landing_contact_phone',
    'contact_location': 'landing_contact_location',
    'contact_map_title': 'landing_contact_map_title',
    'contact_map_note': 'landing_contact_map_note',
    'contact_x_url': 'landing_contact_x_url',
    'contact_linkedin_url': 'landing_contact_linkedin_url',
    'contact_instagram_url': 'landing_contact_instagram_url',
    'contact_github_url': 'landing_contact_github_url',
    'founder_photo_url': 'landing_founder_photo_url',
    'founder_photo_fit': 'landing_founder_photo_fit',
    'founder_photo_position_x': 'landing_founder_photo_position_x',
    'founder_photo_position_y': 'landing_founder_photo_position_y',
    'founder_photo_zoom': 'landing_founder_photo_zoom',
    'founder_role': 'landing_founder_role',
    'founder_title': 'landing_founder_title',
    'founder_name': 'landing_founder_name',
    'founder_description': 'landing_founder_description',
    'founder_portfolio_url': 'landing_founder_portfolio_url',
    'founder_contact_url': 'landing_founder_contact_url',
    'founder_preview_image': 'landing_founder_preview_image',
}

_IMAGE_UPLOAD_KEYS = {
    'hero': 'landing_hero_image_url',
    'founder': 'landing_founder_photo_url',
    'founder_preview': 'landing_founder_preview_image',
}

_PUBLISHED_LANDING_BOOL_KEYS = {
    'hero_enable_widgets': 'landing_hero_enable_widgets',
    'hero_enable_animation': 'landing_hero_enable_animation',
}

_ALLOWED_IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
_MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

# Autosave allow-list: only these field names may be written by the
# per-field autosave endpoint. Prevents mass-assignment onto arbitrary
# PlatformSetting keys from a crafted JSON body.
_AUTOSAVE_FIELDS = set(_PUBLISHED_LANDING_KEYS) | set(_PUBLISHED_LANDING_BOOL_KEYS)


def _draft_key(published_key: str) -> str:
    return f'{published_key}_draft'


def _load_landing_settings_internal(draft_first: bool = False, fallback_to_published: bool = False) -> dict[str, str]:
    values = {}
    for field, published_key in _PUBLISHED_LANDING_KEYS.items():
        if draft_first:
            draft_value = PlatformSetting.get_string(_draft_key(published_key), default='') or ''
            if draft_value:
                values[field] = draft_value
                continue
            if fallback_to_published:
                values[field] = PlatformSetting.get_string(published_key, default='') or ''
            else:
                values[field] = draft_value
        else:
            values[field] = PlatformSetting.get_string(published_key, default='') or ''

    for field, published_key in _PUBLISHED_LANDING_BOOL_KEYS.items():
        if draft_first:
            draft_value = PlatformSetting.get_bool(_draft_key(published_key), default=None)
            if draft_value is not None:
                values[field] = draft_value
                continue
            if fallback_to_published:
                values[field] = PlatformSetting.get_bool(published_key, default=True)
            else:
                values[field] = True
        else:
            values[field] = PlatformSetting.get_bool(published_key, default=True)

    founder_crop_defaults = {
        'founder_photo_fit': 'cover',
        'founder_photo_position_x': '50',
        'founder_photo_position_y': '50',
        'founder_photo_zoom': '100',
    }
    for field, default_value in founder_crop_defaults.items():
        if values.get(field) in (None, ''):
            values[field] = default_value
    return values


def _save_landing_settings(form, publish: bool = False) -> None:
    for field, published_key in _PUBLISHED_LANDING_KEYS.items():
        value = getattr(form, field).data or ''
        PlatformSetting.set_string(_draft_key(published_key), value)
        if publish:
            PlatformSetting.set_string(published_key, value)

    for field, published_key in _PUBLISHED_LANDING_BOOL_KEYS.items():
        value = bool(getattr(form, field).data)
        PlatformSetting.set_bool(_draft_key(published_key), value)
        if publish:
            PlatformSetting.set_bool(published_key, value)


@superadmin.route('/settings/landing/autosave', methods=['POST'])
@superadmin_required
@limiter.limit('40 per minute')
def landing_autosave():
    """Per-field draft autosave for the Landing CMS form.

    Scope is deliberately narrow: exactly one field's draft key is
    written per call, and only the draft key — never the published/live
    key. This is NOT a wrapper around `_save_landing_settings`: calling
    that on a request carrying just one changed field would blank out
    every sibling field's draft with an empty string. Each call here
    binds a single named field into a throwaway form instance, runs only
    that field's own validators, and — if valid — writes only that
    field's draft PlatformSetting row.
    """
    payload = request.get_json(silent=True) or {}
    field = (payload.get('field') or '').strip()
    raw_value = payload.get('value', '')

    try:
        validate_csrf(payload.get('csrf_token') or request.headers.get('X-CSRFToken') or '')
    except Exception:
        return jsonify({'success': False, 'error': 'Your session expired. Refresh the page and try again.'}), 400

    if field not in _AUTOSAVE_FIELDS:
        return jsonify({'success': False, 'error': 'Unknown field.'}), 400

    is_bool_field = field in _PUBLISHED_LANDING_BOOL_KEYS
    formdata = MultiDict({field: ('y' if raw_value else '') if is_bool_field else (raw_value or '')})
    field_form = LandingPageSettingsForm(formdata=formdata, meta={'csrf': False})
    bound_field = getattr(field_form, field)

    if not bound_field.validate(field_form):
        error = bound_field.errors[0] if bound_field.errors else 'Invalid value.'
        return jsonify({'success': False, 'field': field, 'error': error}), 422

    published_key = _PUBLISHED_LANDING_KEYS.get(field) or _PUBLISHED_LANDING_BOOL_KEYS[field]
    try:
        if is_bool_field:
            PlatformSetting.set_bool(_draft_key(published_key), bool(bound_field.data))
        else:
            PlatformSetting.set_string(_draft_key(published_key), bound_field.data or '')
        db.session.commit()
    except Exception as exc:
        logger.exception('Autosave failed for landing field %s: %s', field, exc)
        db.session.rollback()
        return jsonify({'success': False, 'field': field, 'error': 'Could not save. Try again.'}), 500

    return jsonify({
        'success': True,
        'field': field,
        'saved_at': datetime.now(timezone.utc).isoformat(),
    }), 200


def _landing_upload_dir() -> Path:
    from app.services.media.upload_storage import ensure_upload_folder
    return ensure_upload_folder('landing')


def _allowed_image(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in _ALLOWED_IMAGE_EXTS


@superadmin.route('/settings/landing/upload-image', methods=['POST'])
@superadmin_required
@limiter.limit('20 per minute')
def landing_upload_image():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded.'}), 400

    f = request.files['file']
    if f.filename == '':
        return jsonify({'success': False, 'error': 'Empty filename.'}), 400

    category = (request.form.get('category') or '').strip().lower()
    if category not in _IMAGE_UPLOAD_KEYS:
        return jsonify({'success': False, 'error': 'Invalid upload category.'}), 400

    if not _allowed_image(f.filename):
        return jsonify({'success': False, 'error': 'Allowed file types: PNG, JPG, JPEG, WEBP, GIF.'}), 422

    file_bytes = f.read()
    if not file_bytes:
        return jsonify({'success': False, 'error': 'Empty file.'}), 422
    if len(file_bytes) > _MAX_IMAGE_SIZE:
        return jsonify({'success': False, 'error': 'Image too large. Max 5 MB.'}), 422
    from app.security import FileUploadPolicy
    ok, err = FileUploadPolicy.validate_image_upload(
        f.filename, len(file_bytes), file_bytes=file_bytes, declared_mime=getattr(f, 'mimetype', None)
    )
    if not ok:
        return jsonify({'success': False, 'error': err}), 422

    # Rewind after validation, then use the unified image-storage provider.
    # This makes landing images survive redeploys on Cloudinary/Supabase and
    # applies the same WebP optimization used by portfolio media.
    try:
        f.stream.seek(0)
    except Exception:
        pass
    from app.utils import save_image
    url, upload_error = save_image(f, 'landing')
    if not url:
        return jsonify({'success': False, 'error': upload_error or 'Unable to save uploaded file.'}), 500
    PlatformSetting.set_string(_draft_key(_IMAGE_UPLOAD_KEYS[category]), url)
    db.session.commit()
    log_activity('update', 'landing_page', 'landing_image', f'Uploaded {category} image')
    return jsonify({'success': True, 'url': url}), 201


@superadmin.route('/settings/landing/preview')
@superadmin_required
def landing_settings_preview():
    try:
        featured_creators = get_featured_creators(limit=6)
        trending_projects = get_trending_projects(limit=6)
        stats = get_landing_stats()
        community = get_community_stats()
        administrator = get_administrator_card()
        landing_content = _load_landing_settings(draft_first=True, fallback_to_published=True)
        form = LandingContactForm()
        return render_template(
            'public/index.html',
            featured_creators=featured_creators,
            trending_projects=trending_projects,
            plans=BILLING_PLANS,
            paymongo_enabled=is_paymongo_enabled(),
            stats=stats,
            community=community,
            administrator=administrator,
            landing_content=landing_content,
            contact_form=form,
            preview_mode=True,
        )
    except Exception as exc:
        logger.exception('Landing preview failed: %s', exc)
        flash('Unable to load the landing preview right now.', 'danger')
        return redirect(url_for('superadmin.landing_settings'))


def _load_landing_settings(
    draft_first: bool = True,
    fallback_to_published: bool = True,
) -> dict[str, str]:
    return _load_landing_settings_internal(
        draft_first=draft_first,
        fallback_to_published=fallback_to_published,
    )


@superadmin.route('/settings/landing', methods=['GET', 'POST'])
@superadmin_required
def landing_settings():
    form = LandingPageSettingsForm()

    if request.method == 'GET':
        values = _load_landing_settings(draft_first=True, fallback_to_published=True)
        for field, value in values.items():
            setattr(getattr(form, field), 'data', value)

    if form.validate_on_submit():
        action = (request.form.get('action') or 'save').strip().lower()
        publish = action == 'publish'
        try:
            _save_landing_settings(form, publish=publish)
            db.session.commit()
            if publish:
                log_activity('publish', 'landing_page', 'landing_content', 'Published landing page content')
                flash('Landing page content published successfully.', 'success')
            else:
                log_activity('update', 'landing_page', 'landing_content', 'Saved landing page draft')
                flash('Landing page draft saved successfully.', 'success')
            return redirect(url_for('superadmin.landing_settings'))
        except Exception as exc:
            logger.exception('Failed to save landing page settings: %s', exc)
            db.session.rollback()
            flash('Failed to update landing page content. Please try again.', 'danger')

    return render_template(
        'superadmin/landing_settings.html',
        form=form,
        page_title='Landing Page Content',
    )
