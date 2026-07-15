# Phase 0B — Deterministic database migrations

**Source implementation date:** 2026-07-14  
**Applies to:** core database, tenant-data database, Render, Docker, and local SQLite development

## Outcome

MyPortfolioHub now has exactly one versioned schema owner per bind:

| Bind | Models | Migration history | Version table |
|---|---|---|---|
| Core | tenants, users, auth, billing, providers, settings, messages, themes | `migrations/versions` | `alembic_version` |
| Tenant | profile, skills, projects, reactions, testimonials, services, certificates, work experience | `migrations/tenant/versions` | `alembic_version_tenant` |

`flask db-upgrade-all` acquires an application-specific PostgreSQL advisory
lock, upgrades core first, upgrades tenant second, verifies both heads, and
verifies required tenant tables, columns, and indexes. A failure exits nonzero
before a new web release is started.

The prior production bypass has been removed:

- Render and Docker no longer call `db.create_all()` after an Alembic failure.
- `bootstrap-production-db` now rejects with an error and points to
  `db-upgrade-all`; it cannot stamp a migration that did not run.
- `ensure-tenant-schema` is retained for compatibility, but now runs the tenant
  Alembic history instead of ORM table creation.
- Web startup tenant checks are read-only. The old direct tenant `ALTER TABLE`
  repair code is no longer called.
- Alembic core startup imports model metadata without constructing a second
  Flask application.

## Tenant baseline behavior

`0001_tenant_schema_baseline` supports both deployment shapes:

- Empty tenant database: creates the eight current tenant tables and their
  indexes.
- Existing database created by the old `ensure-tenant-schema`/`create_all`
  path: adopts it additively, adds missing current columns, reconciles named
  indexes, then records the tenant revision only after the migration succeeds.
- Single-Postgres deployment: uses `alembic_version_tenant`, so tenant history
  remains independent even though both binds share one physical database.
- Separate databases: resolves `DIRECT_TENANT_DATABASE_URL` or
  `TENANT_DATABASE_URL` and never assumes core tables exist in the tenant DB.

The baseline downgrade is intentionally non-destructive because it may adopt
pre-existing tenant tables. Roll application code back and forward-fix schema;
do not drop tenant content to reverse a deployment.

## Deployment commands

### Render

The checked-in `render.yaml` runs:

```bash
flask db-upgrade-all &&
flask ensure-default-tenant &&
flask create-superadmin
```

Required database controls:

```dotenv
USE_ORM_BOOTSTRAP_ON_STARTUP=false
ALLOW_CREATE_ALL_BOOTSTRAP_ON_MIGRATION_FAILURE=false
AUTO_ENSURE_TENANT_SCHEMA=false
DB_SSLMODE=require
```

Keep `TENANT_DATABASE_URL` blank only for the documented single-Postgres
deployment. Set it to the second database for physical separation. Use direct
port-5432 URLs for migrations where a provider exposes pooled and direct URLs.

### Docker

The image entrypoint runs `flask db-upgrade-all` when
`RUN_MIGRATIONS=true`. Local Compose sets `DB_SSLMODE=disable` only for its
private, non-TLS PostgreSQL network. The canonical Render Blueprint uses its
`preDeployCommand`, so Render must keep `RUN_MIGRATIONS=false`. Hosted
production must use `DB_SSLMODE=require`.

### Manual verification

```bash
flask db-status
```

The command is read-only and exits nonzero when either current head differs
from its expected head or the tenant schema is missing required objects.

## Mandatory pre-deploy backup

Take transaction-consistent backups immediately before the first Phase 0B
production deployment. Do not log connection strings or proof-file contents.

Separate databases:

```bash
pg_dump --format=custom --no-owner --no-acl "$DIRECT_CORE_DATABASE_URL" \
  --file core-pre-phase0b.dump
pg_dump --format=custom --no-owner --no-acl "$DIRECT_TENANT_DATABASE_URL" \
  --file tenant-pre-phase0b.dump
```

