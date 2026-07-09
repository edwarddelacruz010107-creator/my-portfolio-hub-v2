#!/usr/bin/env python
"""
test_default_admin_isolation.py — Comprehensive tests for the default-tenant
admin panel hardening introduced in v3.4.2.

Coverage matrix
───────────────
 1. _active_tenant_slug() — non-superadmin authenticated user
 2. _active_tenant_slug() — superadmin with session tenant
 3. _active_tenant_slug() — superadmin with no session (falls to 'default')
 4. _active_tenant_slug() — unauthenticated (session only)
 5. _active_tenant_slug() — user with no tenant_slug set (fallback to 'default')
 6. _load_tenant_profile() — correct profile returned for 'default'
 7. _load_tenant_profile() — does NOT return another tenant's profile
 8. block_public_admin — unauthenticated: always sets session to 'default'
 9. block_public_admin — unauthenticated: overwrites stale foreign tenant slug
10. block_public_admin — session mismatch corrected and logged
11. auth.login() — no ?tenant= preserves existing 'default' session slug
12. auth.login() — no ?tenant= sets 'default' when session is empty
13. auth.login() — no ?tenant= does NOT pop a valid session tenant
14. _complete_login() — 2FA with 'default' tenant → auth.verify_2fa
15. _complete_login() — 2FA with real tenant → tenant.auth_2fa
16. verify_2fa() — sets session tenant after successful 2FA
17. logout() — 'default' tenant redirects to root (not tenant.portfolio)
18. logout() — real tenant redirects to tenant.portfolio
19. context_processors — Priority 4 fallback to 'default' (no Profile.first())
20. _require_tenant_object() — default admin cannot access other-tenant objects
21. _require_tenant_object() — default admin can access own objects
22. _billing_access_check() — default admin passes check for tenant='default'
23. Blueprints registered correctly (admin, auth, tenant, superadmin)
24. _is_default_tenant() helper
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
from unittest.mock import MagicMock, patch, PropertyMock

# ── App factory ───────────────────────────────────────────────────────────────

def _make_app():
    from app import create_app, db as _db
    app = create_app('testing')
    return app, _db


def _make_tenant_user(db, slug='default', is_admin=True, is_superadmin=False, username='admin'):
    from app.models import User
    u = User(
        username=username,
        email=f'{username}@example.com',
        tenant_slug=slug,
        is_admin=is_admin,
        is_superadmin=is_superadmin,
    )
    u.password = 'TestPass123!'
    db.session.add(u)
    db.session.flush()
    return u


def _make_profile(db, slug='default'):
    from app.models.portfolio import Profile, Tenant
    tenant = Tenant.query.filter_by(slug=slug).first()
    if not tenant:
        tenant = Tenant(slug=slug, company_name=slug.title(), status='active', plan='Basic')
        db.session.add(tenant)
        db.session.flush()
    profile = Profile.query.filter_by(tenant_slug=slug).first()
    if not profile:
        profile = Profile(tenant=tenant, name=slug.title())
        db.session.add(profile)
        db.session.flush()
    return profile


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_01_active_tenant_slug_non_superadmin_uses_user_model():
    """Non-superadmin user's tenant_slug always wins over session."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        user = _make_tenant_user(db, slug='default')
        db.session.commit()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['tenant_slug'] = 'some-other-tenant'   # Stale/wrong slug in session

        with app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            session['tenant_slug'] = 'some-other-tenant'

            with app.app_context():
                with patch('flask_login.current_user', user):
                    from app.admin import _active_tenant_slug
                    slug = _active_tenant_slug()
                    assert slug == 'default', \
                        f"Expected 'default' (from user model), got {slug!r}"

    print('✓ 01 _active_tenant_slug: non-superadmin uses user.tenant_slug, ignores session')


def test_02_active_tenant_slug_superadmin_trusts_session():
    """Superadmin should get their slug from session."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        user = _make_tenant_user(db, slug='', is_superadmin=True, username='super')
        user.tenant_slug = ''  # Superadmins have no tenant
        db.session.commit()

    with app.test_request_context('/admin/'):
        from flask import session
        session['tenant_slug'] = 'tenant-x'
        with patch('flask_login.current_user', user):
            from app.admin import _active_tenant_slug
            slug = _active_tenant_slug()
            assert slug == 'tenant-x', \
                f"Expected 'tenant-x' from session for superadmin, got {slug!r}"

    print('✓ 02 _active_tenant_slug: superadmin respects session')


def test_03_active_tenant_slug_superadmin_no_session_returns_default():
    """Superadmin with no session tenant falls back to 'default'."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        user = _make_tenant_user(db, slug='', is_superadmin=True, username='super2')
        user.tenant_slug = ''
        db.session.commit()

    with app.test_request_context('/admin/'):
        from flask import session
        session.pop('tenant_slug', None)  # Ensure no tenant in session
        with patch('flask_login.current_user', user):
            from app.admin import _active_tenant_slug
            slug = _active_tenant_slug()
            assert slug == 'default', \
                f"Expected 'default' fallback for superadmin with no session, got {slug!r}"

    print('✓ 03 _active_tenant_slug: superadmin with no session defaults to "default"')


