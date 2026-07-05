"""
scripts/create_admin.py
────────────────────────
Idempotent helper to create a default admin account.

Usage:
  - With generated password:
      python scripts/create_admin.py
  - With explicit password (POSIX):
      ADMIN_PASSWORD=secret python scripts/create_admin.py
  - With explicit password (PowerShell):
      $env:ADMIN_PASSWORD = 'secret'; python scripts/create_admin.py

The script honors `ADMIN_USERNAME`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` env vars.
"""
import os
import sys
import secrets

# BUG FIX (audit 2026-07-02): script lives at app/scripts/, 2 levels
# under project root -- 2x dirname() landed on app/, not project root,
# breaking any 'from run import app' / 'from app import ...' import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from run import app
from app import db
from app.models import User
from app.models.core import Tenant
from app.repositories import tenant_repository, user_repository
from app.services.auth.email_policy import EmailPolicyError, assert_email_allowed_for_user


USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
EMAIL = os.environ.get('ADMIN_EMAIL', 'delacruzedward735@gmail.com')
PASSWORD = os.environ.get('ADMIN_PASSWORD')


def main():
    with app.app_context():
        # Check for existing user by username. Email reuse is governed by the
        # central policy because the owner email is intentionally shared by
        # SuperAdmin and the default portfolio admin.
        if user_repository.get_by_username(USERNAME):
            print('Admin user already exists (by username).')
            return

        try:
            email = assert_email_allowed_for_user(EMAIL, role='tenant_admin', slug='default')
        except EmailPolicyError as exc:
            print(f'Cannot create admin user: {exc}')
            return

        generated = False
        password = PASSWORD
        if not password:
            password = secrets.token_urlsafe(12)
            generated = True

        tenant = tenant_repository.get_by_slug('default')
        if not tenant:
            tenant = Tenant(slug='default', company_name='Default Portfolio', email=email, status='active', plan='Administrator')
            db.session.add(tenant)
            db.session.flush()

        u = User(username=USERNAME, email=email, is_admin=True, tenant=tenant, tenant_slug=tenant.slug)
        u.password = password
        db.session.add(u)
        db.session.commit()

        print('✔  Created admin user:')
        print(f'   Username: {USERNAME}')
        print(f'   Email: {EMAIL}')
        if generated:
            print(f'   Temporary password: {password}')
            print('⚠️  IMPORTANT: Change this temporary password immediately after first login!')


if __name__ == '__main__':
    main()
