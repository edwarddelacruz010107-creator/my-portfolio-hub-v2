"""
scripts/seed_default_tenant.py
Ensure the 'default' tenant Profile and its owner User exist.

Usage:
    python scripts/seed_default_tenant.py
    python scripts/seed_default_tenant.py --slug my-portfolio
"""
import sys
import os
# BUG FIX (audit 2026-07-02): one '..' only reaches app/, not project
# root -- 'from app import ...' below failed with ModuleNotFoundError.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import argparse
from app import create_app, db
from app.models import User
from app.repositories import tenant_repository, user_repository, profile_repository

parser = argparse.ArgumentParser()
parser.add_argument('--slug',     default='default',     help='Tenant slug to seed')
parser.add_argument('--name',     default='Your Name',   help='Profile name')
parser.add_argument('--username', default='admin',       help='Admin username')
parser.add_argument('--email',    default='admin@example.com', help='Admin email')
parser.add_argument('--password', default='changeme123', help='Admin password')
args = parser.parse_args()

app = create_app('development')
with app.app_context():
    # Ensure Profile exists
    tenant = tenant_repository.get_by_slug(args.slug)
    if not tenant:
        tenant = Tenant(
            slug=args.slug,
            company_name=args.name,
            email=args.email,
            status='active',
            plan='Basic',
        )
        db.session.add(tenant)
        db.session.flush()

    profile = profile_repository.get_by_tenant_id(tenant.id)
    if not profile:
        profile = Profile(
            tenant=tenant,
            name=args.name,
            title='Full Stack Developer',
            subtitle='Building beautiful digital experiences',
        )
        db.session.add(profile)
        print(f"[+] Created Profile for tenant '{args.slug}'")
    else:
        print(f"[=] Profile already exists for tenant '{args.slug}'")

    # Ensure User exists
    user = user_repository.get_by_email(args.email)
    if not user:
        user = User(
            username=args.username,
            email=args.email,
            tenant=tenant,
            is_admin=True,
            is_superadmin=False,
        )
        user.password = args.password
        db.session.add(user)
        print(f"[+] Created admin user '{args.username}' for tenant '{args.slug}'")
        print(f"    ⚠  Change the password immediately: {args.password}")
    else:
        print(f"[=] Admin user already exists for tenant '{args.slug}'")

    db.session.commit()
    print(f"\n✓ Tenant '{args.slug}' ready.")
    print(f"  Portfolio: http://localhost:5000/{args.slug}/")
    print(f"  Admin:     http://localhost:5000/{args.slug}/admin/login")
