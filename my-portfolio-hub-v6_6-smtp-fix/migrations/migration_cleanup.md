# migration_cleanup.md — MED-03: 0022_tenant_form_settings orphan migration

## Problem

`migrations/0022_tenant_form_settings.sql` creates a `tenant_form_settings` table:
```sql
CREATE TABLE IF NOT EXISTS tenant_form_settings (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ...
    form_provider   VARCHAR(20) ...
    basin_endpoint  TEXT ...
    ...
);
```

However, `form_provider` and `basin_endpoint` are **already columns on the `tenants`
table itself** (models/portfolio.py lines 102–103):
```python
form_provider  = db.Column(db.String(20),  nullable=False, default='internal', index=True)
basin_endpoint = db.Column(db.Text,         nullable=True)
```

There is **no** `TenantFormSettings` SQLAlchemy model anywhere in the codebase.
Running `0022_tenant_form_settings.sql` creates a dead table that:
- Is never queried by the application
- Duplicates data that belongs on `tenants`
- Will confuse future developers

## Correct Decision

**Option A (Recommended): Delete the migration file, keep tenants columns.**

The `Tenant` model already carries `form_provider` and `basin_endpoint`.
Basin and form routing code in `basin_service.py` reads directly from `tenant.*`.
No code reads `tenant_form_settings` at all.

Action:
1. Delete `migrations/0022_tenant_form_settings.sql`.
2. If the migration was already run against production, drop the table:
   ```sql
   DROP TABLE IF EXISTS tenant_form_settings;
   ```
3. Do NOT add the Alembic migration stamp for `0022`.

**Option B: Create the model and migrate data there (not recommended)**

Only choose this if you want per-tenant form settings to be separately
manageable with a UI. This requires:
1. A `TenantFormSettings` SQLAlchemy model
2. Wiring `basin_service.get_tenant_form_config()` to query `TenantFormSettings`
   instead of `Tenant`
3. Admin UI for managing per-tenant form settings
4. Data migration from `tenants.form_provider` / `tenants.basin_endpoint`

This adds complexity without clear benefit given the current architecture.

## Recommendation

Go with Option A. Delete `0022_tenant_form_settings.sql`.
