import sys
import os
# BUG FIX (audit 2026-07-02): one '..' only reaches app/, not project
# root -- 'from app import ...' below failed with ModuleNotFoundError.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from app import create_app, db
from app.models.portfolio import Profile

app = create_app()
# Disable CSRF for test client actions
app.config['WTF_CSRF_ENABLED'] = False

with app.app_context():
    client = app.test_client()

    # Login as superadmin
    resp = client.post('/superadmin/login', data={
        'username': 'superadmin',
        'password': 'superadmin1234!@'
    }, follow_redirects=True)
    print('login status:', resp.status_code)
    html = resp.get_data(as_text=True)
    print('manage tenants button exists:', '/superadmin/tenants' in html and 'Manage Tenants' in html)
    print('sidebar tenants link exists:', 'href="/superadmin/tenants"' in html)
    print('login snippet:', html[:200].replace('\n',' '))

    # Visit tenants page
    r = client.get('/superadmin/tenants')
    print('tenants GET status:', r.status_code)

    # Create a new tenant (slug 'acme') if it doesn't exist
    slug = 'acme'
    existing = db.session.query(Profile).filter_by(tenant_slug=slug).first()
    if existing:
        print('tenant already exists:', existing.name, existing.tenant_slug)
    else:
        resp = client.post('/superadmin/tenants/new', data={'tenant_slug': slug, 'name': 'ACME Corp'}, follow_redirects=True)
        print('create tenant status:', resp.status_code)
        created = db.session.query(Profile).filter_by(tenant_slug=slug).first()
        if created:
            print('tenant created:', created.id, created.name, created.tenant_slug)
        else:
            print('tenant creation failed')
