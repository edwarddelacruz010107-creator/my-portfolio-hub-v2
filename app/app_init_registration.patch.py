"""
CRIT-05 FIX — app/__init__.py blueprint registration patch

The `superadmin_forms` blueprint is defined in `routes/form_settings.py`
but was never imported or registered. Templates call url_for('superadmin_forms.forms_overview')
and url_for('superadmin_forms.forms_tenant_detail') which raise BuildError at render time.

APPLY: Find the blueprint registration block in app/__init__.py (around the
"Blueprints" section) and add the lines marked with # CRIT-05 ADD below.

IMPORTANT: Register `superadmin_forms` BEFORE `tenant_bp` (the catch-all).
It should go alongside the `superadmin_blueprint` registration.
"""

# ── DIFF to apply in app/__init__.py ─────────────────────────────────────────
#
# BEFORE (around line 215 in create_app):
#
#     from app.auth       import auth       as auth_blueprint
#     from app.admin      import admin      as admin_blueprint
#     from app.superadmin import superadmin as superadmin_blueprint
#     from app.main       import main       as main_blueprint
#     from app.tenant     import tenant_bp
#     from app.webhooks   import webhooks   as webhooks_blueprint
#
#     app.register_blueprint(auth_blueprint,       url_prefix='/auth')
#     app.register_blueprint(admin_blueprint,      url_prefix='/admin')
#     app.register_blueprint(main_blueprint)
#     app.register_blueprint(webhooks_blueprint)
#     app.register_blueprint(superadmin_blueprint, url_prefix='/superadmin')
#     app.register_blueprint(tenant_bp)
#
# AFTER (add the two highlighted lines):
#
#     from app.auth       import auth       as auth_blueprint
#     from app.admin      import admin      as admin_blueprint
#     from app.superadmin import superadmin as superadmin_blueprint
#     from app.main       import main       as main_blueprint
#     from app.tenant     import tenant_bp
#     from app.webhooks   import webhooks   as webhooks_blueprint
#     from routes.form_settings import superadmin_forms  # CRIT-05 ADD
#
#     app.register_blueprint(auth_blueprint,       url_prefix='/auth')
#     app.register_blueprint(admin_blueprint,      url_prefix='/admin')
#     app.register_blueprint(main_blueprint)
#     app.register_blueprint(webhooks_blueprint)
#     app.register_blueprint(superadmin_blueprint, url_prefix='/superadmin')
#     app.register_blueprint(superadmin_forms)  # CRIT-05 ADD — url_prefix='/superadmin' already in blueprint
#     app.register_blueprint(tenant_bp)  # Must remain LAST

# ── Standalone code snippet you can drop in: ─────────────────────────────────

def register_superadmin_forms(app):
    """
    Standalone helper — call this from create_app() after other blueprints.
    Registers the superadmin_forms blueprint from routes/form_settings.py.
    """
    try:
        from routes.form_settings import superadmin_forms
        app.register_blueprint(superadmin_forms)
        app.logger.info('superadmin_forms blueprint registered (CRIT-05 fix)')
    except ImportError as exc:
        app.logger.error(
            'CRIT-05: Could not import superadmin_forms from routes/form_settings.py: %s', exc
        )
        raise
