#!/usr/bin/env python3
"""
scripts/seed.py — Seed a fresh dual-DB deployment

Creates:
  • One default superadmin user in core_db
  • One default Tenant in core_db
  • One default Profile in tenant_data_db

Usage:
    FLASK_ENV=production \
    CORE_DATABASE_URL=... \
    TENANT_DATABASE_URL=... \
    SECRET_KEY=... \
    FERNET_KEY=... \
    python scripts/seed.py

Idempotent: safe to run multiple times (skips already-existing rows).
"""

import os
import sys

# ── Bootstrap Flask app ──────────────────────────────────────────────────────
# BUG FIX (audit 2026-07-02): script lives at app/scripts/, 2 levels
# under project root -- 2x dirname() landed on app/, not project root,
# breaking any 'from run import app' / 'from app import ...' import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import create_app, db
from app.models import Tenant, User, Profile
from app.repositories import tenant_repository, user_repository, profile_repository

app = create_app(os.environ.get('FLASK_ENV', 'production'))


def seed():
    with app.app_context():
        print('── Seeding core_db ─────────────────────────────────────────')

        # Default tenant
        tenant = tenant_repository.get_by_slug('default')
        if not tenant:
            tenant = Tenant(
                slug='default',
                company_name='My Portfolio',
                email='admin@example.com',
                status='active',
                plan='Basic',
            )
            db.session.add(tenant)
            db.session.flush()
            print(f'  ✓ Tenant "default" created (id={tenant.id})')
        else:
            print(f'  · Tenant "default" already exists (id={tenant.id})')

        # Superadmin user
        superadmin = user_repository.get_by_username('superadmin')
        if not superadmin:
            raw_password = os.environ.get('SUPERADMIN_PASSWORD', 'change-me-immediately')
            superadmin = User(
                username='superadmin',
                email=os.environ.get('SUPERADMIN_EMAIL', 'superadmin@example.com'),
                tenant_id=tenant.id,
                tenant_slug='default',
                is_admin=True,
                is_superadmin=True,
            )
            superadmin.password = raw_password
            db.session.add(superadmin)
            print('  ✓ Superadmin user created')
        else:
            print('  · Superadmin user already exists')

        db.session.commit()
        print('── core_db seed complete ────────────────────────────────────')

        print('── Seeding tenant_data_db ──────────────────────────────────')
        profile = profile_repository.get_by_tenant_id(tenant.id)
        if not profile:
            profile = Profile(
                tenant_id=tenant.id,
                tenant_slug='default',
                name='Portfolio Owner',
                title='Full Stack Developer',
                bio='Welcome to my portfolio.',
            )
            db.session.add(profile)
            db.session.commit()
            print('  ✓ Default Profile created')
        else:
            print('  · Default Profile already exists')

        print('── tenant_data_db seed complete ────────────────────────────')
        print('\nSeed complete. Change the superadmin password immediately.')


if __name__ == '__main__':
    seed()
