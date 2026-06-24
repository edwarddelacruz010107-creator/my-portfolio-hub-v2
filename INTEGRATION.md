# Portfolio CMS v4.2 — Integration Guide
## Part 1: Sub Monitor Rebuild  |  Part 2: Per-Tenant Form Isolation

---

## WHAT'S IN THIS PACKAGE

```
output/
├── migrations/
│   ├── 0022_tenant_form_settings.sql      ← Run directly on Postgres (manual)
│   └── 0022_tenant_form_settings.py       ← Alembic version (flask db upgrade)
├── models/
│   └── tenant_form_settings.py            ← SQLAlchemy model + Fernet encryption
├── services/
│   └── forms.py                           ← Contact form dispatch service
├── forms/
│   └── tenant_forms.py                    ← WTForms with cross-field validation
├── routes/
│   └── form_settings.py                   ← Tenant admin + superadmin blueprints
└── templates/
    ├── superadmin/
    │   ├── subscription_monitor.html       ← Rebuilt to match dashboard styling
    │   ├── forms_overview.html             ← Superadmin: all tenants, masked keys
    │   └── forms_tenant_detail.html        ← Superadmin: single tenant view
    └── admin/settings/
        └── form_settings.html             ← Tenant admin: provider picker + config
```

---

## PART 1 — SUBSCRIPTION MONITOR

### What changed
- Replaced emoji section headers → Lucide icons via iconify-icon
- Replaced custom `.metric-card` divs → existing `stat-card-v2` / `stats-row` classes
- Replaced plain `.card` tables → `table-card` + `data-table` pattern (matches tenants page)
- Replaced inline `<style>` junk CSS → scoped block using same CSS vars as base
- Added 8 metric cards: Active, Expired, Expiring, Pending, Revenue, MRR, ARR, Trial
- Added empty states (no more bare "No subscriptions" table rows)
- Added responsive breakpoints: tablet hides cols 4+, mobile 2-col stats

### Drop-in replacement
```
cp templates/superadmin/subscription_monitor.html \
   <your_project>/templates/superadmin/subscription_monitor.html
```

No route changes needed. The template consumes the same context variables
(`metrics`, `expiring_7`, `expiring_30`, `expired`, `recent_notifications`, `now`).

### New metric keys expected in `metrics` dict
Add these to your `subscription_monitor` route:
```python
metrics['mrr']         = calculate_mrr()   # sum of active monthly subscriptions
metrics['arr']         = metrics['mrr'] * 12
metrics['total_trial'] = Subscription.query.filter_by(status='trial').count()
```
If these keys are missing, the template defaults to 0 gracefully.

---

## PART 2 — PER-TENANT FORM ISOLATION

### Architecture
```
Visitor
  └─→ Tenant Contact Form (e.g. john.portfoliohub.online)
        └─→ main route detects tenant_id from subdomain
              └─→ send_contact_message(tenant_id=X, ...)
                    └─→ load TenantFormSettings WHERE tenant_id=X
                          ├─→ provider=basin     → POST to tenant's Basin endpoint
                          ├─→ provider=web3forms → POST to Web3Forms with tenant key
                          └─→ provider=disabled  → return INTERNAL_FALLBACK signal
                                                   (caller stores in Inquiry table)
```
No tenant can ever see another's API key. Keys never leave the server.

### Step 1 — Run migration
```bash
# Option A: Alembic (recommended)
cp migrations/0022_tenant_form_settings.py <project>/migrations/versions/
flask db upgrade

# Option B: Raw SQL
psql $DATABASE_URL < migrations/0022_tenant_form_settings.sql
```

### Step 2 — Add model
```bash
cp models/tenant_form_settings.py <project>/app/models/
```

Register in `app/models/__init__.py`:
```python
from app.models.tenant_form_settings import TenantFormSettings  # noqa: F401
```

### Step 3 — Add service
```bash
cp services/forms.py <project>/app/services/
```

### Step 4 — Add form class
```bash
cp forms/tenant_forms.py <project>/app/forms/
```

### Step 5 — Register blueprints
```bash
cp routes/form_settings.py <project>/app/routes/
```

In `app/__init__.py` (inside `create_app`):
```python
from app.routes.form_settings import admin_forms, superadmin_forms

app.register_blueprint(admin_forms)
app.register_blueprint(superadmin_forms)
```

### Step 6 — Copy templates
```bash
cp templates/superadmin/forms_overview.html        <project>/templates/superadmin/
cp templates/superadmin/forms_tenant_detail.html   <project>/templates/superadmin/
mkdir -p <project>/templates/admin/settings
cp templates/admin/settings/form_settings.html     <project>/templates/admin/settings/
```

### Step 7 — Add nav links

In `templates/admin/base.html`, add under Settings section:
```html
<a href="{{ url_for('admin_forms.form_settings') }}"
   class="nav-item {% if request.endpoint == 'admin_forms.form_settings' %}active{% endif %}">
  <span class="nav-icon"><iconify-icon icon="lucide:layout-panel-top" width="18"></iconify-icon></span>
  Contact Form
</a>
```

In `templates/superadmin/base.html`, add under Communications section:
```html
<a href="{{ url_for('superadmin_forms.forms_overview') }}"
   class="nav-item {% if 'superadmin_forms' in request.endpoint %}active{% endif %}">
  <span class="nav-icon"><iconify-icon icon="lucide:layout-panel-top" width="18"></iconify-icon></span>
  Form Providers
</a>
```

### Step 8 — Update existing contact form route

In your existing contact route (likely in `app/main/__init__.py` or similar):

```python
from app.services.forms import send_contact_message

@main.route('/contact', methods=['POST'])
@limiter.limit('10/hour')
def contact():
    tenant = g.tenant  # resolved from subdomain — your existing pattern
    
    success, error = send_contact_message(
        tenant_id=tenant.id,
        name=request.form.get('name', ''),
        email=request.form.get('email', ''),
        subject=request.form.get('subject', 'Contact Form Submission'),
        message=request.form.get('message', ''),
    )
    
    if success:
        flash('Message sent!', 'success')
    elif error == 'INTERNAL_FALLBACK':
        # Store in Inquiry table — existing behaviour
        _store_inquiry(tenant, request.form)
        flash('Message received!', 'success')
    else:
        flash(f'Error: {error}', 'error')
    
    return redirect(url_for('main.index'))
```

---

## SECURITY CHECKLIST

| Control                        | Implementation                          |
|--------------------------------|-----------------------------------------|
| API key storage                | Fernet-encrypted (reuses existing key)  |
| API key in templates           | NEVER — masked display only             |
| API key in JSON responses      | NEVER                                   |
| Tenant isolation               | tenant_id from subdomain, not POST body |
| CSRF                           | Flask-WTF on all POST routes            |
| Rate limiting (test endpoint)  | `@limiter.limit('5 per minute')`        |
| Input validation               | WTForms + service-layer truncation      |
| Error logging                  | logger.warning/error (no key values)    |

---

## ENVIRONMENT VARIABLES

No new env vars required. Encryption reuses:
```
FERNET_KEY=<your-existing-key>   # or falls back to SECRET_KEY derivation
```

---

## BACKWARD COMPATIBILITY

- No existing columns dropped
- `tenants.form_provider` and `tenants.basin_endpoint` (from 0021) remain untouched
- Backfill migration copies existing basin config into `tenant_form_settings`
- Existing `basin_service.py` and `TenantCommunicationSettings` unchanged
- Internal CMS fallback (`INTERNAL_FALLBACK`) preserves all existing Inquiry behaviour
