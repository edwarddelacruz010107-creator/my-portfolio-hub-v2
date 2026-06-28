"""
routes/form_settings.py — Tenant admin + superadmin form provider routes (v4.2)

Routes (Tenant Admin):
    GET/POST  /admin/settings/forms        → tenant views & edits their own config
    POST      /admin/settings/forms/test   → live test submission

Routes (Superadmin):
    GET  /superadmin/forms                 → list all tenants + provider status
    GET  /superadmin/forms/<tenant_id>     → masked view of one tenant's config

Security:
    - Tenant admin route: scoped to current_user.tenant_id (login_required)
    - Superadmin route: uses the authoritative superadmin_required from
      app.superadmin — enforces TOTP gate, not just role check.
    - API keys NEVER returned to frontend (masked or omitted)
    - CSRF on all POST routes (Flask-WTF)
    - Rate limiting on /test endpoint (Flask-Limiter: 5/minute)

NEW-03 FIX:
    Removed the inline superadmin_required definition that bypassed TOTP.
    Now imports the authoritative decorator from app.superadmin which
    correctly redirects unauthenticated users through the TOTP login flow.

NEW-02 FIX:
    All imports now use app.* paths matching the actual package layout.
    Previously these referenced root-level modules/ that did not exist under app/.
"""
from __future__ import annotations

import logging

from flask import (
    Blueprint, abort, flash, jsonify, redirect,
    render_template, request, url_for,
)
from flask_login import current_user, login_required

from app import db, limiter
from app.models.tenant_form_settings import TenantFormSettings
from app.models.portfolio import Tenant
from app.services.forms import test_provider, validate_provider
from app.forms.tenant_forms import TenantFormSettingsForm
# NEW-03 FIX: import the authoritative decorator — enforces TOTP gate
from app.superadmin import superadmin_required

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tenant Admin Blueprint  →  /admin/settings/forms
# ══════════════════════════════════════════════════════════════════════════════

admin_forms = Blueprint('admin_forms', __name__, url_prefix='/admin/settings')


@admin_forms.route('/forms', methods=['GET', 'POST'])
@login_required
def form_settings():
    """
    Tenant admin: view and save their own form provider config.
    Scoped to current_user.tenant_id — cannot access other tenants.
    """
    if not current_user.is_admin:
        abort(403)
    tenant_id = current_user.tenant_id
    settings  = TenantFormSettings.get_or_create(tenant_id)
    form      = TenantFormSettingsForm(obj=settings)

    # Pre-populate provider from existing record on GET
    if request.method == 'GET':
        form.provider.data       = settings.provider
        form.is_enabled.data     = settings.is_enabled
        form.receiver_email.data = settings.receiver_email or ''
        form.sender_name.data    = settings.sender_name or ''
        form.form_endpoint.data  = settings.form_endpoint or ''
        # api_key field intentionally left blank (never pre-fill secrets)

    if form.validate_on_submit():
        settings.provider       = form.provider.data
        settings.is_enabled     = form.is_enabled.data
        settings.receiver_email = form.receiver_email.data or None
        settings.sender_name    = form.sender_name.data or None

        # Only update endpoint if provider is Basin
        if form.provider.data == 'basin':
            settings.form_endpoint = form.form_endpoint.data or None
        else:
            settings.form_endpoint = None

        # API key: only update if a new value was provided (basin doesn't use one)
        new_key = (form.api_key.data or '').strip()
        if new_key:
            try:
                settings.api_key = new_key   # triggers Fernet encryption; raises on failure
            except RuntimeError as exc:
                logger.error('form_settings: encryption failed for tenant=%s: %s', tenant_id, exc)
                flash(
                    'Could not save API key — encryption service is unavailable. '
                    'Please contact your platform administrator.',
                    'danger',
                )
                return render_template(
                    'admin/settings/form_settings.html',
                    form=form,
                    settings=settings,
                )

        db.session.commit()
        logger.info('admin_forms: tenant=%s saved provider=%s', tenant_id, settings.provider)
        flash('Form provider settings saved.', 'success')
        return redirect(url_for('admin_forms.form_settings'))

    valid, validation_msg = validate_provider(settings)

    return render_template(
        'admin/settings/form_settings.html',
        form=form,
        settings=settings,
        validation_ok=valid,
        validation_msg=validation_msg,
    )


@admin_forms.route('/forms/test', methods=['POST'])
@login_required
@limiter.limit('5 per minute')
def test_form_provider():
    """
    Trigger a live test submission through the tenant's configured provider.
    Returns JSON for AJAX use.
    Rate-limited to 5 requests/minute to prevent Basin charge abuse.
    """
    if not current_user.is_admin:
        return jsonify({'ok': False, 'message': 'Forbidden.'}), 403
    tenant_id = current_user.tenant_id
    settings  = TenantFormSettings.for_tenant(tenant_id)

    if not settings:
        return jsonify({'ok': False, 'message': 'No form provider configured yet.'})

    success, error = test_provider(settings)
    if success:
        logger.info('admin_forms: test OK tenant=%s provider=%s', tenant_id, settings.provider)
        return jsonify({'ok': True, 'message': 'Test email sent successfully!'})

    if error == 'INTERNAL_FALLBACK':
        return jsonify({'ok': False, 'message': 'Provider is disabled.'})

    logger.warning('admin_forms: test FAILED tenant=%s: %s', tenant_id, error)
    return jsonify({'ok': False, 'message': error or 'Test failed.'})


# ══════════════════════════════════════════════════════════════════════════════
# Superadmin Blueprint  →  /superadmin/forms
# ══════════════════════════════════════════════════════════════════════════════

superadmin_forms = Blueprint('superadmin_forms', __name__, url_prefix='/superadmin')


@superadmin_forms.route('/forms')
@login_required
@superadmin_required   # NEW-03 FIX: authoritative decorator — enforces TOTP
def forms_overview():
    """
    Superadmin: view all tenants' form provider status.
    API keys shown ONLY as masked (***abcd). Never exposed raw.
    """
    tenants = Tenant.query.order_by(Tenant.company_name).all()

    rows = []
    for tenant in tenants:
        fs = TenantFormSettings.for_tenant(tenant.id)
        rows.append({
            'tenant':     tenant,
            'provider':   fs.provider    if fs else 'disabled',
            'status':     fs.status_label if fs else 'disabled',
            'masked_key': fs.api_key_masked if fs else '',
            'is_enabled': fs.is_enabled  if fs else False,
            'configured': fs.is_configured if fs else False,
        })

    return render_template(
        'superadmin/forms_overview.html',
        rows=rows,
        total=len(rows),
        connected=sum(1 for r in rows if r['status'] == 'connected'),
        needs_setup=sum(1 for r in rows if r['status'] == 'needs_setup'),
        disabled=sum(1 for r in rows if r['status'] == 'disabled'),
    )


@superadmin_forms.route('/forms/<int:tenant_id>')
@login_required
@superadmin_required   # NEW-03 FIX: authoritative decorator — enforces TOTP
def forms_tenant_detail(tenant_id: int):
    """
    Superadmin: view one tenant's form config (masked API key).
    """
    tenant   = Tenant.query.get_or_404(tenant_id)
    settings = TenantFormSettings.for_tenant(tenant_id)

    return render_template(
        'superadmin/forms_tenant_detail.html',
        tenant=tenant,
        settings=settings,
        masked_key=(settings.api_key_masked if settings else ''),
    )