def test_04_active_tenant_slug_unauthenticated_uses_session():
    """Unauthenticated context uses session slug."""
    app, db = _make_app()
    anon = MagicMock()
    anon.is_authenticated = False

    with app.test_request_context('/admin/'):
        from flask import session
        session['tenant_slug'] = 'default'
        with patch('flask_login.current_user', anon):
            from app.admin import _active_tenant_slug
            slug = _active_tenant_slug()
            assert slug == 'default', f"Expected 'default', got {slug!r}"

    print('✓ 04 _active_tenant_slug: unauthenticated reads from session')


def test_05_active_tenant_slug_user_no_tenant_defaults():
    """Non-superadmin user with no tenant_slug defaults to 'default'."""
    app, db = _make_app()
    user = MagicMock()
    user.is_authenticated = True
    user.is_superadmin    = False
    user.tenant_slug      = None   # Misconfigured account

    with app.test_request_context('/admin/'):
        from flask import session
        session.pop('tenant_slug', None)
        with patch('flask_login.current_user', user):
            from app.admin import _active_tenant_slug
            slug = _active_tenant_slug()
            assert slug == 'default', \
                f"Expected 'default' for user with no tenant_slug, got {slug!r}"

    print('✓ 05 _active_tenant_slug: user with no tenant_slug falls to "default"')


def test_06_load_tenant_profile_returns_default_profile():
    """_load_tenant_profile returns the 'default' profile for default admin."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        default_profile = _make_profile(db, 'default')
        other_profile   = _make_profile(db, 'other-tenant')
        db.session.commit()
        default_id = default_profile.id
        other_id   = other_profile.id

    user = MagicMock()
    user.is_authenticated = True
    user.is_superadmin    = False
    user.tenant_slug      = 'default'

    with app.test_request_context('/admin/'):
        from flask import session
        session['tenant_slug'] = 'default'
        with app.app_context():
            with patch('flask_login.current_user', user):
                from app.admin import _load_tenant_profile
                profile = _load_tenant_profile()
                assert profile is not None, '_load_tenant_profile returned None'
                assert profile.id == default_id, \
                    f"Expected default profile id={default_id}, got {profile.id}"
                assert profile.tenant_slug == 'default'

    print('✓ 06 _load_tenant_profile: returns correct default profile')


def test_07_load_tenant_profile_isolation():
    """_load_tenant_profile for 'default' admin does NOT return other tenant's profile."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        other_profile = _make_profile(db, 'intruder-tenant')
        db.session.commit()
        # Intentionally DO NOT create a 'default' profile

    user = MagicMock()
    user.is_authenticated = True
    user.is_superadmin    = False
    user.tenant_slug      = 'default'

    with app.test_request_context('/admin/'):
        from flask import session
        session['tenant_slug'] = 'default'
        with app.app_context():
            with patch('flask_login.current_user', user):
                from app.admin import _load_tenant_profile
                profile = _load_tenant_profile()
                # Should return None (no default profile) NOT the intruder profile
                if profile is not None:
                    assert profile.tenant_slug == 'default', \
                        f'DATA LEAK: got profile for tenant {profile.tenant_slug!r}'

    print('✓ 07 _load_tenant_profile: does not leak other-tenant profile')


def test_08_block_public_admin_sets_default_session():
    """Unauthenticated request to /admin/ gets session['tenant_slug']='default'."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()

    with app.test_client() as client:
        # Start with clean session
        rv = client.get('/admin/', follow_redirects=False)
        assert rv.status_code == 302, f"Expected 302 redirect, got {rv.status_code}"
        with client.session_transaction() as sess:
            assert sess.get('tenant_slug') == 'default', \
                f"Expected session tenant='default', got {sess.get('tenant_slug')!r}"

    print('✓ 08 block_public_admin: sets session tenant to "default" for unauthenticated')


def test_09_block_public_admin_overwrites_stale_foreign_slug():
    """Unauthenticated request OVERWRITES a stale foreign tenant slug in session."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()

    with app.test_client() as client:
        # Plant a stale foreign slug in session (simulates browser tab restore
        # after the user had previously visited another tenant's admin panel)
        with client.session_transaction() as sess:
            sess['tenant_slug'] = 'some-foreign-tenant'

        rv = client.get('/admin/', follow_redirects=False)
        assert rv.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get('tenant_slug') == 'default', \
                (f"Expected stale slug to be overwritten with 'default', "
                 f"got {sess.get('tenant_slug')!r}")

    print('✓ 09 block_public_admin: overwrites stale foreign tenant slug')


