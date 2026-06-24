# DATABASE AUDIT REPORT — Portfolio CMS v5.2

**Date:** 2026-06-18

---

## Architecture Summary

Dual-database architecture:
- **Core DB** (`CORE_DATABASE_URL`): tenants, users, subscriptions, billing, activity logs, notifications
- **Tenant DB** (`TENANT_DATABASE_URL`): profile, skills, projects, testimonials, services, inquiries

SQLAlchemy bind key: Core = default, Tenant = `'tenant'`

---

## Indexes Audit

### Migration 0025 — Critical Indexes (already present)

The following indexes were added in migration `0025_add_critical_indexes.py`:

| Table | Columns | Index Name | Unique |
|-------|---------|------------|--------|
| users | tenant_slug | ix_user_tenant_slug | No |
| users | email, tenant_slug | ix_user_email_tenant | No |
| profile | tenant_slug | ix_profile_tenant_slug | No |
| projects | tenant_slug, status | ix_projects_tenant_status | No |
| skills | tenant_slug | ix_skills_tenant_slug | No |
| testimonials | tenant_slug, is_visible | ix_testimonials_tenant_visible | No |
| services | tenant_slug, is_visible | ix_services_tenant_visible | No |
| subscriptions | tenant_id, status | ix_subscriptions_tenant_status | No |
| tenants | slug | ix_tenants_slug | Yes |

### Recommended Additional Indexes (post-deployment)

```sql
-- Activity log queries (common in admin panel)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activity_log_tenant_created
  ON activity_log(tenant_slug, created_at DESC);

-- Inquiry/message queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_inquiries_tenant_read
  ON inquiries(tenant_slug, is_read);

-- Project slug lookups (public portfolio pages)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_projects_slug
  ON projects(slug);
```

---

## Model Relationships Audit

### Core DB Models (SQLAlchemy default bind)

| Model | Table | Key Relationships | tenant_id FK |
|-------|-------|-------------------|--------------|
| Tenant | tenants | has_many Users, Subscriptions | — |
| User | users | belongs_to Tenant | FK tenants.id ✓ |
| Subscription | subscriptions | belongs_to Tenant | FK tenants.id ✓ |
| ActivityLog | activity_log | belongs_to Tenant | FK tenants.id ✓ |
| Inquiry | inquiries | belongs_to Tenant | FK tenants.id ✓ |
| SubscriptionNotification | subscription_notifications | belongs_to Tenant, Subscription | FK tenants.id ✓ |

### Tenant DB Models (bind_key='tenant')

| Model | Table | tenant_slug Filter Required |
|-------|-------|----------------------------|
| Profile | profile | ✓ (filter_by tenant_slug) |
| Skill | skills | ✓ |
| Project | projects | ✓ (via `published_for_tenant()`) |
| Testimonial | testimonials | ✓ |
| Service | services | ✓ |

---

## Migration Chain Status

### Core DB (managed by Alembic)
- Head: migration 0025 (critical indexes)
- Chain: 001 → 0002 → 0003 → ... → 0025
- ⚠ **Two `0003_` files** exist (`0003_add_license_and_trial_columns.py` and `0003_billing_v3_3.py`) — this causes an ambiguous revision chain. The Alembic `down_revision` in each must be verified.

### Tenant DB (managed by `flask ensure-tenant-schema`)
- No Alembic history — tables created via `db.create_all(bind_key='tenant')`
- ⚠ **Not version-controlled** — schema changes must be applied manually or via `ensure-tenant-schema`
- **Recommendation:** Convert to `flask db init --multidb` for proper dual-DB migration support

---

## Connection Pool Configuration

### Production (ProductionConfig)
```python
SQLALCHEMY_ENGINE_OPTIONS = {
    'poolclass': NullPool,  # Required for Supabase PgBouncer
    'pool_pre_ping': True,
    'connect_args': {
        'sslmode': 'require',
        'connect_timeout': 10,
        'options': '-c statement_timeout=30000',
    },
}
```

**NullPool is intentional** for Supabase (PgBouncer) compatibility. PgBouncer manages its own connection pool; SQLAlchemy pooling on top causes "prepared statement does not exist" errors.

### Development (DevelopmentConfig — fixed)
```python
SQLALCHEMY_ENGINE_OPTIONS = {
    'pool_pre_ping': True,  # Detects stale SQLite connections
}
```

---

## Data Integrity Concerns

1. **Duplicate migration 0003** — Resolve before next `flask db upgrade` to avoid Alembic errors
2. **Tenant DB missing from migrations** — Run `flask ensure-tenant-schema` after every deployment
3. **profile.tenant_id nullable** — Profile rows created without a tenant_id will break tenant isolation queries

