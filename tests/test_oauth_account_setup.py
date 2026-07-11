"""Regression tests for OAuth account setup and editable tenant usernames."""
from __future__ import annotations

import uuid

from werkzeug.security import generate_password_hash

from app import db
from app.models.core import Tenant, User


def test_user_password_setter_enables_local_login():
    user = User(
        username='oauth-placeholder',
        email='oauth-placeholder@example.com',
        tenant_slug='placeholder',
        tenant_id=1,
        is_admin=True,
        auth_provider='google',
        password_hash=generate_password_hash('unavailable-secret'),
        local_password_enabled=False,
        oauth_setup_required=True,
    )
    assert user.verify_password('anything') is False

    user.password = 'StrongPassword!2026'

    assert user.local_password_enabled is True
    assert user.oauth_setup_required is False
    assert user.auth_provider == 'both'
    assert user.verify_password('StrongPassword!2026') is True


def test_oauth_and_username_routes_are_registered(app):
    endpoints = {rule.endpoint: str(rule) for rule in app.url_map.iter_rules()}
    assert endpoints['auth.oauth_account_setup'] == '/auth/oauth/account-setup'
    assert endpoints['admin.update_username'] == '/studio/settings/username'


def test_new_oauth_user_completes_one_time_setup(app):
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['SESSION_PROTECTION'] = None
    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        tenant = Tenant(
            slug=f'oauth-{suffix}',
            company_name='OAuth Setup Test',
            email=f'oauth-{suffix}@example.com',
            status='active',
            plan='starter',
            subscription_state='trial',
            subscription_status='trial',
        )
        db.session.add(tenant)
        db.session.flush()
        user = User(
            username=f'oauth-{suffix}',
            email=tenant.email,
            tenant_slug=tenant.slug,
            tenant_id=tenant.id,
            is_admin=True,
            is_superadmin=False,
            auth_provider='google',
            google_id=f'google-{suffix}',
            password_hash=generate_password_hash('unknown-random-secret'),
            local_password_enabled=False,
            oauth_setup_required=True,
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        tenant_slug = tenant.slug

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
        session['tenant_slug'] = tenant_slug

    response = client.get('/auth/oauth/account-setup')
    assert response.status_code == 200
    assert b'ONE-TIME ACCOUNT SETUP' in response.data

    username = f'user.{suffix}'
    response = client.post(
        '/auth/oauth/account-setup',
        data={
            'username': username,
            'password': 'StrongPassword!2026',
            'confirm_password': 'StrongPassword!2026',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/studio/')

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.username == username
        assert user.local_password_enabled is True
        assert user.oauth_setup_required is False
        assert user.auth_provider == 'both'
        assert user.verify_password('StrongPassword!2026') is True


def test_settings_template_contains_username_editor():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / 'app/templates/admin/settings.html').read_text(encoding='utf-8')
    assert "url_for('admin.update_username')" in source
    assert 'Connected Sign-in Methods' in source
    assert 'Signing in with Google or GitHub does not ask' in source


def test_oauth_account_setup_uses_polished_auth_layout():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / 'app/templates/auth/oauth_account_setup.html').read_text(encoding='utf-8')
    stylesheet = (root / 'app/static/css/oauth-account-setup.css').read_text(encoding='utf-8')
    script = (root / 'app/static/js/oauth-account-setup.js').read_text(encoding='utf-8')

    assert 'oauth-brand-panel' in template
    assert 'oauth-form-panel' in template
    assert 'get_flashed_messages' in template
    assert 'data-password-match' in template
    assert 'overflow-y:auto' in stylesheet
    assert 'prefers-reduced-motion' in stylesheet
    assert "localStorage.setItem('phPublicTheme'" in script