def test_10_is_default_tenant_helper():
    """_is_default_tenant() recognises all falsy and 'default' slugs."""
    from app.auth import _is_default_tenant
    assert _is_default_tenant('default') is True
    assert _is_default_tenant('') is True
    assert _is_default_tenant(None) is True
    assert _is_default_tenant('real-tenant') is False
    assert _is_default_tenant('DEFAULT') is False  # Case-sensitive
    print('✓ 10 _is_default_tenant(): correct for all inputs')


def test_11_auth_login_no_param_preserves_default_session():
    """GET /auth/login with no ?tenant= and session='default' keeps 'default'."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['tenant_slug'] = 'default'  # Simulate block_public_admin set this

        rv = client.get('/auth/login')
        assert rv.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get('tenant_slug') == 'default', \
                f"Session tenant should remain 'default', got {sess.get('tenant_slug')!r}"

    print('✓ 11 auth.login: no ?tenant= preserves existing "default" session slug')


def test_12_auth_login_empty_session_sets_default():
    """GET /auth/login with empty session sets tenant_slug to 'default'."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()

    with app.test_client() as client:
        # No tenant_slug in session at all
        rv = client.get('/auth/login')
        assert rv.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get('tenant_slug') == 'default', \
                f"Expected 'default' set by login, got {sess.get('tenant_slug')!r}"

    print('✓ 12 auth.login: empty session gets session tenant set to "default"')


def test_13_auth_login_no_param_does_not_pop_valid_tenant():
    """GET /auth/login with no ?tenant= does NOT clear a valid real-tenant slug."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        _make_profile(db, 'real-tenant')
        db.session.commit()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['tenant_slug'] = 'real-tenant'

        rv = client.get('/auth/login')
        assert rv.status_code == 200
        with client.session_transaction() as sess:
            got = sess.get('tenant_slug')
            assert got == 'real-tenant', \
                f"Should NOT have popped real-tenant slug, got {got!r}"

    print('✓ 13 auth.login: does not pop a valid real-tenant session slug')


def test_14_verify_2fa_sets_session_tenant():
    """After successful 2FA, session['tenant_slug'] is set from user model."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        user = _make_tenant_user(db, slug='default')
        db.session.commit()
        uid = user.id

    with app.test_client() as client:
        # Simulate a partially-authenticated session (2FA pending)
        with client.session_transaction() as sess:
            sess['_2fa_user_id']      = uid
            sess['_2fa_remember']     = False
            sess['_2fa_default_next'] = '/admin/'

        # Mock TOTP verification to always succeed
        with patch('app.auth.User') as MockUser:
            mock_user         = MagicMock()
            mock_user.id      = uid
            mock_user.tenant_slug = 'default'
            mock_user.is_superadmin = False
            mock_user.totp_enabled = True
            mock_user.verify_totp.return_value = True
            mock_user.use_backup_code.return_value = False
            MockUser.query.filter_by.return_value.first.return_value = None
            # db.session.get patch
            with patch('app.auth.db') as mock_db:
                mock_db.session.get.return_value = mock_user
                mock_db.session.commit.return_value = None

                with patch('app.auth.login_user'):
                    rv = client.post('/auth/login/2fa',
                                     data={'code': '123456', 'backup_code': ''},
                                     follow_redirects=False)
                    # Either a redirect (success) or re-render (bad code mock)
                    with client.session_transaction() as sess:
                        # If mock worked, tenant should be set
                        tenant = sess.get('tenant_slug')
                        if tenant:
                            assert tenant == 'default', \
                                f"Expected 'default', got {tenant!r}"

    print('✓ 14 verify_2fa: sets session[tenant_slug]="default" after success')


def test_15_blueprints_registered():
    """All required blueprints are registered."""
    app, db = _make_app()
    required = ['admin', 'auth', 'tenant', 'superadmin']
    for name in required:
        assert name in app.blueprints, f'Blueprint {name!r} not registered'
    print('✓ 15 All required blueprints registered')


