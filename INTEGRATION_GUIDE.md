# ENTERPRISE REFACTOR v6.0 — INTEGRATION GUIDE

## Overview of Deliverables

```
refactor/
├── app/
│   ├── admin/
│   │   └── upload_handlers.py          ← Quota-enforced upload endpoint
│   ├── middleware/
│   │   └── subscription_guard.py       ← Subscription lifecycle middleware
│   ├── models/
│   │   └── core_additions.py           ← MediaUpload + PlanUsageLog ORM models
│   ├── services/
│   │   ├── plan_capabilities.py        ← Capability-based plan system
│   │   └── storage_service.py          ← Upload validation, optimisation, quota
│   └── templates/
│       └── admin/
│           ├── billing/
│           │   └── plans_v6.html       ← Capability-driven plan comparison page
│           └── partials/
│               └── _quota_widget.html  ← Storage quota sidebar widget
├── migrations/versions/
│   └── 0033_storage_quota_plan_caps.py ← Additive DB migration
├── tools/
│   └── debug_admin_otp.py              ← OTP delivery diagnostic
└── PATCH_INSTRUCTIONS_core_py.py       ← Exact surgical edits to core.py
```

---

## Phase 1 — Copy New Files (Safe, Additive)

```bash
# From project root
cp refactor/app/services/plan_capabilities.py       app/services/
cp refactor/app/services/storage_service.py         app/services/
cp refactor/app/models/core_additions.py            app/models/
cp refactor/app/admin/upload_handlers.py            app/admin/
cp refactor/app/middleware/subscription_guard.py    app/middleware/
cp refactor/app/templates/admin/billing/plans_v6.html \
                                                    app/templates/admin/billing/
cp refactor/app/templates/admin/partials/_quota_widget.html \
                                                    app/templates/admin/partials/
cp refactor/migrations/versions/0033_storage_quota_plan_caps.py \
                                                    migrations/versions/
cp refactor/tools/debug_admin_otp.py                tools/
```

---

## Phase 2 — Run the Migration

```bash
flask db upgrade 0033_storage_quota_plan_caps
```

Verify:
```sql
-- In psql / pgAdmin:
SELECT column_name FROM information_schema.columns
WHERE table_name = 'tenants'
  AND column_name IN ('storage_used_bytes','storage_limit_bytes','subscription_state','grace_period_ends_at');
-- Should return 4 rows

SELECT table_name FROM information_schema.tables
WHERE table_name IN ('media_uploads', 'plan_usage_log');
-- Should return 2 rows
```

---

## Phase 3 — Edit app/models/core.py (Surgical)

See `PATCH_INSTRUCTIONS_core_py.py` for exact diffs.
Summary of changes:

### 3a. Add Tenant columns (after `updated_at` line):
```python
storage_used_bytes   = db.Column(db.BigInteger, nullable=False, default=0)
storage_limit_bytes  = db.Column(db.BigInteger, nullable=True)
subscription_state   = db.Column(db.String(30), nullable=False, default='active')
grace_period_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
```

### 3b. Replace `plan_features()` and add helpers (inside Tenant class):
```python
def plan_features(self) -> dict:
    from app.services.plan_capabilities import get_tenant_capabilities
    return get_tenant_capabilities(self).as_dict()

def get_capabilities(self):
    from app.services.plan_capabilities import get_tenant_capabilities
    return get_tenant_capabilities(self)

def storage_pct(self) -> float:
    caps = self.get_capabilities()
    return caps.storage_usage_pct(self.storage_used_bytes or 0)

def storage_near_limit(self) -> bool:
    caps = self.get_capabilities()
    return caps.storage_warning(self.storage_used_bytes or 0)
```

### 3c. Replace the `PLAN_FEATURES` dict + `normalize_plan_name` + `get_plan_features`:
```python
SUBSCRIPTION_PLAN_ORDER = {'Trial': 0, 'Basic': 1, 'Pro': 2, 'Enterprise': 3}
PAID_PLAN_NAMES = frozenset({'Basic', 'Pro', 'Enterprise'})

def normalize_plan_name(plan: str) -> str:
    from app.services.plan_capabilities import get_capabilities
    return get_capabilities(plan).plan_name

def get_plan_features(plan: str) -> dict:
    from app.services.plan_capabilities import get_capabilities
    return get_capabilities(plan).as_dict()
```

