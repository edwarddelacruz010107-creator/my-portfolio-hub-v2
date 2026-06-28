#!/usr/bin/env python
"""
test_legacy_billing_authz_fix.py — Regression tests for SEC Finding #1.

Verifies app/main/__init__.py billing routes (billing, billing_plans,
billing_payment, billing_history) can no longer be reached or mutated
by anonymous users or by admins of other tenants, while preserving
the existing flow for the 'default' tenant's own admin and superadmins.

Uses create_app('testing') — in-memory SQLite, never touches the
real CORE_DATABASE_URL / TENANT_DATABASE_URL.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from app import create_app, db
from app.models.portfolio import Tenant, Profile, Subscription
from app.models.core import User


def _dedupe_indexes(sa_extension):
    """
    Work around a pre-existing, unrelated bug present across multiple
    models in app/models/*.py: several tenant_id columns have both
    index=True (auto-name 'ix_<table>_tenant_id') and an explicit
    duplicate-named db.Index() in __table_args__. This breaks
    db.create_all() against any fresh database for EVERY bind
    (confirmed across both the default and 'tenant' SQLAlchemy binds —
    not just one table). Out of scope for this security fix; flagged
    separately in the audit report. Worked around here, locally, across
    all bind metadatas, so the regression suite can run.
    """
    for metadata in sa_extension.metadatas.values():
        for table in metadata.tables.values():
            seen = set()
            for ix in list(table.indexes):
                if ix.name in seen:
                    table.indexes.discard(ix)
                else:
                    seen.add(ix.name)


def _make_app():
    """
    NOTE: uses an isolated temp-file SQLite DB per test (not the shared
    'sqlite:///:memory:' default) to avoid a pre-existing, unrelated
    test-harness issue where Flask-SQLAlchemy reuses a single in-memory
    engine across repeated create_app('testing') calls within one
    process, causing 'index already exists' on db.create_all(). This is
    reproducible on the existing unmodified test_billing_v34.py and is
    out of scope for this security fix.
    """
    app = create_app('testing')
    db_fd, db_path = tempfile.mkstemp(suffix='.sqlite')
    os.close(db_fd)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_BINDS'] = {'tenant': f'sqlite:///{db_path}'}
    app.config['_test_db_path'] = db_path
    with app.app_context():
        db.engine.dispose()
        for bind in app.config['SQLALCHEMY_BINDS']:
            db.engines[bind].dispose()
    return app


def _make_tenant_and_profile(slug):
    tenant = Tenant(slug=slug, company_name=slug, email=f'{slug}@test.com', plan='Basic')
    db.session.add(tenant)
    db.session.flush()
    profile = Profile(tenant=tenant, name=slug, email=f'{slug}@test.com', plan='Basic')
    db.session.add(profile)
    db.session.flush()
    return tenant, profile


def _make_admin(tenant, username, is_superadmin=False):
    user = User(
        username=username,
        email=f'{username}@test.com',
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        is_admin=True,
        is_superadmin=is_superadmin,
    )
    user.password = 'TestPassw0rd!123'
    db.session.add(user)
    db.session.flush()
    return user


@pytest.fixture
def app():
    app = _make_app()
    with app.app_context():
        db.drop_all()
        _dedupe_indexes(db)
        db.create_all()
        default_tenant, default_profile = _make_tenant_and_profile('default')
        other_tenant, _ = _make_tenant_and_profile('othertenant')
        default_admin = _make_admin(default_tenant, 'default_admin')
        other_admin = _make_admin(other_tenant, 'other_admin')
        superadmin = _make_admin(default_tenant, 'super_admin', is_superadmin=True)
        db.session.commit()
        app.config['_test_ids'] = {
            'default_admin': default_admin.id,
            'other_admin': other_admin.id,
            'superadmin': superadmin.id,
        }
        yield app
        db.session.remove()
    db_path = app.config.get('_test_db_path')
    if db_path and os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, username, password='TestPassw0rd!123'):
    """
    Logs in via the real /auth/login flow (not a hand-crafted session).
    LOGIN_MANAGER uses session_protection='strong', which invalidates
    sessions lacking the user-agent/IP-derived '_id' that only
    flask_login.login_user() sets — so tests must drive the real
    login view rather than injecting session['_user_id'] directly.
    """
    return client.post(
        '/auth/login',
        data={'username': username, 'password': password},
        follow_redirects=False,
    )


# ── Anonymous access: must be denied ────────────────────────────────────────

def test_anonymous_get_billing_plans_redirects_to_login(client):
    resp = client.get('/billing/plans', follow_redirects=False)
    assert resp.status_code in (302, 401)
    if resp.status_code == 302:
        assert '/auth/login' in resp.headers['Location']


def test_anonymous_post_billing_plans_cannot_mutate(client, app):
    resp = client.post('/billing/plans', data={'plan': 'Pro', 'billing_cycle': 'monthly'})
    assert resp.status_code in (302, 401)
    if resp.status_code == 302:
        assert '/auth/login' in resp.headers['Location']

    with app.app_context():
        profile = Profile.query.filter_by(tenant_slug='default').first()
        sub = profile.current_subscription()
        # No subscription should have been created/modified by the anonymous request.
        assert sub is None or sub.plan != 'Pro'


def test_anonymous_billing_index_and_history_redirect(client):
    for path in ('/billing', '/billing/payment', '/billing/history'):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 401), f'{path} should not be publicly reachable'


# ── Cross-tenant access: must be denied (403) ───────────────────────────────

def test_other_tenant_admin_forbidden_from_default_billing(client, app):
    _login(client, 'other_admin')
    resp = client.get('/billing/plans')
    assert resp.status_code == 403


def test_other_tenant_admin_cannot_mutate_default_subscription(client, app):
    _login(client, 'other_admin')
    resp = client.post('/billing/plans', data={'plan': 'Enterprise', 'billing_cycle': 'monthly'})
    assert resp.status_code == 403

    with app.app_context():
        profile = Profile.query.filter_by(tenant_slug='default').first()
        sub = profile.current_subscription()
        assert sub is None or sub.plan != 'Enterprise'


# ── Authorized access: must still work (no functional regression) ─────────

def test_default_tenant_admin_can_view_billing_plans(client, app):
    _login(client, 'default_admin')
    resp = client.get('/billing/plans')
    assert resp.status_code == 200


def test_default_tenant_admin_can_update_subscription(client, app):
    _login(client, 'default_admin')
    resp = client.post(
        '/billing/plans',
        data={'plan': 'Pro', 'billing_cycle': 'monthly', 'action': 'manual'},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app.app_context():
        profile = Profile.query.filter_by(tenant_slug='default').first()
        sub = profile.current_subscription()
        assert sub is not None
        assert sub.plan == 'Pro'


def test_superadmin_can_view_default_billing_plans(client, app):
    _login(client, 'super_admin')
    resp = client.get('/billing/plans')
    assert resp.status_code == 200


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))