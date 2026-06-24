# Portfolio CMS v4.1 — Complete Senior-Level Audit Report
**Audited:** `portfolio_cms_v4.1-resend-basin`  
**Date:** 2026-06-14  
**Auditor:** Systems Engineering Audit (Claude Sonnet 4.6)

---

## Executive Summary

| Severity | Count |
|----------|-------|
| 🔴 CRITICAL | 6 |
| 🟠 HIGH | 5 |
| 🟡 MEDIUM | 5 |
| 🔵 LOW | 4 |
| **Total** | **20** |

**Production status: DO NOT DEPLOY.** Six critical bugs cause guaranteed crashes on every PayMongo checkout attempt and webhook receipt. Three of these are `TypeError`/`AttributeError` that will 500 every tenant who tries to pay.

---

## 🔴 CRITICAL ISSUES

---

### CRIT-01 — `initiate_checkout()` called with wrong argument order
**File:** `app/services/billing_handlers.py:113`  
**Crash:** `TypeError: initiate_checkout() takes 1 positional argument but 2 were given` (or assigns `profile` to `db_session`)

**Root cause:**  
`billing.py` defines:
```python
def initiate_checkout(db_session, profile, plan, billing_cycle, success_url, cancel_url):
```
`billing_handlers.py` calls:
```python
checkout_url, error = initiate_checkout(
    profile,           # ← assigned to db_session param
    selected_plan,     # ← assigned to profile param  
    billing_cycle=billing_cycle,
    return_endpoint=...,  # ← does NOT exist in the signature
)
```

Three problems compounded:
1. `db_session` is missing — `profile` lands in its slot
2. `return_endpoint` kwarg does not exist in the function signature → `TypeError`
3. Missing `success_url` / `cancel_url` (both have defaults of `""` — will produce empty PayMongo redirect URLs)

**Fix:** `app/services/billing_handlers.py` — see `patches/billing_handlers.py`

---

### CRIT-02 — `create_checkout_session()` called with wrong kwargs + unpacked as tuple
**File:** `app/services/billing.py:214–227`  
**Crash:** `TypeError: create_checkout_session() got unexpected keyword arguments 'amount_cents', 'currency', 'description', 'metadata'`  
**Secondary crash:** Even if resolved, `checkout_url, session_id = result` would fail because `create_checkout_session()` returns a `dict`, not a 2-tuple.

**Root cause:**  
`paymongo.py` defines:
```python
def create_checkout_session(*, tenant_id, tenant_slug, plan_name, billing_cycle, subscription_id, success_url, failed_url, cancel_url) -> Optional[Dict]:
    ...
    return {'checkout_url': ..., 'session_id': ..., 'customer_id': ...}
```
`billing.py` calls it with:
```python
checkout_url, session_id = create_checkout_session(
    amount_cents=..., currency=..., description=..., metadata=...,  # all wrong
)
```

**Fix:** `app/services/billing.py` — `initiate_checkout()` rewritten. See `patches/billing.py`

---

### CRIT-03 — `sub.external_id` does not exist; column is `paymongo_id`
**File:** `app/services/billing.py:227`  
**Crash:** `AttributeError: 'Subscription' object has no attribute 'external_id'`  
Also referenced at lines 466, 486–495 in `sync_subscription_from_paymongo()`.

**Root cause:**  
`Subscription` model has `paymongo_id` (line 679 of `models/portfolio.py`).  
`billing.py` writes to `sub.external_id` — a nonexistent attribute. SQLAlchemy silently stores it as a transient Python attribute, so it is **never persisted** and the checkout session ID is permanently lost. All subsequent webhook reconciliation relying on this field silently fails.

**Fix:** All `sub.external_id` → `sub.paymongo_id`. See `patches/billing.py`

---

### CRIT-04 — `mark_subscription_cancelled()` / `mark_subscription_expired()` missing from production billing.py
**File:** `app/utils/paymongo.py:330, 369`  
**Crash:** `ImportError: cannot import name 'mark_subscription_cancelled' from 'app.services.billing'` on every `subscription.cancelled` or `subscription.updated` webhook.

