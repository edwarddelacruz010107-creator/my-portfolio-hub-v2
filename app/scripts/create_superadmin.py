"""
scripts/create_superadmin.py
──────────────────────────
Create a superadmin account for the owner of the whole system.

Usage (run from project root):
  python app/scripts/create_superadmin.py
  ADMIN_USERNAME=owner ADMIN_EMAIL=owner@example.com ADMIN_PASSWORD='secret' python app/scripts/create_superadmin.py
"""
import os
import sys
import secrets

# BUG FIX: this file lives at app/scripts/create_superadmin.py (2 levels
# under project root), but the path math here only climbed 2 dirname()
# calls -> landed at .../app, not project root. `from run import app`
# below has been failing with ModuleNotFoundError on every invocation run
# the way the docstring above documents. Now walks up 3 levels
# (create_superadmin.py -> scripts -> app -> project root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from run import app
from app import db
from app.models import User
from app.models.core import Tenant  # BUG FIX: was unimported — NameError on any fresh-DB bootstrap
from app.repositories import tenant_repository, user_repository

USERNAME = os.environ.get('ADMIN_USERNAME', 'superadmin')
EMAIL = os.environ.get('ADMIN_EMAIL', 'superadmin@portfolio.local')
PASSWORD = os.environ.get('SUPERADMIN_PASSWORD') or os.environ.get('ADMIN_PASSWORD')

# SECURITY: removed hardcoded 'superadmin1234!@' fallback. That value was
# reachable by anyone who read this file (or this repo/patch zip) — if an
# operator forgot to set SUPERADMIN_PASSWORD/ADMIN_PASSWORD, the account was
# provisioned with a fully predictable credential. Now: generate a random
# one-time password if none is supplied, AND set require_password_reset=True
# so User.require_password_reset (enforced centrally in
# app/auth/__init__.py::_authorize_and_login, which both the superadmin and
# tenant-admin login flows call — see app/superadmin/routes/core_auth.py
# ::login) forces a change on first login regardless of which credential
# path was used. This field and its enforcement already existed in the
# codebase (migration 0008) — it just was never set here.
_GENERATED_PASSWORD = PASSWORD is None
if _GENERATED_PASSWORD:
    PASSWORD = secrets.token_urlsafe(18)


def _upsert_default_tenant():
    tenant = tenant_repository.get_by_slug('default')
    if not tenant:
        tenant = Tenant(
            slug='default',
            company_name='Default Portfolio',
            email='superadmin@portfolio.local',
            status='active',
        )
        db.session.add(tenant)
        db.session.flush()
    return tenant


def main():
    with app.app_context():
        existing = user_repository.get_by_username(USERNAME) or user_repository.get_by_email(EMAIL)
        password = PASSWORD

        if existing:
            existing.password = password
            existing.is_admin = True
            existing.is_superadmin = True
            existing.require_password_reset = True
            tenant = _upsert_default_tenant()
            existing.tenant = tenant
            existing.tenant_slug = tenant.slug
            db.session.commit()
            print('✔  Superadmin account already exists — password updated:')
            print(f'   Username: {USERNAME}')
            if _GENERATED_PASSWORD:
                print(f'   Password (generated, one-time): {password}')
                print('   ⚠️  Store this now — it will not be shown again. Change it on first login.')
            else:
                print('   Password: <as supplied via env var>')
            return

        tenant = _upsert_default_tenant()

        u = User(username=USERNAME, email=EMAIL, is_admin=True, is_superadmin=True, tenant=tenant, tenant_slug=tenant.slug)
        u.password = password
        u.require_password_reset = True
        db.session.add(u)
        db.session.commit()

        print('✔  Created superadmin user:')
        print(f'   Username: {USERNAME}')
        print(f'   Email: {EMAIL}')
        if _GENERATED_PASSWORD:
            print(f'   Password (generated, one-time): {password}')
            print('   ⚠️  Store this now — it will not be shown again. Change it on first login.')
        else:
            print('   Password: <as supplied via env var>')
        print('⚠️  Change this password immediately after first login if you are on a public system.')


if __name__ == '__main__':
    main()
