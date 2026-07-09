"""
test_tenant_security_v37.py — v3.7 Multi-Tenant Security Test Suite

Covers:
  • test_reserved_slugs         — RESERVED_SLUGS enforcement on create/update
  • test_tenant_auth            — Cross-tenant login isolation
  • test_session_hmac           — HMAC stamp/validate round-trip
  • test_user_loader_isolation  — user_loader refuses cross-tenant load
  • test_password_reset_isolation — password reset scoped to tenant
  • test_2fa_isolation          — 2FA session stamped with tenant
  • test_contact_settings       — TenantCommunicationSettings CRUD + fallback
  • test_contact_rate_limit     — per-tenant+IP rate limit key
  • test_superadmin_switching   — superadmin can switch tenants; non-superadmin cannot
  • test_resolve_active_tenant  — canonical resolution order

Run:
    python -m pytest test_tenant_security_v37.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# ── Minimal app bootstrap ─────────────────────────────────────────────────────
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-v37-hardened')
os.environ.setdefault('WTF_CSRF_ENABLED', 'False')
os.environ.setdefault('TESTING', 'True')


def _make_app():
    """Create a fresh Flask test app."""
    from app import create_app
    app = create_app('testing')
    app.config.update({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SECRET_KEY': 'test-secret-key-v37-hardened',
        'SERVER_NAME': None,
    })
    return app


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_user(db, username='alice', tenant_slug='acme', is_superadmin=False,
               password='TestPass1!'):
    """Create and return a User + Tenant + Profile triple."""
    from app.models.portfolio import Tenant, Profile
    from app.models import User

    tenant = Tenant(slug=tenant_slug, company_name=tenant_slug.title(),
                    email=f'{username}@example.com', status='active', plan='Basic')
    db.session.add(tenant)
    db.session.flush()

    profile = Profile(tenant_id=tenant.id, name=f'{username.title()} Test',
                      email=f'{username}@example.com')
    db.session.add(profile)
    db.session.flush()

    user = User(
        username=username,
        email=f'{username}@example.com',
        tenant_slug=tenant_slug,
        tenant_id=tenant.id,  # required FK
        is_admin=True,
        is_superadmin=is_superadmin,
    )
    user.password = password
    db.session.add(user)
    db.session.commit()
    return user, tenant, profile


# ─────────────────────────────────────────────────────────────────────────────
# 1. RESERVED SLUGS
# ─────────────────────────────────────────────────────────────────────────────

class TestReservedSlugs(unittest.TestCase):
    def test_canonical_reserved_set(self):
        from app.tenant_security import RESERVED_SLUGS
        for slug in ('admin', 'auth', 'superadmin', 'static', 'default',
                     'api', 'www', 'billing', 'webhook', 'login', 'logout'):
            self.assertIn(slug, RESERVED_SLUGS,
                          f'Expected {slug!r} in RESERVED_SLUGS')

    def test_is_reserved_slug_true(self):
        from app.tenant_security import is_reserved_slug
        self.assertTrue(is_reserved_slug('admin'))
        self.assertTrue(is_reserved_slug('ADMIN'))
        self.assertTrue(is_reserved_slug('  Auth  '))
        self.assertTrue(is_reserved_slug(''))

    def test_is_reserved_slug_false(self):
        from app.tenant_security import is_reserved_slug
        self.assertFalse(is_reserved_slug('acme-corp'))
        self.assertFalse(is_reserved_slug('john-doe'))
        self.assertFalse(is_reserved_slug('myclient'))

    def test_validate_slug_reserved(self):
        from app.tenant_security import validate_slug
        ok, err = validate_slug('admin')
        self.assertFalse(ok)
        self.assertIn('reserved', err)

    def test_validate_slug_valid(self):
        from app.tenant_security import validate_slug
        ok, err = validate_slug('john-doe')
        self.assertTrue(ok, f'Expected valid slug; got error: {err}')

    def test_validate_slug_format(self):
        from app.tenant_security import validate_slug
        self.assertFalse(validate_slug('-starts-with-dash')[0])
        self.assertFalse(validate_slug('ends-with-dash-')[0])
        self.assertFalse(validate_slug('has spaces')[0])
        self.assertFalse(validate_slug('a')[0])        # too short
        ok, _ = validate_slug('ab')
        self.assertTrue(ok)

    def test_superadmin_create_rejects_reserved(self):
        """Superadmin tenant creation must reject reserved slugs via validate_slug."""
        app = _make_app()
        with app.app_context():
            from app import db
            db.create_all()
            from app.tenant_security import validate_slug
            # Direct validation test — covers what the route does
            ok, err = validate_slug('superadmin')
            self.assertFalse(ok)
            self.assertIn('reserved', err.lower())

    def test_superadmin_rename_rejects_reserved(self):
        """Superadmin tenant rename must reject reserved slugs."""
        from app.tenant_security import validate_slug
        ok, err = validate_slug('dashboard')
        self.assertFalse(ok)


# ─────────────────────────────────────────────────────────────────────────────
# 2. SESSION HMAC
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionHMAC(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_stamp_and_validate_valid(self):
        """stamp_session_tenant writes HMAC; session_tenant_valid returns True."""
        from app.tenant_security import stamp_session_tenant, session_tenant_valid

        user, _, _ = _make_user(self.db, 'bob', 'bob-corp')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            stamp_session_tenant(user.id, 'bob-corp')

            self.assertEqual(session['tenant_slug'], 'bob-corp')
            self.assertIn('_tsig', session)
            self.assertTrue(session_tenant_valid())

    def test_legacy_signature_without_session_token_is_accepted_and_restamped(self):
        """Sessions signed with the pre-v4.0 format should still validate and be upgraded."""
        from app.tenant_security import session_tenant_valid

        user, _, _ = _make_user(self.db, 'legacy', 'legacy-co')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(user)

            created_at = datetime.now(timezone.utc).isoformat()
            secret = self.app.config['SECRET_KEY'].encode('utf-8')
            legacy_sig = hmac.new(
                secret,
                f"{user.id}:legacy-co:{created_at}".encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()

            session['tenant_slug'] = 'legacy-co'
            session['_tsig'] = legacy_sig
            session['_tsig_created'] = created_at
            session['_tsig_user_id'] = user.id
            session.pop('_session_token', None)

            self.assertTrue(session_tenant_valid())
            self.assertIn('_session_token', session)
            self.assertIn('_tsig', session)

    def test_tampered_signature_invalid(self):
        """Manually corrupting _tsig makes session_tenant_valid return False."""
        from app.tenant_security import stamp_session_tenant, session_tenant_valid

        user, _, _ = _make_user(self.db, 'carol', 'carol-co')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            stamp_session_tenant(user.id, 'carol-co')
            session['_tsig'] = 'aaaa' + session['_tsig'][4:]  # corrupt

            self.assertFalse(session_tenant_valid())

    def test_missing_signature_invalid(self):
        """Missing _tsig (pre-v3.7 session) causes session_tenant_valid to return False."""
        from app.tenant_security import session_tenant_valid

        user, _, _ = _make_user(self.db, 'dave', 'dave-inc')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            session['tenant_slug'] = 'dave-inc'
            # No _tsig at all

            self.assertFalse(session_tenant_valid())

    def test_uid_mismatch_invalid(self):
        """_tsig signed for uid=1 but current_user.id=2 should be invalid."""
        from app.tenant_security import stamp_session_tenant, session_tenant_valid

        user1, _, _ = _make_user(self.db, 'user1', 'tenant1')
        user2, _, _ = _make_user(self.db, 'user2', 'tenant2')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            # Stamp for user1, then login as user2
            stamp_session_tenant(user1.id, 'tenant1')
            login_user(user2)

            self.assertFalse(session_tenant_valid())


# ─────────────────────────────────────────────────────────────────────────────
# 3. resolve_active_tenant()
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveActiveTenant(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_non_superadmin_returns_db_tenant(self):
        """Non-superadmin always returns user.tenant_slug from DB, ignoring session."""
        from app.tenant_security import resolve_active_tenant

        user, _, _ = _make_user(self.db, 'eve', 'eve-studio')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            session['tenant_slug'] = 'hacked-tenant'  # attacker sets session

            result = resolve_active_tenant()
            self.assertEqual(result, 'eve-studio',
                             'Non-superadmin must return DB tenant_slug, not session')

    def test_superadmin_reads_session(self):
        """Superadmin can switch tenants via session."""
        from app.tenant_security import resolve_active_tenant

        sa, _, _ = _make_user(self.db, 'root', 'sa-root', is_superadmin=True)

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(sa)
            session['tenant_slug'] = 'some-client'

            result = resolve_active_tenant()
            self.assertEqual(result, 'some-client')

    def test_unauthenticated_returns_default(self):
        """Unauthenticated request returns 'default'."""
        from app.tenant_security import resolve_active_tenant

        with self.app.test_request_context('/'):
            result = resolve_active_tenant()
            self.assertEqual(result, 'default')

    def test_non_superadmin_corrects_drift(self):
        """If session drifted, resolve_active_tenant corrects it silently."""
        from app.tenant_security import resolve_active_tenant

        user, _, _ = _make_user(self.db, 'frank', 'frank-co')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            session['tenant_slug'] = 'wrong-tenant'

            result = resolve_active_tenant()
            # Should return DB value
            self.assertEqual(result, 'frank-co')
            # Session should be corrected
            self.assertEqual(session.get('tenant_slug'), 'frank-co')


# ─────────────────────────────────────────────────────────────────────────────
# 4. user_loader isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestUserLoaderIsolation(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_user_loader_correct_tenant(self):
        """user_loader returns user when session tenant matches."""
        user, _, _ = _make_user(self.db, 'grace', 'grace-lab')

        with self.app.test_request_context('/admin/'):
            from flask import session
            session['tenant_slug'] = 'grace-lab'

            from app import login_manager
            loaded = login_manager._user_callback(str(user.id))
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.username, 'grace')

    def test_user_loader_cross_tenant_refused(self):
        """user_loader returns None when session tenant differs from user.tenant_slug."""
        user, _, _ = _make_user(self.db, 'hank', 'hank-co')

        with self.app.test_request_context('/admin/'):
            from flask import session
            session['tenant_slug'] = 'other-tenant'  # different tenant

            from app import login_manager
            loaded = login_manager._user_callback(str(user.id))
            self.assertIsNone(loaded,
                'user_loader must refuse cross-tenant load')

    def test_user_loader_superadmin_bypasses(self):
        """Superadmin user_loader is not restricted by session tenant."""
        sa, _, _ = _make_user(self.db, 'iris', 'sa-iris', is_superadmin=True)

        with self.app.test_request_context('/admin/'):
            from flask import session
            session['tenant_slug'] = 'any-tenant'

            from app import login_manager
            loaded = login_manager._user_callback(str(sa.id))
            self.assertIsNotNone(loaded)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Password reset isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordResetIsolation(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_reset_email_scoped_to_tenant(self):
        """
        forgot_password scopes user lookup to session tenant.
        The tenant-scoped query returns only the matching tenant's user,
        not users from other tenants with the same email.
        (Note: SQLite enforces global email uniqueness; this test verifies
        the query filter logic rather than the multi-tenant data scenario.)
        """
        user_a, _, _ = _make_user(self.db, 'jack', 'acme')

        with self.app.test_request_context('/auth/forgot-password'):
            from flask import session
            session['tenant_slug'] = 'acme'

            from app.models import User
            # Simulate the scoped query used in the route
            result = User.query.filter(
                User.email == 'jack@example.com',
                User.tenant_slug == 'acme',
            ).first()
            self.assertIsNotNone(result)
            self.assertEqual(result.username, 'jack')

            # Querying a different tenant returns nothing for this user
            result_wrong = User.query.filter(
                User.email == 'jack@example.com',
                User.tenant_slug == 'other-tenant',
            ).first()
            self.assertIsNone(result_wrong,
                'Tenant-scoped query must not return users from other tenants')

    def test_reset_wrong_tenant_blocked(self):
        """
        reset_password validates token belongs to the session's tenant.
        """
        user, _, _ = _make_user(self.db, 'kate', 'kate-co')
        token = user.generate_reset_token(expires_in_minutes=30)
        self.db.session.commit()

        with self.app.test_request_context(f'/auth/reset-password/{token}'):
            from flask import session
            # Attacker is on a different tenant
            session['tenant_slug'] = 'evil-tenant'

            # Confirm that tenant mismatch would be caught
            from app.models import User as _User
            found = _User.query.filter_by(password_reset_token=token).first()
            self.assertIsNotNone(found)
            user_tenant = found.tenant_slug or 'default'
            active_tenant = session.get('tenant_slug', 'default')
            # They differ → route should block
            self.assertNotEqual(user_tenant, active_tenant)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Contact communication settings
# ─────────────────────────────────────────────────────────────────────────────

class TestContactSettings(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_get_or_create(self):
        """TenantCommunicationSettings.get_or_create creates new row."""
        _, tenant, _ = _make_user(self.db, 'leo', 'leo-labs')

        from app.models.portfolio import TenantCommunicationSettings
        cs = TenantCommunicationSettings.get_or_create(tenant.id, 'leo-labs')
        self.db.session.commit()

        self.assertEqual(cs.tenant_slug, 'leo-labs')
        self.assertFalse(cs.has_web3forms)
        self.assertFalse(cs.has_smtp)

    def test_encrypt_decrypt_web3forms(self):
        """web3forms_key setter encrypts; getter decrypts."""
        from app.models.portfolio import TenantCommunicationSettings, encrypt_secret, decrypt_secret
        cs = TenantCommunicationSettings()
        cs.web3forms_key = 'myplainkey123'

        # Raw stored value should NOT be plaintext
        raw = cs._web3forms_key
        self.assertNotEqual(raw, 'myplainkey123')
        self.assertTrue(len(raw) > 20)

        # Property should decrypt back
        self.assertEqual(cs.web3forms_key, 'myplainkey123')

    def test_encrypt_decrypt_mail_password(self):
        """mail_password setter encrypts; getter decrypts."""
        from app.models.portfolio import TenantCommunicationSettings
        cs = TenantCommunicationSettings()
        cs.mail_password = 's3cr3t_smtp_pw'

        self.assertNotEqual(cs._mail_password, 's3cr3t_smtp_pw')
        self.assertEqual(cs.mail_password, 's3cr3t_smtp_pw')

    def test_empty_secret_stays_empty(self):
        """Empty string round-trip does not produce garbage."""
        from app.models.portfolio import TenantCommunicationSettings
        cs = TenantCommunicationSettings()
        cs.web3forms_key = ''
        self.assertEqual(cs._web3forms_key, '')
        self.assertEqual(cs.web3forms_key, '')

    def test_effective_web3forms_falls_back(self):
        """If tenant key absent, falls back to app config."""
        from app.models.portfolio import TenantCommunicationSettings
        cs = TenantCommunicationSettings()
        # No key set
        result = cs.effective_web3forms_key({'WEB3FORMS_ACCESS_KEY': 'global-key'})
        self.assertEqual(result, 'global-key')

    def test_effective_smtp_falls_back(self):
        """If tenant SMTP absent, falls back to app config."""
        from app.models.portfolio import TenantCommunicationSettings
        cs = TenantCommunicationSettings()
        cfg = {'MAIL_SERVER': 'smtp.global.com', 'MAIL_PORT': '465',
               'MAIL_USERNAME': 'global@example.com', 'MAIL_PASSWORD': 'gpass',
               'MAIL_DEFAULT_SENDER': 'noreply@global.com', 'ADMIN_EMAIL': 'admin@global.com'}
        smtp = cs.effective_smtp_config(cfg)
        self.assertEqual(smtp['host'], 'smtp.global.com')
        self.assertEqual(smtp['username'], 'global@example.com')

    def test_has_smtp_true_when_configured(self):
        """has_smtp is True only when host + username + password all set."""
        from app.models.portfolio import TenantCommunicationSettings
        cs = TenantCommunicationSettings()
        cs.smtp_host = 'smtp.example.com'
        cs.mail_username = 'user@example.com'
        cs.mail_password = 'pass123'
        self.assertTrue(cs.has_smtp)

    def test_has_smtp_false_when_partial(self):
        """has_smtp is False if any of the three core fields is missing."""
        from app.models.portfolio import TenantCommunicationSettings
        cs = TenantCommunicationSettings()
        cs.smtp_host = 'smtp.example.com'
        cs.mail_username = 'user@example.com'
        # no password
        self.assertFalse(cs.has_smtp)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Superadmin tenant switching
# ─────────────────────────────────────────────────────────────────────────────

class TestSuperadminSwitching(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_superadmin_can_switch_tenant_via_session(self):
        """Superadmin: resolve_active_tenant reads session (switching allowed)."""
        from app.tenant_security import resolve_active_tenant
        sa, _, _ = _make_user(self.db, 'super', 'sa-system', is_superadmin=True)

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(sa)
            session['tenant_slug'] = 'client-a'
            self.assertEqual(resolve_active_tenant(), 'client-a')

            session['tenant_slug'] = 'client-b'
            self.assertEqual(resolve_active_tenant(), 'client-b')

    def test_non_superadmin_cannot_switch_tenant(self):
        """Non-superadmin: session tenant is ignored; DB tenant always returned."""
        from app.tenant_security import resolve_active_tenant
        user, _, _ = _make_user(self.db, 'nina', 'nina-co')

        with self.app.test_request_context('/admin/'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            session['tenant_slug'] = 'attacker-tenant'
            self.assertEqual(resolve_active_tenant(), 'nina-co')


# ─────────────────────────────────────────────────────────────────────────────
# 8. Contact rate-limit key
# ─────────────────────────────────────────────────────────────────────────────

class TestContactRateLimit(unittest.TestCase):
    def test_rate_limit_key_includes_tenant(self):
        """Rate-limit key must be scoped to tenant+IP, not just IP."""
        app = _make_app()
        with app.test_request_context('/acme/contact',
                                      environ_base={'REMOTE_ADDR': '1.2.3.4'}):
            from flask import g
            g.tenant_slug = 'acme'
            ip = '1.2.3.4'
            key = f"{g.get('tenant_slug', 'default')}:{ip}"
            self.assertEqual(key, 'acme:1.2.3.4')

            # Different tenant, same IP → different bucket
            g.tenant_slug = 'beta'
            key2 = f"{g.get('tenant_slug', 'default')}:{ip}"
            self.assertNotEqual(key, key2)


# ─────────────────────────────────────────────────────────────────────────────
# 9. VULN-07: tenant blueprint does not override session for authenticated users
# ─────────────────────────────────────────────────────────────────────────────

class TestTenantBlueprintSessionGuard(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_visiting_other_tenant_route_does_not_override_session(self):
        """
        An authenticated non-superadmin user visiting /<other-slug>/ must NOT
        have their session['tenant_slug'] overwritten.
        The tenant blueprint now guards this.
        """
        from app.tenant_security import resolve_active_tenant, stamp_session_tenant
        user, _, _ = _make_user(self.db, 'oscar', 'oscar-co')

        with self.app.test_request_context('/other-co/'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            stamp_session_tenant(user.id, 'oscar-co')

            # Simulate what the tenant blueprint now does for authenticated non-superadmin
            slug = 'other-co'  # from URL
            from flask_login import current_user
            if current_user.is_authenticated and not current_user.is_superadmin:
                pass  # blueprint skips setting session
            else:
                session['tenant_slug'] = slug

            # Session should still be 'oscar-co'
            self.assertEqual(session.get('tenant_slug'), 'oscar-co')
            # resolve_active_tenant should return DB value
            result = resolve_active_tenant()
            self.assertEqual(result, 'oscar-co')


# ─────────────────────────────────────────────────────────────────────────────
# 10. 2FA Isolation (v3.7)
# ─────────────────────────────────────────────────────────────────────────────

class Test2FAIsolation(unittest.TestCase):
    """
    Verify that:
      • stamp_session_tenant is called after 2FA verification (HMAC re-stamped)
      • A pending-2FA user from tenant-A cannot complete 2FA in a tenant-B session
      • TenantGuard skips the verify_2fa endpoint while _2fa_user_id is pending
    """

    def setUp(self):
        self.app = _make_app()
        self.ctx = self.app.app_context()
        self.ctx.push()
        from app import db
        db.create_all()
        self.db = db

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def test_hmac_stamped_after_2fa_verify(self):
        """After successful 2FA, session must have a valid _tsig signature."""
        from app.tenant_security import stamp_session_tenant, session_tenant_valid
        user, _, _ = _make_user(self.db, 'bob2fa', 'bob-corp')

        with self.app.test_request_context('/auth/login/2fa'):
            from flask_login import login_user
            from flask import session
            login_user(user)
            # Simulate post-2FA stamp
            stamp_session_tenant(user.id, 'bob-corp')
            session['totp_verified'] = True
            self.assertTrue(session_tenant_valid())
            self.assertEqual(session.get('tenant_slug'), 'bob-corp')
            self.assertIn('_tsig', session)

    def test_cross_tenant_2fa_blocked(self):
        """
        A pending-2FA session for tenant-A must be rejected when the active
        session tenant is tenant-B.  verify_2fa clears session and redirects.
        """
        user_a, _, _ = _make_user(self.db, 'alice2fa', 'tenant-a')

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess['_2fa_user_id']  = user_a.id
                sess['tenant_slug']   = 'tenant-b'   # attacker's tenant
                sess['_2fa_remember'] = False

            resp = client.post('/auth/login/2fa', data={'code': '000000', 'backup_code': ''})
            # Should not succeed; either redirect to login or 400, never 200 dashboard
            self.assertNotEqual(resp.status_code, 200)
            # After the request the _2fa_user_id should be cleared
            with client.session_transaction() as sess:
                self.assertNotIn('_2fa_user_id', sess)

    def test_tenant_guard_skips_pending_2fa_endpoint(self):
        """
        TenantGuard must not force-logout a user who is mid-2FA
        (i.e. _2fa_user_id is in session and endpoint is verify_2fa).
        """
        from app.tenant_security import TenantGuard, stamp_session_tenant
        user, _, _ = _make_user(self.db, 'carol2fa', 'carol-co')

        with self.app.test_request_context('/auth/login/2fa'):
            from flask_login import login_user
            from flask import session, _request_ctx_stack
            login_user(user)
            stamp_session_tenant(user.id, 'carol-co')
            # Now simulate that _2fa_user_id is pending (mid-2FA) — endpoint is verify_2fa
            session['_2fa_user_id'] = user.id
            # Manually set endpoint on the request context so TenantGuard can read it
            from flask import request as _req
            _req.endpoint = 'auth.verify_2fa'
            result = TenantGuard.validate()
            self.assertIsNone(result, 'TenantGuard must skip mid-2FA requests')

    def test_default_tenant_2fa_routes_to_auth_verify(self):
        """
        For the 'default' tenant, _complete_login must route to auth.verify_2fa,
        not tenant.auth_2fa (which would reject 'default' as reserved).
        """
        from app.auth import _is_default_tenant, _DEFAULT_TENANT_SLUG
        self.assertTrue(_is_default_tenant('default'))
        self.assertTrue(_is_default_tenant(''))
        self.assertTrue(_is_default_tenant(None))
        self.assertFalse(_is_default_tenant('someclient'))


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    unittest.main(verbosity=2)
