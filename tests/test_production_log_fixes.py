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
    assert 'sa_inspect(self).attrs._sa_resend_api_key.value' in model
    assert 'existing_blob = cfg.get_sa_resend_api_key_blob()' in route
    assert 'cfg.sa_resend_active = True' in route


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
