# migration_cleanup.md — MED-03: RESOLVED (correcting a stale diagnosis)

## Original diagnosis (WRONG — do not act on it)

This doc previously claimed `tenant_form_settings` was a dead table with no
`TenantFormSettings` SQLAlchemy model and nothing reading it, recommending
deletion of the migration entirely.

**That was factually incorrect at the time it was written, and is
definitely incorrect now.** `app/models/tenant_form_settings.py` defines
the canonical `TenantFormSettings` model, and it is load-bearing:
`contact_service.py`, `forms.py`, several admin/superadmin routes, and
`tenant_isolation.py` all read/write it directly. Deleting the table or
the migration that creates it would break tenant contact-form routing in
production.

## What was actually true

The confusion was two competing files with the same name:

- `migrations/versions/0022_tenant_form_settings.py` — the **real**,
  correctly Alembic-chained migration (`0021 → 0022 → 0023`). It's
  defensive: checks `inspector.has_table('tenant_form_settings')` before
  creating, because `0001_initial_schema.py` already creates the table.
  This is the one that actually runs on `flask db upgrade` and it is
  correct.
- `migrations/0022_tenant_form_settings.py` + `.sql` — an earlier,
  unconditional-`CREATE TABLE` draft sitting **outside** `versions/`.
  Alembic's `ScriptDirectory` only scans `versions/`, so these never ran
  and never will. This is what the original diagnosis was actually
  looking at, and mistook for the only definition of the table.

## Resolution (this pass)

Deleted the two orphaned duplicate files outside `versions/`. The real
migration in `versions/0022_tenant_form_settings.py` is untouched and
remains the authoritative source of this table. No schema change, no data
risk — the deleted files never executed.

If you're auditing old docs like this one in the future: verify against
current code before acting on a stale recommendation, even one written by
a prior audit pass. This one was confidently wrong.
