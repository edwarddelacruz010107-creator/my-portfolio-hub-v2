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
- The canonical Render target uses the Dockerfile, runs non-root under `tini`, downloads ClamAV signatures at build time, fails when the required scanner is unavailable, and probes `/readyz`.
- Runtime/customer uploads are excluded from source control, Docker build context, and the release archive; production media must use configured durable storage.
- `release_evidence/` contains the source gate, DOM-sink audit, final QA decision, and CycloneDX 1.5 SBOM.

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

1. Create a new Blueprint service from `render.yaml` using the Docker runtime.
2. Set every `sync: false` secret, including `SECRET_KEY`, `FERNET_KEY`, `CORE_DATABASE_URL`, `SUPERADMIN_EMAIL`, and the first-deploy `SUPERADMIN_PASSWORD`.
3. Configure Redis and durable object storage. Do not use the container filesystem for customer media in production.
4. Deploy. The pre-deploy command runs `db-upgrade-all`, `ensure-default-tenant`, and `create-superadmin`; any failure aborts the rollout.
5. Verify `/livez` and `/readyz`, log in once, rotate the Superadmin password, then remove `SUPERADMIN_PASSWORD`.

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
