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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
from app import db
from app.models.portfolio import Tenant
from app.models import User


USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@portfolio.local')
PASSWORD = os.environ.get('ADMIN_PASSWORD')


def main():
    with app.app_context():
        # Check for existing user by username or email
        if User.query.filter_by(username=USERNAME).first() or User.query.filter_by(email=EMAIL).first():
            print('Admin user already exists (by username or email).')
            return

        generated = False
        password = PASSWORD
        if not password:
            password = secrets.token_urlsafe(12)
            generated = True

        tenant = Tenant.query.filter_by(slug='default').first()
        if not tenant:
            tenant = Tenant(slug='default', company_name='Default Portfolio', email=EMAIL, status='active')
            db.session.add(tenant)
            db.session.flush()

        u = User(username=USERNAME, email=EMAIL, is_admin=True, tenant=tenant, tenant_slug=tenant.slug)
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
