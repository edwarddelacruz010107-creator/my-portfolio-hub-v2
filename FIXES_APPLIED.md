# FIXES APPLIED — Portfolio CMS v5.2 Production Hardening

**Applied:** 2026-06-18

## Summary

| # | Severity | File | Fix |
|---|----------|------|-----|
| 1 | CRITICAL | `app/utils/db_config.py` | Fixed `get_database_url()` to use `CORE_DATABASE_URL` |
| 2 | CRITICAL | `migrations/env.py` | Updated to use corrected `get_database_url()` |
| 3 | CRITICAL | `app/__init__.py` | Removed duplicate `/health` route |
| 4 | HIGH | `render.yaml` | `healthCheckPath: /heartbeat` → `/health` |
| 5 | HIGH | `config.py` | Removed stray module-level `Flask()` instance |
| 6 | HIGH | `config.py` | Fixed `DevelopmentConfig` engine options (removed PG-specific SSL args) |
| 7 | HIGH | `app/services/scheduler_lock.py` | Created PG advisory lock for scheduler safety |
| 8 | HIGH | `app/__init__.py` | Integrated advisory lock into `_init_scheduler()` |
| 9 | MEDIUM | `config.py` | Removed duplicate `SESSION_COOKIE_*` definitions |
| 10 | MEDIUM | Repository | Removed garbage directories (`{app`, `{migrations,...}`, etc.) |
| 11 | MEDIUM | `.env` | Redacted committed secrets |
| 12 | LOW | `.env.example` | Created comprehensive example with placeholders |
| 13 | LOW | `.gitignore` | Added `.mypy_cache/` entry |

---

## File-by-File Details

### `app/utils/db_config.py` (CRITICAL FIX)

**Before:** `get_database_url()` checked only `DIRECT_DATABASE_URL`, `DEV_DATABASE_URL`, `DATABASE_URL` — none set in v5.0 production.

**After:** Full priority chain:
```
DIRECT_CORE_DATABASE_URL → DIRECT_DATABASE_URL → CORE_DATABASE_URL
→ DEV_CORE_DATABASE_URL → DEV_DATABASE_URL → DATABASE_URL
```
Added `get_tenant_database_url()` for future multi-DB Alembic support.

### `migrations/env.py` (CRITICAL FIX)

Updated to call `get_database_url()` from the corrected `db_config.py`. The URL is now resolved in the correct priority order. No other behavioral changes.

### `app/__init__.py` (CRITICAL + HIGH FIXES)

1. Removed duplicate `@app.route("/health")` that checked only core DB and shadowed the heartbeat blueprint's comprehensive health check.
2. Added `acquire_scheduler_lock(app)` call in `_init_scheduler()` before starting APScheduler.

### `render.yaml` (HIGH FIX)

`healthCheckPath: /heartbeat` → `healthCheckPath: /health`

The `/health` endpoint (from `app/heartbeat/__init__.py`) now checks:
- Core DB connectivity
- Tenant DB connectivity + required table presence
- Redis connection status
- Self-ping state

### `config.py` (HIGH + MEDIUM FIXES)

1. **Removed stray Flask instance** — `app = Flask(...)` at module scope was dead code causing confusing import side effects.
2. **Fixed DevelopmentConfig engine options** — Removed `'sslmode': 'require'` and other PostgreSQL-specific `connect_args` that crashed SQLite connections in dev.
3. **Removed duplicate session cookie block** — `BaseConfig` had two definitions; the second (`SameSite=Strict`) overwrote the first (`SameSite=Lax`). Strict breaks cross-site flows on Render's reverse proxy. Now single definition with `Lax`.

### `app/services/scheduler_lock.py` (NEW FILE — HIGH FIX)

New module implementing a two-layer scheduler singleton lock:
1. `threading.Lock` — in-process race prevention (Gunicorn threaded workers)
2. `pg_try_advisory_lock(7919)` — cross-process/cross-instance race prevention (multiple Render instances)

### `.env` (CRITICAL — SECRETS REDACTED)

All live secret values replaced with `<REDACTED>` placeholders. The real values must be rotated — see `SECRETS_ROTATION_REPORT.md`.

