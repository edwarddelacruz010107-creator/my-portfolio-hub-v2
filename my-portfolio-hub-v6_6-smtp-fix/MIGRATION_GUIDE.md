# Portfolio CMS v5.0 — Dual-Database Migration Guide

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Render Web Service (single Flask process)                    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Flask App                                           │    │
│  │                                                      │    │
│  │  Blueprints:  auth / admin / superadmin / tenant    │    │
│  │               main / heartbeat / webhooks           │    │
│  └──────────────┬────────────────────┬────────────────┘    │
│                 │                    │                        │
│          default bind          "tenant" bind                 │
│          CORE_DATABASE_URL     TENANT_DATABASE_URL           │
│                 │                    │                        │
└─────────────────┼────────────────────┼────────────────────────┘
                  │                    │
     ┌────────────▼───────┐  ┌────────▼──────────┐
     │   core_db          │  │  tenant_data_db    │
     │                    │  │                    │
     │  tenants           │  │  profile           │
     │  users             │  │  skills            │
     │  subscriptions     │  │  projects          │
     │  payment_methods   │  │  testimonials      │
     │  payment_submit.   │  │  services          │
     │  webhook_events    │  │  tenant_form_sett. │
     │  platform_settings │  │                    │
     │  tenant_comm_sett. │  │  ALL rows scoped   │
     │  password_reset_.. │  │  by tenant_id      │
     │  global_email_conf │  │  (no FK to core)   │
     │  inquiries         │  │                    │
     │  inquiry_replies   │  │                    │
     │  subscription_not. │  │                    │
     │  activity_log      │  │                    │
     └────────────────────┘  └────────────────────┘
```

## Model Classification

### core_db (SQLALCHEMY_DATABASE_URI = CORE_DATABASE_URL)
| Model | Table | Rationale |
|---|---|---|
| Tenant | tenants | Registry — the authority for tenant existence |
| User | users | Auth — FK to tenants |
| Subscription | subscriptions | Billing — FK to tenants |
| WebhookEvent | webhook_events | PayMongo idempotency |
| PaymentMethod | payment_methods | Billing config |
| PaymentInstruction | payment_instructions | Legacy billing (deprecated) |
| PaymentSubmission | payment_submissions | Proof of payment |
| PlatformSetting | platform_settings | Superadmin toggles |
| TenantCommunicationSettings | tenant_communication_settings | Encrypted per-tenant SMTP |
| PasswordResetOTP | password_reset_otps | All OTP flows |
| GlobalEmailConfig | global_email_config | Superadmin email config |
| Inquiry | inquiries | Contact msgs + SA↔tenant messaging |
| InquiryReply | inquiry_replies | Thread replies |
| SubscriptionNotification | subscription_notifications | Billing alerts |
| ActivityLog | activity_log | Cross-tenant audit trail |

### tenant_data_db (SQLALCHEMY_BINDS["tenant"] = TENANT_DATABASE_URL)
| Model | Table | Rationale |
|---|---|---|
| Profile | profile | Portfolio content, 1-per-tenant |
| Skill | skills | Portfolio content |
| Project | projects | Portfolio content |
| Testimonial | testimonials | Portfolio content |
| Service | services | Portfolio content |
| TenantFormSettings | tenant_form_settings | Contact form config |

## Cross-DB Relationship Rule

SQLAlchemy `db.relationship()` and `db.ForeignKey()` **cannot span two physical databases**.

**Prohibited pattern:**
```python
# WRONG — will raise OperationalError at runtime
class Profile(db.Model):
    __bind_key__ = 'tenant'
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'))  # tenants is in core_db!
```

**Correct pattern:**
```python
# CORRECT — integer column, no FK constraint
class Profile(db.Model):
    __bind_key__ = 'tenant'
    tenant_id = db.Column(db.Integer, nullable=False, index=True)  # no ForeignKey()
```

Referential integrity for tenant_id in tenant_data_db is enforced at the **application layer**:
1. At tenant creation time — Profile row is created with valid tenant_id
2. On every query — `filter_by(tenant_id=session['tenant_id'])`
3. At tenant deletion time — explicit cleanup via `Profile.query.filter_by(tenant_id=id).delete()`

## File Changes Required

### New files (delivered in this migration package)
```
app/models/core.py              — All core_db models
app/models/tenant_data.py       — All tenant_data_db models
app/models/__init__.py          — Updated registry
config.py                       — Dual-DB bind configuration
app/tenant_isolation.py         — Centralized query helpers
scripts/migrate_data.py         — Data migration script
scripts/seed.py                 — Fresh-deployment seeder
scripts/rollback.py             — Emergency rollback
migrations/core/env.py          — Alembic env for core_db
migrations/tenant/env.py        — Alembic env for tenant_data_db
```

### Files to modify in your codebase

**app/__init__.py**
- Replace `from app.models.portfolio import ...` with `from app.models import ...`
- Replace `from app.models.user import User` with `from app.models import User`
- Replace `db.create_all()` with bind-aware calls (see `__init__.patch.py`)

**app/admin/__init__.py** — 1,949 lines
- Replace `Profile.query.filter_by(tenant_slug=...)` → `Profile.query.filter_by(tenant_id=session['tenant_id'])`
- Replace `Project.query.filter_by(tenant_slug=...)` → `Project.query.filter_by(tenant_id=session['tenant_id'])`
- Apply same pattern to Skill, Testimonial, Service, TenantFormSettings
- Prefer importing from `app.tenant_isolation` (e.g., `tenant_projects()`)

**app/superadmin/__init__.py** — 2,541 lines
- Queries against tenant data tables need explicit `tenant_id` filter (not slug)
- Superadmin cross-tenant listing: `Project.query.all()` is still valid (returns all tenants)

**app/main/__init__.py** — public portfolio routes
- Replace `Profile.query.filter_by(tenant_slug=slug).first()` with:
  ```python
  from app.tenant_isolation import resolve_public_portfolio
  tenant, profile = resolve_public_portfolio(slug)
  ```

**app/auth/__init__.py** — login route
- After successful login, set `session['tenant_id'] = user.tenant_id`
- Already sets `session['tenant_slug']` — keep both

**app/services/*.py**
- Any service that queries portfolio models must accept/use `tenant_id`, not `tenant_slug`
- `billing.py`, `manual_billing.py` — query Subscription, PaymentSubmission (core_db, no change needed)
- `forms.py` — queries TenantFormSettings; update to `filter_by(tenant_id=...)`

## Migration Procedure

### Phase 1: Preparation
```bash
# 1. Provision two PostgreSQL databases on Render
#    Name them: portfolio-core-db and portfolio-tenant-db

