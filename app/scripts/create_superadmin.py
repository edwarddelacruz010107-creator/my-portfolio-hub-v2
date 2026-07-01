"""
scripts/create_superadmin.py
──────────────────────────
Create a superadmin account for the owner of the whole system.

Usage:
  python scripts/create_superadmin.py
  ADMIN_USERNAME=owner ADMIN_EMAIL=owner@example.com ADMIN_PASSWORD='secret' python scripts/create_superadmin.py
"""
import os
import sys
import secrets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import app
from app import db
from app.models import User
from app.repositories import tenant_repository, user_repository

USERNAME = os.environ.get('ADMIN_USERNAME', 'superadmin')
EMAIL = os.environ.get('ADMIN_EMAIL', 'superadmin@portfolio.local')
PASSWORD = os.environ.get('SUPERADMIN_PASSWORD') or os.environ.get('ADMIN_PASSWORD')
DEFAULT_PASSWORD = 'superadmin1234!@'


def main():
    with app.app_context():
        existing = user_repository.get_by_username(USERNAME) or user_repository.get_by_email(EMAIL)
        password = PASSWORD or DEFAULT_PASSWORD

        if existing:
            existing.password = password
            existing.is_admin = True
            existing.is_superadmin = True
            tenant = tenant_repository.get_by_slug('default')
            if not tenant:
                tenant = Tenant(slug='default', company_name='Default Portfolio', email='superadmin@portfolio.local', status='active')
                db.session.add(tenant)
                db.session.flush()
            existing.tenant = tenant
            existing.tenant_slug = tenant.slug
            db.session.commit()
            print('✔  Superadmin account already exists — password updated:')
            print(f'   Username: {USERNAME}')
            print(f'   Password: {password}')
            return

        tenant = tenant_repository.get_by_slug('default')
        if not tenant:
            tenant = Tenant(slug='default', company_name='Default Portfolio', email='superadmin@portfolio.local', status='active')
            db.session.add(tenant)
            db.session.flush()

        u = User(username=USERNAME, email=EMAIL, is_admin=True, is_superadmin=True, tenant=tenant, tenant_slug=tenant.slug)
        u.password = password
        db.session.add(u)
        db.session.commit()

        print('✔  Created superadmin user:')
        print(f'   Username: {USERNAME}')
        print(f'   Email: {EMAIL}')
        print(f'   Password: {password}')
        print('⚠️  Change this password immediately after first login if you are on a public system.')


if __name__ == '__main__':
    main()