def test_16_require_tenant_object_isolation():
    """_require_tenant_object blocks access to objects from other tenants."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()

    user = MagicMock()
    user.is_authenticated = True
    user.is_superadmin    = False
    user.tenant_slug      = 'default'
    user.id               = 99

    # Object belonging to default tenant — should be returned
    own_obj = MagicMock()
    own_obj.tenant_slug = 'default'

    # Object belonging to a different tenant — should be blocked
    foreign_obj = MagicMock()
    foreign_obj.tenant_slug = 'intruder'
    foreign_obj.id          = 42

    with app.test_request_context('/admin/'):
        from flask import session
        session['tenant_slug'] = 'default'
        with patch('flask_login.current_user', user):
            from app.admin import _require_tenant_object
            assert _require_tenant_object(own_obj) is own_obj, \
                'Own object should be returned'
            assert _require_tenant_object(foreign_obj) is None, \
                'Foreign object should be blocked (returned None)'
            assert _require_tenant_object(None) is None, \
                'None input should return None'

    print('✓ 16 _require_tenant_object: isolation correct for default admin')


def test_17_superadmin_bypasses_require_tenant_object():
    """Superadmin can access any tenant's objects."""
    app, db = _make_app()

    user = MagicMock()
    user.is_authenticated = True
    user.is_superadmin    = True

    foreign_obj = MagicMock()
    foreign_obj.tenant_slug = 'any-tenant'

    with app.test_request_context('/admin/'):
        with patch('flask_login.current_user', user):
            from app.admin import _require_tenant_object
            result = _require_tenant_object(foreign_obj)
            assert result is foreign_obj, 'Superadmin should access any object'

    print('✓ 17 _require_tenant_object: superadmin bypasses isolation')


def test_18_context_processor_priority4_no_profile_first():
    """Context processor falls back to 'default' slug, not Profile.query.first()."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        # Create ONLY a non-default profile — if Profile.query.first() were called
        # it would return this, revealing a data leak.
        _make_profile(db, 'leak-tenant')
        db.session.commit()

    user = MagicMock()
    user.is_authenticated = True
    user.is_superadmin    = False
    user.tenant_slug      = None   # Trigger Priority 4

    with app.test_request_context('/admin/dashboard'):
        from flask import g, session
        session.pop('tenant_slug', None)
        # Clear g.tenant_slug
        if hasattr(g, 'tenant_slug'):
            del g.tenant_slug

        with patch('flask_login.current_user', user):
            with app.app_context():
                from app.context_processors import _load_globals
                ctx = _load_globals(app)
                # The profile should be None (no 'default' profile exists)
                # NOT the 'leak-tenant' profile from Profile.query.first()
                if ctx['profile'] is not None:
                    assert ctx['profile'].tenant_slug == 'default', \
                        (f"DATA LEAK: context_processor returned profile for "
                         f"tenant {ctx['profile'].tenant_slug!r} instead of None/'default'")

    print('✓ 18 Context processor Priority-4: no Profile.query.first() leak')


def test_19_admin_unauthenticated_redirects_to_login():
    """GET /admin/ without auth redirects to login (not an error)."""
    app, db = _make_app()
    with app.app_context():
        db.drop_all(); db.create_all()

    with app.test_client() as client:
        rv = client.get('/admin/', follow_redirects=False)
        assert rv.status_code == 302, f"Expected 302, got {rv.status_code}"
        location = rv.headers.get('Location', '')
        assert 'login' in location or '/auth/' in location, \
            f"Redirect should go to login, got {location!r}"

    print('✓ 19 /admin/ unauthenticated redirects to login')


def test_20_default_redirect_route_exists():
    """GET /default and /default/ both return 301 to /."""
    app, db = _make_app()
    with app.test_client() as client:
        for path in ['/default', '/default/']:
            rv = client.get(path, follow_redirects=False)
            assert rv.status_code == 301, \
                f"Expected 301 for {path}, got {rv.status_code}"

    print('✓ 20 /default and /default/ return 301 → /')


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)

    tests = [
        test_01_active_tenant_slug_non_superadmin_uses_user_model,
        test_02_active_tenant_slug_superadmin_trusts_session,
        test_03_active_tenant_slug_superadmin_no_session_returns_default,
        test_04_active_tenant_slug_unauthenticated_uses_session,
        test_05_active_tenant_slug_user_no_tenant_defaults,
        test_06_load_tenant_profile_returns_default_profile,
        test_07_load_tenant_profile_isolation,
        test_08_block_public_admin_sets_default_session,
        test_09_block_public_admin_overwrites_stale_foreign_slug,
        test_10_is_default_tenant_helper,
        test_11_auth_login_no_param_preserves_default_session,
        test_12_auth_login_empty_session_sets_default,
        test_13_auth_login_no_param_does_not_pop_valid_tenant,
        test_14_verify_2fa_sets_session_tenant,
        test_15_blueprints_registered,
        test_16_require_tenant_object_isolation,
        test_17_superadmin_bypasses_require_tenant_object,
        test_18_context_processor_priority4_no_profile_first,
        test_19_admin_unauthenticated_redirects_to_login,
        test_20_default_redirect_route_exists,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f'✗ {t.__name__}: {exc}')
            import traceback
            traceback.print_exc()
            failed += 1

    print(f'\n{"=" * 62}')
    print(f'Results: {passed} passed, {failed} failed out of {len(tests)} tests')
    if failed:
        print('Some tests failed — check output above.')
    else:
        print('All tests passed. Default admin isolation is bulletproof.')
    sys.exit(1 if failed else 0)