# 2. Set environment variables
export CORE_DATABASE_URL=postgresql://...
export TENANT_DATABASE_URL=postgresql://...

# 3. Run schema migrations
cd migrations/core   && alembic upgrade head
cd migrations/tenant && alembic upgrade head
```

### Phase 2: Data Migration (with write-traffic paused)
```bash
# Dry run first
export DATABASE_URL=<your-existing-db-url>
python scripts/migrate_data.py --dry-run

# Verify counts are correct, then run live migration
python scripts/migrate_data.py
```

### Phase 3: Verification
```bash
# Verify row counts match between source and destinations
python - <<'EOF'
from sqlalchemy import create_engine, text
import os

src    = create_engine(os.environ['DATABASE_URL'])
core   = create_engine(os.environ['CORE_DATABASE_URL'])
tenant = create_engine(os.environ['TENANT_DATABASE_URL'])

for table, engine in [
    ('tenants', core), ('users', core), ('subscriptions', core),
    ('profile', tenant), ('projects', tenant), ('skills', tenant),
]:
    with src.connect() as c:
        src_count = c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
    with engine.connect() as c:
        dst_count = c.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
    status = '✓' if src_count == dst_count else '❌ MISMATCH'
    print(f'{status} {table}: {src_count} → {dst_count}')
EOF
```

### Phase 4: Deploy
```bash
# Set new env vars on Render (remove old DATABASE_URL after confirming)
# CORE_DATABASE_URL=postgresql://...
# TENANT_DATABASE_URL=postgresql://...

# Deploy the new code
git push render main
```

### Phase 5: Rollback (if needed)
```bash
python scripts/rollback.py --dry-run
python scripts/rollback.py
```

## Security Audit

### Tenant Isolation Checklist
Every route that returns tenant portfolio data MUST:

- [ ] Set `session['tenant_id']` at login
- [ ] Use `@tenant_required` decorator OR manually call `_require_tenant_id()`
- [ ] Filter ALL tenant_data_db queries by `tenant_id=session['tenant_id']`
- [ ] Use `get_project_or_403(id)` (and similar helpers) for single-object lookups
- [ ] Never expose raw IDs from URL params without ownership verification

### Attack Vectors Addressed

| Threat | Mitigation |
|---|---|
| IDOR (Insecure Direct Object Reference) | `get_*_or_403()` helpers check ownership |
| Cross-tenant data leak | `filter_by(tenant_id=...)` on every query |
| Session fixation | `session['tenant_id']` is set only after authentication |
| FK bypass | No cross-DB FKs; app-layer enforcement |
| Enumeration via 404 | `get_*_or_403()` returns 403 (not 404) on wrong-tenant access |

## Performance Recommendations

1. **Connection pool**: Both DBs use NullPool in production (Render PgBouncer
   transaction mode). If you switch to a dedicated Postgres without PgBouncer,
   use QueuePool with `pool_size=5, max_overflow=10`.

2. **tenant_id indexes**: All tenant_data_db tables have `index=True` on
   `tenant_id`. Verify with `EXPLAIN ANALYZE` after migration.

3. **Two-query public portfolio**: The `resolve_public_portfolio(slug)` pattern
   does two round-trips (core → tenant_data). Cache the Tenant lookup:
   ```python
   from flask_caching import cache
   @cache.memoize(timeout=300)
   def get_tenant_by_slug(slug):
       return Tenant.query.filter_by(slug=slug, status='active').first()
   ```

4. **Avoid N+1 on superadmin tenant list**: When listing all tenants with
   subscription status in the superadmin panel, use a single JOIN query in
   core_db (Tenant + Subscription) rather than lazy-loading per row.

## Scaling to Thousands of Tenants

The tenant_data_db is horizontally scalable because:
- All tables partition naturally on `tenant_id`
- No cross-tenant JOINs exist in the hot path
- Future: add `PARTITION BY RANGE (tenant_id)` on large tables
- Future: shard by tenant_id range across multiple TENANT_DATABASE_URLs
  (requires routing middleware in `app/tenant_isolation.py`)
