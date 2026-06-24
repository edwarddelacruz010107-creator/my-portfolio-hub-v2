# AUDIT REPORT — Portfolio CMS v5.2 Production Hardening

**Date:** 2026-06-18  
**Auditor:** Senior Principal Flask Architect / Security Engineer  
**Target:** portfolio_cms_v5_2_remediated  
**Scope:** Render production readiness, security hardening, multi-tenant isolation

---

## CRITICAL FINDINGS

### 1. Migration Drift (CRITICAL — FIXED)

**Finding:** `migrations/env.py` and `app/utils/db_config.py` used `DIRECT_DATABASE_URL`, `DEV_DATABASE_URL`, and `DATABASE_URL` — none of which are set in production. Production uses `CORE_DATABASE_URL` + `TENANT_DATABASE_URL` (v5.0 dual-DB architecture). `flask db upgrade` was migrating the wrong database (or failing silently).

**Fix:** `db_config.py` updated with full priority chain: `DIRECT_CORE_DATABASE_URL` → `DIRECT_DATABASE_URL` → `CORE_DATABASE_URL` → `DEV_CORE_DATABASE_URL` → `DEV_DATABASE_URL` → `DATABASE_URL`. `migrations/env.py` updated to call `get_database_url()` which now resolves correctly.

---

### 2. Duplicate `/health` Endpoint (CRITICAL — FIXED)

**Finding:** Two `/health` routes were registered:
- `app/__init__.py`: registered `@app.route("/health")` checking only core DB
- `app/heartbeat/__init__.py`: registered `/health` checking core DB + tenant DB + Redis + scheduler

The heartbeat blueprint's richer `/health` was being shadowed by the simpler one in `__init__.py`.

**Fix:** Removed the duplicate `@app.route("/health")` from `app/__init__.py`. The heartbeat blueprint's comprehensive `/health` is now the only endpoint.

---

### 3. Render Health Check Wrong Path (HIGH — FIXED)

**Finding:** `render.yaml` used `healthCheckPath: /heartbeat` which only checks the core database. Tenant DB failure was invisible to Render.

**Fix:** Changed to `healthCheckPath: /health` which checks core DB, tenant DB (including missing tables), Redis, and reports scheduler status.

---

### 4. Secrets Exposed in .env (CRITICAL)

**Finding:** `.env` file committed to repository contained live production secrets:
- `SECRET_KEY` with actual value `h6-wa5lUi0vbQaIRlIHMur1cCBAo1voFH40QDxwkbHU`
- `FERNET_KEY` with actual value
- `SUPERADMIN_PASSWORD` = `superadmin12345!@`
- `BETTERSTACK_HEARTBEAT_URL` (live monitoring URL)
- `ADMIN_EMAIL` (real email address)

**Fix:** All secret values redacted in `.env`. New `.env.example` created with placeholder values. See `SECRETS_ROTATION_REPORT.md` for rotation instructions.

---

### 5. Stray Flask Instance in config.py (HIGH — FIXED)

**Finding:** `config.py` created a `Flask()` instance at module scope (`app = Flask(...)`) purely for URL building context. This instance is never used and creates a stale Flask app state at import time, interfering with `create_app()`.

**Fix:** Removed the stray Flask instance. The module-level `app` variable in `config.py` is no longer created.

---

### 6. DevelopmentConfig PostgreSQL SSL connect_args on SQLite (HIGH — FIXED)

**Finding:** `DevelopmentConfig.SQLALCHEMY_ENGINE_OPTIONS` included `'sslmode': 'require'` and other PostgreSQL-specific `connect_args`. When using SQLite (the default dev database), these args cause `sqlite3.OperationalError: no such keyword: sslmode`.

**Fix:** `DevelopmentConfig.SQLALCHEMY_ENGINE_OPTIONS` simplified to `{'pool_pre_ping': True}`. PostgreSQL-specific settings remain in `ProductionConfig`.

---

### 7. Scheduler Duplicate Execution Risk (HIGH — FIXED)

