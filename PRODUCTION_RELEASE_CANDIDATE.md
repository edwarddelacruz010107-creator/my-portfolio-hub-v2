# Production release candidate

**Candidate date:** 2026-07-15  
**Source status:** ready for exact-image staging  
**Production promotion:** not approved until the gates below pass

## What is closed in source

- A fresh/empty SQLite or PostgreSQL database cannot reach normal request handling. Required tables, both Alembic heads, and tenant model indexes/columns are checked read-only. Production fails startup; development returns a controlled 503 setup page.
- Windows local setup is one command: `.\setup-local.ps1 -Start`. It upgrades core and tenant migration histories, verifies them, creates the empty default workspace, and starts the server.
- Production never uses `db.create_all()` or stamps an unapplied migration. The legacy `init-db` command rejects with migration instructions.
- CSP has no `unsafe-inline` directive. Executable/style blocks use nonces; audited legacy attributes use exact response-specific hashes. Iconify runtime and all referenced icons are self-hosted and pinned.
- User, provider, and database strings were removed from risky HTML sinks. The remaining 46 first-party occurrences are classified and locked by an exact digest in `tools/dom_sink_gate.py`.
- The canonical Render target uses the Dockerfile, runs non-root under `tini`, downloads and verifies ClamAV signatures in the 2 GiB runtime, fails when the required scanner is unavailable, and probes `/readyz`.
- Runtime/customer uploads are excluded from source control, Docker build context, and the release archive; production media must use configured durable storage.
- `release_evidence/` contains the source gate, DOM-sink audit, final QA decision, and CycloneDX 1.5 SBOM.

## Render migration hotfix — 2026-07-15

The first production rehearsal exposed an invalid constraint placement in core
migration `0057`: `ck_ledger_backfill_disposition` referenced `disposition`
while PostgreSQL was creating `financial_audit_events`, which has no such
column. The constraint now belongs to `ledger_backfill_items`, matching the
ORM model. A focused AST regression test verifies both the owning table and
column, and a scan of all inline migration check constraints found no other
table/column mismatch.

The failed PostgreSQL Alembic transaction should remain at `0056`; redeploy the
corrected image and let `flask db-upgrade-all` retry normally. Do not stamp the
database, delete the Alembic version table, or create the ledger tables by hand.

## Render ClamAV memory hotfix — 2026-07-15

The next rehearsal used an older archive and exposed two independent scanner
startup failures: `freshclam` exhausted the 512 MiB Render build/runtime limit,
and the non-root runtime could not write `/var/log/clamav/freshclam.log`. The
current image no longer downloads signatures during the memory-bounded build,
removes file logging from `freshclam`, owns both ClamAV data/log directories,
and initializes signatures only for the Gunicorn runtime—not the pre-deploy
migration command. The canonical Render service is now `plan: standard` because
inline ClamAV is not supported within the 512 MiB Free/Starter limit.

## Local recovery for `no such table: tenants`

Stop the server, then run from PowerShell in the project directory:

```powershell
.\setup-local.ps1 -Start
```

The manual equivalent is:

```powershell
$env:FLASK_ENV = "development"
python -m flask --app run.py setup-local
python -m flask --app run.py db-status
python run.py
```

Do not delete or stamp migration version tables to work around an error.

## Render deployment

1. Create or sync the Blueprint service from `render.yaml` using Docker and the declared Standard 2 GiB service tier.
2. Set every `sync: false` secret, including `SECRET_KEY`, `FERNET_KEY`, `CORE_DATABASE_URL`, `SUPERADMIN_EMAIL`, and the first-deploy `SUPERADMIN_PASSWORD`.
3. Keep `RUN_MIGRATIONS=false`; the Render pre-deploy command is the single migration owner. Configure Redis and durable object storage. Do not use the container filesystem for customer media in production.
4. Deploy. The pre-deploy command runs `db-upgrade-all`, `ensure-default-tenant`, and `create-superadmin`; any failure aborts the rollout.
5. Verify `/livez` and `/readyz`, log in once, rotate the Superadmin password, then remove `SUPERADMIN_PASSWORD`.

If Redis logs `Name or service not known`, replace `REDIS_URL` with the current
internal URL from the Render Key Value instance and confirm the web service and
Key Value instance are in the same workspace and region. A stale Redis hostname
is non-fatal with one worker, but caching and shared rate limiting remain
degraded until it is corrected.

If an existing Render service uses the native Python runtime, create a new
Docker service and move traffic after verification; Render runtime types cannot
be changed in place.

## Mandatory staging-to-production gates

- Build the exact image and run Python dependency, SAST, secret, and container CVE scans with no unresolved Critical/High findings.
- Apply both migration histories to an empty database, oldest supported backup, current clone, and interrupted-upgrade clone; record counts/checksums and rollback results.
- Run all runtime-dependent tests with PostgreSQL and Redis, including tenant/role negative authorization, CSRF, webhook replay, concurrent idempotency, and multi-worker behavior.
- Exercise malware scanning with a clean file, EICAR test file, timeout, missing executable, bad signature database, and quarantine cleanup.
- Run browser/CSP, keyboard, axe, zoom, reduced-motion, responsive visual, and performance/load/soak matrices.
- Restore backups/PITR for both database histories and private object metadata; record RPO/RTO.
- Rehearse rolling deploy and rollback with old/new workers concurrently.
- Complete an authorized staging penetration test for tenant isolation, auth recovery, custom hosts, impersonation, uploads/exports, XSS/SSRF/CSRF, webhooks, and abuse controls.

Production promotion requires attached evidence and an explicit release-owner decision; source completion alone is not that decision.