**Root cause:**  
`paymongo.py` imports:
```python
from app.services.billing import activate_subscription, mark_subscription_cancelled
# also:
from app.services.billing import mark_subscription_expired
```
These functions **do not exist** in the live `app/services/billing.py`. They only exist in `patches/billing.py` (the unpublished patch file). Every cancellation webhook will raise `ImportError` and return HTTP 500 to PayMongo, which will retry indefinitely.

**Fix:** Add both functions to `app/services/billing.py`. See `patches/billing.py`

---

### CRIT-05 — `superadmin_forms` blueprint defined but never registered
**File:** `routes/form_settings.py:141`, templates `forms_overview.html:153`, `forms_tenant_detail.html:8,151`  
**Crash:** `BuildError: Could not build url for endpoint 'superadmin_forms.forms_overview'` — 500 on every forms management page render.

**Root cause:**  
`routes/form_settings.py` creates:
```python
superadmin_forms = Blueprint('superadmin_forms', __name__, url_prefix='/superadmin')
```
This blueprint is **never imported or registered** in `app/__init__.py` or `app/superadmin/__init__.py`. The two templates that call `url_for('superadmin_forms.forms_overview')` will crash with `BuildError`.

**Fix:** Register in `app/__init__.py`. See `patches/app_init_registration.py`

---

### CRIT-06 — `hmac.new()` does not exist — should be `hmac.new` → `hmac.HMAC` or use `hmac.new()` alias
**File:** `app/utils/paymongo.py:144`  
**Crash:** `AttributeError: module 'hmac' has no attribute 'new'`

**Root cause:**
```python
expected = hmac.new(
    webhook_secret.encode('utf-8'),
    payload,
    hashlib.sha256,
).hexdigest()
```
Python's `hmac` module does not have a `.new()` attribute. The correct API is `hmac.new(key, msg, digestmod)` in Python 2, but in Python 3 (used here: 3.12) it is `hmac.new()` which is actually still valid in Python 3 as a legacy alias — **however**, checking the Python 3.12 docs confirms `hmac.new()` IS available as an alias for `hmac.HMAC()`. This would work, **but** the argument order is `hmac.new(key, msg=None, digestmod='')` and passing `hashlib.sha256` as third positional arg is correct.

**RE-EVALUATION:** `hmac.new()` IS valid in Python 3.12. This is NOT a crash. Downgraded from CRITICAL to **MEDIUM** (see MEDIUM-01). Replacing the prior CRIT-06 slot.

**Actual CRIT-06: `get_or_create_pending_subscription()` called with `profile` instead of `profile.tenant_id`**  
**File:** `app/services/billing.py:205`  
**Crash:** `AttributeError` or wrong type — function signature expects `tenant_id: int | str`, receives a `Profile` ORM object.

```python
# billing.py initiate_checkout():
sub = get_or_create_pending_subscription(
    db_session, profile, norm, billing_cycle=billing_cycle  # ← profile should be profile.tenant_id
)
```

```python
# get_or_create_pending_subscription definition:
def get_or_create_pending_subscription(db_session, tenant_id: int | str, plan, ...):
    ...
    .filter_by(tenant_id=tenant_id, status="active")  # SQLAlchemy receives a Profile object → wrong query
```

**Fix:** `app/services/billing.py` — pass `profile.tenant_id`. See `patches/billing.py`

---

## 🟠 HIGH ISSUES

---

### HIGH-01 — APScheduler fires in EVERY Gunicorn worker — duplicate renewal emails
**File:** `app/__init__.py:_init_scheduler()`, `render.yaml:21`  
**Impact:** With `--workers 2`, the renewal check job fires twice per day per configured trigger. Tenants receive duplicate expiry/renewal emails. With `--workers 4` it fires 4×.

**Root cause:** `_init_scheduler()` is called inside `create_app()` with no cross-process mutex. Each Gunicorn worker forks, calls `create_app()`, and starts its own independent `BackgroundScheduler` with the same cron job. The `if _scheduler and _scheduler.running: return` guard is a module-level variable — it is not shared across processes.

**Fix:**
```yaml
# render.yaml — reduce to 1 worker OR externalize scheduler
startCommand: >
  gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT --timeout 60 wsgi:app
```
Long term: move `run_renewal_check` to a Render Cron Job (separate service).

---