---

## Phase 4 — Edit app/models/__init__.py

Add at the bottom:
```python
from app.models.core_additions import MediaUpload, PlanUsageLog  # noqa: F401
```

---

## Phase 5 — Register Middleware in app/__init__.py

After all blueprint registrations (look for `app.register_blueprint`):
```python
# v6.0 — Subscription lifecycle enforcement
from app.middleware.subscription_guard import init_subscription_guard
init_subscription_guard(app)
```

---

## Phase 6 — Wire Upload Routes in app/admin/__init__.py

At the bottom of `app/admin/__init__.py`, before the final `return admin_bp`:
```python
# v6.0 — Quota-enforced upload handlers
from app.admin.upload_handlers import register_upload_routes
register_upload_routes(admin_bp)
```

---

## Phase 7 — Update Admin Billing Plans Route

In `app/admin/__init__.py`, find the `billing_plans` route and update to pass
the new context vars:

```python
@admin_bp.route('/billing/plans')
@login_required
def billing_plans():
    from app.services.plan_capabilities import all_plans_summary
    from app.services.storage_service import get_quota_summary

    tenant = Tenant.query.get(current_user.tenant_id)
    return render_template(
        'admin/billing/plans_v6.html',
        current_plan=tenant.effective_plan() if tenant else 'Trial',
        subscription=next((s for s in tenant.subscriptions if s.is_active()), None) if tenant else None,
        subscription_state=getattr(tenant, 'subscription_state', 'active'),
        quota_summary=get_quota_summary(tenant) if tenant else None,
        plans=all_plans_summary(),
    )
```

---

## Phase 8 — Add Quota Widget to Admin Base Template

In `app/templates/admin/base.html` (or wherever the sidebar renders),
add inside the sidebar nav:

```html
{% from 'admin/partials/_quota_widget.html' import quota_widget %}
{% if quota_summary %}
  {% include 'admin/partials/_quota_widget.html' %}
{% endif %}
```

And expose `quota_summary` from your context processor
(see PATCH_INSTRUCTIONS_core_py.py Change 6).

---

## Phase 9 — Install Pillow (if not already installed)

```bash
pip install Pillow
```

Add to requirements.txt:
```
Pillow>=10.0.0
```

---

## Phase 10 — Configure UPLOAD_BASE_PATH

In your `.env` or `config.py`:
```env
UPLOAD_BASE_PATH=/var/www/portfolio-cms/uploads
```

For Docker, mount this as a volume:
```yaml
volumes:
  - /host/uploads:/var/www/portfolio-cms/uploads
```

---

## Phase 11 — Debug OTP Delivery (if admin OTP still broken)

```bash
flask shell < tools/debug_admin_otp.py
```

The script walks every step of the provider resolution chain and tells you
exactly which step is failing and why.

The fix for the default admin OTP bug (v5.9.2) is already present in
`app/services/password_reset_service.py` via `_send_admin_otp_via_tenant_providers()`.

If OTP still fails:
1. Run the diagnostic above
2. Go to `/admin/email-services` and configure SMTP credentials
3. Click "Test Connection" — it must return success
4. Verify `TenantEmailProvider.active = True` for the smtp row
   (`SELECT * FROM tenant_email_providers WHERE tenant_id = <id>`)

---

## Security Checklist Post-Deployment

- [ ] `UPLOAD_BASE_PATH` is outside the webroot (not under `static/`)
- [ ] Nginx/Gunicorn does NOT serve files from `UPLOAD_BASE_PATH` directly
       without auth checks (use a Flask route with `send_file` instead)
- [ ] `FERNET_KEY` is set in production (encrypts stored SMTP passwords)
- [ ] `ALLOWED_MIME_TYPES` whitelist is the only gate — do NOT trust browser MIME
- [ ] `flask db upgrade` was run BEFORE deploying new code
- [ ] Pillow is installed and `PIL` imports cleanly

---

## Rollback Plan

Migration 0033 has a full `downgrade()`. To roll back:
```bash
flask db downgrade 0032_superadmin_email_providers
```

Then remove the copied files. No existing data is touched by this migration.
