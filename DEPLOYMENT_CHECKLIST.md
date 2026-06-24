# DEPLOYMENT CHECKLIST — Portfolio CMS v5.2 → Render Production

**Date:** 2026-06-18  
Status key: ✓ = complete, ⚠ = action required, 🔄 = run on each deploy

---

## PRE-DEPLOYMENT (One-Time Setup)

### Secrets & Environment Variables

- [ ] ⚠ **Rotate SECRET_KEY** — was exposed in committed .env (see SECRETS_ROTATION_REPORT.md)
- [ ] ⚠ **Rotate FERNET_KEY** — was exposed; re-encrypt stored API keys after rotation
- [ ] ⚠ **Change SUPERADMIN_PASSWORD** from default `superadmin12345!@`
- [ ] ⚠ **Regenerate BETTERSTACK_HEARTBEAT_URL** — was exposed
- [ ] Set `CORE_DATABASE_URL` in Render dashboard (PostgreSQL #1)
- [ ] Set `TENANT_DATABASE_URL` in Render dashboard (PostgreSQL #2)
- [ ] Set `MAILERSEND_API_KEY` + `MAILERSEND_FROM_EMAIL`
- [ ] Set `PAYMONGO_SECRET_KEY` + `PAYMONGO_WEBHOOK_SECRET` (if billing enabled)
- [ ] Set `REDIS_URL` (auto-injected from `portfolio-redis` service)
- [ ] Set `APP_BASE_URL` = your Render domain
- [ ] Set `FERNET_KEY` (must be valid Fernet key format)
- [ ] Set `SENTRY_DSN` (optional but recommended)

### Database Setup

- [ ] Create Core PostgreSQL database in Render
- [ ] Create Tenant PostgreSQL database in Render
- [ ] Set both URLs in Render environment
- [ ] If using Supabase: set `DIRECT_CORE_DATABASE_URL` to port-5432 URL for Alembic

### Redis Setup

- [ ] Redis service (`portfolio-redis`) defined in `render.yaml` — auto-provisioned
- [ ] Verify `REDIS_URL` is auto-injected via `fromService` in `render.yaml` ✓

---

## EACH DEPLOYMENT

### Pre-Deploy Commands (in render.yaml)

```yaml
preDeployCommand: >
  flask db upgrade &&
  flask ensure-tenant-schema &&
  flask ensure-default-tenant
```

- [ ] 🔄 `flask db upgrade` — applies Alembic migrations to core DB
- [ ] 🔄 `flask ensure-tenant-schema` — creates/verifies tenant DB tables (profile, skills, etc.)
- [ ] 🔄 `flask ensure-default-tenant` — ensures default tenant and profile exist

### Post-Deploy Verification

- [ ] 🔄 `GET /health` returns `{"status": "ok"}` with HTTP 200
  - Checks: core DB, tenant DB, Redis
- [ ] 🔄 `GET /heartbeat` returns `{"status": "healthy"}` with HTTP 200
- [ ] 🔄 Root `/` renders portfolio (not redirect, not error)
- [ ] 🔄 `/admin/` requires login
- [ ] 🔄 `/superadmin/` requires login

---

## GUNICORN CONFIGURATION

Current (`render.yaml`):
```
--workers 1 --threads 4 --timeout 120 --keep-alive 5
```

- ✓ `--workers 1`: Required for APScheduler singleton (renewal check)
- ✓ `--threads 4`: Provides concurrency within the single worker
- ✓ `--timeout 120`: Adequate for PayMongo webhook processing
- ✓ `--keep-alive 5`: Reasonable for Render's load balancer

**If scaling beyond 1 worker:**
1. Remove `ENABLE_SCHEDULER=true` from web service env vars
2. Uncomment the `cron` service block in `render.yaml`
3. The PG advisory lock (`scheduler_lock.py`) provides an additional safety net

---

## HEALTH CHECK

Render health check: `GET /health`

Expected response (HTTP 200):
```json
{
  "status": "ok",
  "checks": {
    "database_core": {"status": "ok"},
    "database_tenant": {"status": "ok", "missing_tables": []},
    "redis": {"status": "ok"}
  }
}
```

If `database_tenant.missing_tables` is non-empty → run `flask ensure-tenant-schema`  
If `redis.status = "fallback"` → REDIS_URL is misconfigured; fix in Render dashboard

---

## MONITORING

- BetterStack: configure to monitor `/heartbeat` (returns 200 when healthy, 503 when DB is down)
- Render metrics: watch memory and CPU — `--workers 1 --threads 4` means 1 process, 4 threads
- Sentry: set `SENTRY_DSN` for error tracking in production

---

## ROLLBACK PROCEDURE

If deployment fails:
1. Render dashboard → Manual Deploy → select previous successful deploy
2. If DB migration caused issues: `flask db downgrade -1`
3. The `migrations/rollback.sql` file contains manual rollback SQL for critical migrations

