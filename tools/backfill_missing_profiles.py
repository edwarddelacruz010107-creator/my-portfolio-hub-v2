"""
tools/backfill_missing_profiles.py — one-time backfill for tenants that
registered before the self-service signup Profile fix.

Root cause this repairs:
    app/superadmin/routes/tenants.py "All Tenants" lists Profile rows, not
    Tenant rows. Self-service signup (app/services/auth/registration_service.py)
    used to create a Tenant + User but never a Profile, so those tenants were
    invisible in SuperAdmin even though the account genuinely existed (which
    is why re-registering with the same email correctly said "already
    exists" — the User row was there all along).

registration_service.py now creates the Profile at signup time, so this
script is only needed to catch up tenants that signed up BEFORE that fix
landed. It is idempotent — safe to run more than once, and skips any
tenant that already has a Profile.

Usage:
    flask shell -c "exec(open('tools/backfill_missing_profiles.py').read())"
  or add as a Flask CLI command / run inline in an app context.
"""
from app import db
from app.models.core import Tenant
from app.models.portfolio import Profile

created = []
skipped = []

for tenant in Tenant.query.all():
    existing = Profile.query.filter_by(tenant_slug=tenant.slug).first()
    if existing:
        skipped.append(tenant.slug)
        continue

    profile = Profile(
        tenant=tenant,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        name=tenant.company_name or tenant.slug,
        email=tenant.email or tenant.contact_email or '',
        plan=tenant.plan or 'Basic',
    )
    if profile.tenant_id is None:
        profile.tenant_id = tenant.id
        profile.tenant_slug = tenant.slug
    db.session.add(profile)
    created.append(tenant.slug)

db.session.commit()

print(f'Created {len(created)} missing profile(s): {created}')
print(f'Skipped {len(skipped)} tenant(s) that already had a profile.')