Single database:

```bash
pg_dump --format=custom --no-owner --no-acl "$DIRECT_CORE_DATABASE_URL" \
  --file portfolio-pre-phase0b.dump
```

Record, outside application logs:

- backup object location, size, checksum, creation time, and retention;
- `SELECT version_num FROM alembic_version`;
- tenant head when present: `SELECT version_num FROM alembic_version_tenant`;
- row counts for `tenants`, `users`, `subscriptions`, `payment_submissions`,
  `profile`, `projects`, and `skills`.

Perform a restore test before declaring the backup usable.

## Rehearsal matrix

Run the following on disposable PostgreSQL databases using the exact release
image and environment shape intended for production.

| Scenario | Preparation | Required result |
|---|---|---|
| Empty, single DB | New DB; tenant URL blank | Core and tenant heads reached; second run is a no-op; all required objects exist |
| Empty, separate DBs | New core and tenant DBs | Each history writes only its own version table and model tables |
| Oldest supported backup | Restore the oldest retained supported backup | Upgrade succeeds without losing rows; counts and sampled records reconcile |
| Current production clone | Restore a fresh production backup | Upgrade succeeds; auth, billing, providers, tenant routing, themes, and Administrator plan smoke tests pass |
| Legacy ORM-created tenant schema | Restore a DB with tenant tables but no tenant version table | Additive tenant baseline adopts and verifies the schema without replacing tenant rows |
| Interrupted attempt | Terminate a disposable migration transaction, then rerun | PostgreSQL rolls back transactional DDL or rerun completes guarded additive work; no manual stamp |
| Lock contention | Start two `db-upgrade-all` commands | Second command waits; only one migration sequence runs at a time |
| Forced tenant failure | Revoke tenant DDL permission after core upgrade | Command exits nonzero, new web release does not start, old release remains available |

For each scenario:

```bash
flask db-upgrade-all
flask db-status
flask db-upgrade-all
flask db-status
```

Then compare pre/post counts and run the normal application smoke suite. A
schema that cannot be reconciled must fail closed. Do not resolve it with
`alembic stamp`, `bootstrap-production-db`, or `db.create_all()`.

## Rollback and recovery boundaries

### Application rollback

Render pre-deploy commands run before the new web release. If migration fails,
the new release is not started and the old release continues serving.

Phase 0B migration changes are additive or compatibility-oriented. Roll back
the application image first. Leave the newer schema in place and ship a
reviewed forward fix. Do not run broad automatic downgrades against production.

### Database restore

Restore only when a migration or operator action caused data loss/corruption
and a forward fix is not safe. Stop writes, preserve the failed database for
analysis, restore both databases from the same backup point, verify row counts
and heads, then restart the prior application image.

Example restore to a new database, never over the only copy:

```bash
createdb portfolio_restore_check
pg_restore --clean --if-exists --no-owner --no-acl \
  --dbname portfolio_restore_check portfolio-pre-phase0b.dump
```

### Partially applied historical revision

Do not guess a revision or stamp head. Capture the failing revision and SQL,
compare the live objects with the revision body, restore the pre-deploy backup
or write a single reviewed, idempotent forward repair. Re-run
`flask db-upgrade-all` and `flask db-status` after the repair.

## Verification completed in this workspace

- Python compilation passed for `app`, `migrations`, and `tests`.
- Pure-stdlib Phase 0B regression suite passed all six tests.
- Static topology check found 63 connected core revisions with one base/head.
- Tenant history has one connected baseline revision with one base/head.
- Static model contract check confirms the tenant baseline contains the exact
  eight model tables, their columns, and their indexes.
- Deployment configuration has no callable ORM bootstrap or migration-failure
  fallback.

Live database execution was not possible in the provided workspace because it
contains no PostgreSQL service and the Python environment does not have Flask,
SQLAlchemy, Alembic, or pytest installed. The rehearsal matrix above is a
release gate, not optional post-deploy work.