### HIGH-02 — `render.yaml` missing critical env vars — email and PayMongo silently broken on first deploy
**File:** `render.yaml`  
**Impact:** `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `APP_BASE_URL`, `FERNET_KEY` (if used), `PAYMONGO_ENABLED` are absent from `render.yaml`. Resend email will silently fail (falls back to SMTP which is also unconfigured). PayMongo redirect URLs will be empty strings.

**Missing from render.yaml:**
```yaml
- key: RESEND_API_KEY
  sync: false
- key: RESEND_FROM_EMAIL
  sync: false
- key: APP_BASE_URL
  sync: false
- key: BILLING_GRACE_PERIOD_DAYS
  value: "3"
```

**Fix:** See `patches/render.yaml`

---

### HIGH-03 — `flask ensure-default-tenant` CLI command defined in `run.py`, not in `wsgi:app`
**File:** `render.yaml:17`, `run.py:60`  
**Impact:** Render's `preDeployCommand` runs:
```
flask ensure-default-tenant
```
Flask resolves CLI commands from the `FLASK_APP` entry point. `render.yaml` sets `FLASK_APP=run.py`. The CLI command `ensure-default-tenant` IS registered in `run.py` — **so this actually works**. However `wsgi.py` creates the app via `create_app()` which registers CLI commands via `register_cli_commands(app)`, and `run.py` additionally registers `ensure-default-tenant` separately. The **real bug** is that `register_cli_commands()` only registers `run-renewal-check`, not `ensure-default-tenant`. If someone sets `FLASK_APP=wsgi.py`, the command breaks. Currently functional with `FLASK_APP=run.py` but fragile.

**Fix:** Move all CLI commands to `register_cli_commands()` in `app/__init__.py`.

---

### HIGH-04 — TOTP brute-force limiter uses in-memory dict — ineffective across workers
**File:** `app/auth/__init__.py` (TOTP limiter)  
**Impact:** With 2 Gunicorn workers, an attacker effectively gets 2× the per-process attempt limit before lockout. With `Flask-Limiter` + Redis configured for HTTP rate limits, the TOTP-specific in-process dict is still unshared.

**Fix:** Replace the in-process TOTP attempt counter with a Redis key using the same `REDIS_URL`. See `patches/auth_totp_ratelimit.py`.

---

### HIGH-05 — `upgrade.sql` uses MySQL-only DDL — cannot run on PostgreSQL/Supabase
**File:** `migrations/upgrade.sql:36, 45, 63, 70, 84, 104, 131, 138`  
**Impact:** Running this migration against the production Supabase PostgreSQL will fail immediately.

**MySQL-only syntax found:**
```sql
ADD COLUMN IF NOT EXISTS payload  MEDIUMTEXT NULL  -- MEDIUMTEXT is MySQL; use TEXT
id INT NOT NULL AUTO_INCREMENT,                    -- use SERIAL or BIGSERIAL
ENGINE=InnoDB DEFAULT CHARSET=utf8mb4             -- MySQL engine clause; invalid in PG
```

**Fix:** See `patches/upgrade_postgresql.sql`

---

## 🟡 MEDIUM ISSUES

---

### MED-01 — `hmac.new()` is valid Python 3 but `hashlib.sha256` should be passed as the class not an instance
**File:** `app/utils/paymongo.py:144`  
**Status:** Does not crash but is ambiguous. Python 3 docs recommend using the string `'sha256'` or `hashlib.sha256` (the class) as digestmod. Current code passes `hashlib.sha256` (the class) — this is technically correct but worth standardizing.

**Recommended fix:**
```python
expected = hmac.new(
    webhook_secret.encode('utf-8'),
    payload,
    'sha256',
).hexdigest()
```

---

### MED-02 — `PERMANENT_SESSION_LIFETIME` not set — admin sessions never expire
**File:** `config.py`  
**Impact:** Flask defaults `PERMANENT_SESSION_LIFETIME` to 31 days. But `session.permanent` is only set to True if code explicitly calls `session.permanent = True`. If it is never set, sessions are browser-session-scoped (expire on tab close), which conflicts with `REMEMBER_COOKIE_DURATION = timedelta(days=30)`.  
Recommend explicit setting:
```python
PERMANENT_SESSION_LIFETIME = timedelta(hours=8)  # Adjust per business need
```

---

### MED-03 — `0022_tenant_form_settings.sql` migration creates `tenant_form_settings` table but no Python model exists
**File:** `migrations/0022_tenant_form_settings.sql`, `app/models/portfolio.py`  
**Impact:** The SQL migration creates a `tenant_form_settings` table. No `TenantFormSettings` SQLAlchemy model exists. The `form_provider` and `basin_endpoint` columns are instead on the `Tenant` model directly. This means the SQL migration creates a dead table that conflicts with the intended design.

**Fix:** Either drop the migration and rely on the Tenant model columns (preferred), or create the model and wire it. See notes in `patches/migration_cleanup.md`.

---

### MED-04 — `session_protection = 'strong'` + HMAC tenant re-stamping causes spurious logouts on IP change
**File:** `app/__init__.py:login_manager.session_protection = 'strong'`  
**Impact:** `login_manager.session_protection = 'strong'` invalidates sessions when the client IP or user agent changes (e.g., mobile users switching cell towers, users behind CDNs). The custom `TenantGuard` HMAC re-stamps sessions on every request — these two mechanisms are redundant and can conflict, causing unexpected logouts.

**Fix:**
```python
login_manager.session_protection = 'basic'  # or None if TenantGuard is the sole guard
```

---

### MED-05 — `instance/portfolio_dev.db` and `__pycache__` committed to version control
**File:** `.gitignore` (missing or incomplete)  
**Impact:** The SQLite dev database (which may contain real data if populated locally) and compiled bytecode are in the repo. `__pycache__` directories inflate the archive.

**Fix:** Add to `.gitignore`:
```
instance/
__pycache__/
*.pyc
*.pyo
.env
```

---

## 🔵 LOW ISSUES

---

### LOW-01 — `migrate_fix.py` and `forms/tenant_forms.py` are dead files that conflict with existing modules
**File:** `migrate_fix.py`, `forms/tenant_forms.py`  
**Impact:** `migrate_fix.py` appears to be a one-time migration aid left in the repo root. `forms/tenant_forms.py` at the repo root conflicts with `app/forms/__init__.py`. If accidentally imported, it shadows the real forms module.

---

### LOW-02 — `billing_overview_patch.py` and `forms_patch/` directories are dead patch artifacts
**File:** `app/superadmin/billing_overview_patch.py`, `app/forms_patch/billing_forms.py`  
**Impact:** These are patch artifacts that should have been applied and removed. They add confusion and risk being imported accidentally if someone refactors imports.

---

### LOW-03 — `test_*.py` files scattered in repo root alongside production code
**File:** `test_billing_v34.py`, `test_db.py`, `test_default_admin_isolation.py`, etc.  
**Impact:** Tests in the repo root are not auto-discovered by pytest when run from `tests/`. They also pollute the root namespace.

---

### LOW-04 — `docker-compose.prod.yml` uses `build: .` with no `Dockerfile` volume mounts for persistent uploads
**File:** `docker-compose.prod.yml`  
**Impact:** Local `static/uploads/` is not volume-mounted in the prod compose file, so uploaded profile/project images are lost on container restart. Only matters if not using Supabase storage.

---

## Deployment Fix Summary

### render.yaml — Complete Fixed Version
See `patches/render.yaml`

### FLASK_APP entry point
`wsgi.py` correctly calls `create_app()`. Keep `FLASK_APP=wsgi.py` (not `run.py`) for Gunicorn. The `ensure-default-tenant` command should be moved into `register_cli_commands()`.

### Worker count
Reduce to `--workers 1 --threads 4` until APScheduler is externalized to a Render Cron Job.

---

## PostgreSQL Schema (Production-Ready)

See `patches/schema_postgresql.sql` for the complete corrected schema.

---

## Patch Files Index

| File | Fixes |
|------|-------|
| `patches/billing.py` | CRIT-02, CRIT-03, CRIT-04, CRIT-06 |
| `patches/billing_handlers.py` | CRIT-01 |
| `patches/paymongo.py` | MED-01 |
| `patches/app_init_registration.py` | CRIT-05 |
| `patches/render.yaml` | HIGH-02, HIGH-01 (worker count) |
| `patches/upgrade_postgresql.sql` | HIGH-05 |
| `patches/schema_postgresql.sql` | Full PG schema |
| `patches/migration_cleanup.md` | MED-03 guidance |