**Finding:** `_init_scheduler()` checked `ENABLE_SCHEDULER` env var but provided no distributed lock. With `--workers N > 1`, all N workers would start APScheduler, causing N duplicate renewal emails per trigger.

**Fix:** Added `app/services/scheduler_lock.py` implementing:
- In-process `threading.Lock` (same-process race prevention)
- PostgreSQL advisory lock via `pg_try_advisory_lock(7919)` (multi-worker race prevention)

---

### 8. Conflicting SESSION_COOKIE_* Settings (MEDIUM — FIXED)

**Finding:** `BaseConfig` defined `SESSION_COOKIE_SAMESITE = "Lax"` then immediately redefined it as `SESSION_COOKIE_SAMESITE = 'Strict'` (and same for `REMEMBER_COOKIE_SAMESITE`). Python class body evaluation means `'Strict'` wins — which breaks OAuth flows and Render's reverse proxy cookie handling.

**Fix:** Removed duplicate block. Single consistent definition remains: `SESSION_COOKIE_SAMESITE = "Lax"`, `SESSION_COOKIE_SECURE = True` (production), `REMEMBER_COOKIE_SECURE = True` (production).

---

### 9. Garbage Directories (MEDIUM — FIXED)

**Finding:** Repository contained malformed directory names created by previous automation:
- `{app` 
- `{migrations,models,services,routes,templates`
- `{migrations,models,services,routes,templates/{superadmin,admin`
- `app/{services,auth,utils,superadmin}`

These are the results of shell brace expansion in automation scripts without proper quoting.

**Fix:** All garbage directories removed.

---

## VERIFIED FINDINGS — NO ACTION REQUIRED

### ProxyFix (VERIFIED OK)

`app/__init__.py` correctly applies `ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)` before Talisman and before blueprint registration.

### Redis Graceful Fallback (VERIFIED OK)

`resolve_limiter_storage_uri()` in `app/__init__.py` pings Redis with a 2-second timeout before committing to it. Falls back to `memory://` with a warning. Application boots without Redis.

### Flask-Talisman HTTPS Enforcement (VERIFIED OK)

`Talisman(app, force_https=not app.debug)` — correctly disabled in dev, enabled in production.

### Session Security (VERIFIED OK after fix)

After removing the duplicate block:
- `SESSION_COOKIE_SECURE = True` (production)
- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_SAMESITE = "Lax"`
- `REMEMBER_COOKIE_SECURE = True` (production)
- `REMEMBER_COOKIE_HTTPONLY = True`

### Tenant Isolation (VERIFIED OK)

All `Profile`, `Project`, `Skill`, `Testimonial`, `Service`, `Inquiry` queries in tenant blueprints filter by `tenant_slug`. The `TenantGuard.validate()` runs on every request via `before_request`. The user loader verifies `session['tenant_slug']` matches `user.tenant_slug`.

### CSP (VERIFIED ACCEPTABLE)

CSP uses `'unsafe-inline'` for `style-src` only (required by template inline styles). `script-src` is `'self'` plus CDN allowlist with no `'unsafe-inline'`. This is the standard pattern for Flask/Jinja2 apps without a build pipeline.

### Gunicorn Configuration (VERIFIED OK)

`render.yaml` runs: `--workers 1 --threads 4 --timeout 120 --keep-alive 5`  
Single worker is correct for APScheduler safety. 4 threads provides concurrency. 120s timeout is appropriate for payment webhooks.

---

## REMAINING RECOMMENDATIONS (Post-deployment)

1. **Rotate all secrets** from the committed `.env` — see `SECRETS_ROTATION_REPORT.md`
2. **Enable `flask db upgrade` for tenant DB** — run `flask ensure-tenant-schema` to create tenant tables until a proper `--multidb` Alembic split is implemented
3. **Set `DIRECT_CORE_DATABASE_URL`** to the port-5432 direct Supabase URL for reliable Alembic migrations
4. **Monitor `/health`** via Render's health check — it now validates both databases
5. **Scale beyond 1 worker only after testing** the PG advisory lock behavior

