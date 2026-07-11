from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_smtp_blob_never_boolean_tests_sqlalchemy_clause():
    route = (ROOT / 'app/superadmin/routes/email_settings.py').read_text()
    model = (ROOT / 'app/models/core.py').read_text()
    assert "getattr(cfg, '_sa_smtp_password', '') or ''" not in route
    assert "getattr(verified_cfg, '_sa_smtp_password', '') or ''" not in route
    assert 'def get_sa_smtp_password_blob' in model
    assert 'sa_inspect(self).attrs._sa_smtp_password.value' in model


def test_resend_secret_save_uses_scalar_blob_and_activates_provider():
    route = (ROOT / 'app/superadmin/routes/email_settings.py').read_text()
    model = (ROOT / 'app/models/core.py').read_text()
    assert "getattr(cfg, '_sa_resend_api_key', '') or ''" not in route
    assert 'def get_sa_resend_api_key_blob' in model
    assert 'sa_inspect(self).attrs._sa_resend_api_key' in model
    assert 'existing_blob = cfg.get_sa_resend_api_key_blob()' in route
    assert 'cfg.sa_resend_active = True' in route
    assert 'get_fresh_by_id(config_id)' in route
    assert "'encrypted_key_blob_exists': bool(resend_blob)" in route


def test_resend_delivery_uses_standard_https_client():
    source = (ROOT / 'app/services/email/superadmin_email_service.py').read_text()
    resend = source[source.index('def _send_resend'):source.index('def _send_mailersend')]
    assert "requests.post(" in resend
    assert "https://api.resend.com/emails" in resend
    assert "'User-Agent': 'MyPortfolioHub/1.0 (+https://myportfoliohub.online)'" in resend


def test_superadmin_deploy_is_idempotent_and_never_logs_password():
    source = (ROOT / 'app/__init__.py').read_text()
    command = source[source.index("@app.cli.command('create-superadmin')"):]
    command = command[:command.index("@app.cli.command('create-admin')")]
    assert 'existing.password' not in command
    assert 'New password:' not in command
    assert 'Password:  {password}' not in command
    assert 'account preserved (password unchanged)' in command


def test_missing_project_placeholder_is_packaged():
    asset = ROOT / 'app/static/image/project_placeholder.svg'
    assert asset.is_file()
    assert '<svg' in asset.read_text()


def test_mailersend_1010_has_actionable_diagnostic():
    source = (ROOT / 'app/services/email/superadmin_email_service.py').read_text()
    assert "response.status_code == 403" in source
    assert "requests.post(" in source
    assert "'User-Agent': 'MyPortfolioHub/1.0 (+https://myportfoliohub.online)'" in source
    assert 'awaiting approval' in source


def test_theme_engine_enforces_editable_plan_theme_limits():
    source = (ROOT / 'app/theme_engine.py').read_text()
    assert 'def _theme_allowed_by_plan' in source
    assert 'get_plan_features(plan)' in source
    assert "theme_id != DEFAULT_THEME and not bool(features.get('theme_customization', False))" in source
    assert "meta.get('premium', False) and not bool(features.get('premium_themes', False))" in source
    assert "features and not features.get('theme_customization'" not in source
    assert 'return requested if self._theme_allowed_by_plan(tenant_profile, requested, meta) else FALLBACK_THEME' in source
    assert 'return self._theme_allowed_by_plan(tenant_profile, theme_id, meta)' in source


def test_admin_theme_preview_does_not_bypass_plan_gate():
    route = (ROOT / 'app/admin/routes/profile_appearance.py').read_text()
    template = (ROOT / 'app/templates/admin/themes/index.html').read_text()
    preview = route[route.index('def preview_theme'):route.index('rendered_preview = engine.render')]
    assert 'if not engine.can_use_theme(profile, theme_id):' in preview
    assert 'abort(403)' in preview
    assert 'is_administrator=True' not in preview
    assert 'is_administrator=False' in preview
    assert "{% if theme._can_use %}" in template
    assert 'Preview locked for {{ theme.name }}' in template


def test_plan_limits_include_theme_access_switches():
    limits = (ROOT / 'app/services/billing/trial_limits.py').read_text()
    assert '"theme_customization",' in limits
    assert '"premium_themes",' in limits
    assert '"theme_customization": False' in limits
    assert '"premium_themes": False' in limits
    assert '"theme_customization": limits["theme_customization"]' in limits
    assert '"premium_themes": limits["premium_themes"]' in limits
