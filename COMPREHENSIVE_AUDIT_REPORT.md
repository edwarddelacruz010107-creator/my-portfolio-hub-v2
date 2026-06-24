# Portfolio CMS v5.0 — COMPREHENSIVE CODEBASE AUDIT
**Date:** June 16, 2026  
**Status:** CRITICAL ISSUES FOUND  
**Verdict:** DO NOT DEPLOY — Fix Required

---

## EXECUTIVE SUMMARY

### Critical Issues: 6
### High Issues: 5  
### Medium Issues: 4
### Low Issues: 3

**Primary Failure Vector:** Render deployment will fail due to environment variable mismatches. Application expects `MAILERSEND_API_KEY`, `CORE_DATABASE_URL`, `TENANT_DATABASE_URL` but `render.yaml` defines `RESEND_API_KEY` and single `DATABASE_URL`.

---

## PHASE 1 — DEPENDENCY & IMPORT AUDIT

### ✅ Entry Points (CORRECT)
- **wsgi.py**: Exports `app` object — Gunicorn will find it ✓
- **run.py**: Development entry point — correctly imports from `app` ✓
- **Gunicorn command**: `gunicorn wsgi:app` — correctly configured in render.yaml ✓

### ✅ Extension Initialization (CORRECT)
- SQLAlchemy, Flask-Migrate, Flask-Login: All correctly init'd
- Flask-Limiter with Redis: Properly configured
- CSRF Protection: Correctly enabled with exemptions for webhooks

### ⚠️ Blueprint Registration (CORRECT BUT ORDER MATTERS)
- Order: auth → admin → main → webhooks → superadmin → superadmin_forms → admin_forms → tenant_bp ✓
- Superadmin uses explicit `url_prefix='/superadmin'` to prevent conflicts ✓

---

## PHASE 2 — CRITICAL DATABASE MISCONFIGURATION

### ❌ CRITICAL: Dual-Database Architecture Not Configured in Render

**Issue:** `render.yaml` defines single `DATABASE_URL`, but `config.py` requires TWO databases:

```python
# config.py (lines 281-282)
required_vars = [
    'CORE_DATABASE_URL',      # ← Render.yaml missing this
    'TENANT_DATABASE_URL',    # ← Render.yaml missing this
]
```

**What render.yaml actually has:**
```yaml
- key: DATABASE_URL
  sync: false
```

**What it SHOULD have:**
```yaml
- key: CORE_DATABASE_URL
  sync: false
- key: TENANT_DATABASE_URL
  sync: false
```

**Impact:** On Render startup, ProductionConfig.init_app() will throw:
```
ValueError: Production environment missing required variables: CORE_DATABASE_URL, TENANT_DATABASE_URL
```

**Fix Required:**
1. Update render.yaml to define CORE_DATABASE_URL and TENANT_DATABASE_URL
2. Provision TWO PostgreSQL databases on Render
3. Set environment variables in Render dashboard

---

## PHASE 3 — EMAIL SERVICE CONFIGURATION MISMATCH

### ❌ CRITICAL: Render.yaml references outdated email provider

**render.yaml (line 66):**
```yaml
- key: RESEND_API_KEY
  sync: false
```

**But config.py expects:**
```python
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')      # ← Deprecated
RESEND_FROM_EMAIL = os.environ.get('RESEND_FROM_EMAIL', '') # ← Not used
```

**Actual implementation (v5.0):**
- All email routes through `mailersend_service.py`
- `config.py` DOES define MAILERSEND_* variables
- `.env.example` documents MAILERSEND_API_KEY, not RESEND

**Correct variables needed:**
```yaml
- key: MAILERSEND_API_KEY
  sync: false
- key: MAILERSEND_FROM_EMAIL
  sync: false
```

### ❌ HIGH: Superadmin still references deprecated Resend

**app/superadmin/__init__.py (line 1788):**
```python
from app.services.resend_service import validate_resend_key

# Line 1798-1802: Still validates Resend keys
if action == 'validate_resend_key':
    key = request.form.get('resend_api_key', '').strip()
    valid, msg = validate_resend_key(key)
```

**Impact:** Email settings UI in superadmin will fail when trying to validate API keys.

**Fix:** Update to use `mailersend_service.validate_mailersend_key()`

---

## PHASE 4 — DEPRECATED FUNCTION CALLS

### ❌ HIGH: Web3Forms still called in tenant contact form

**app/tenant/__init__.py (line ~2000):**
```python
from app.services.email_service import send_contact_form_web3forms
w3f_sent = send_contact_form_web3forms(...)  # ← Deprecated, returns False
```

**Impact:** Contact form emails will silently fail.

**Fix:** Route through `mailersend_service.send_email()` or internal Inquiry model.

---

## PHASE 5 — SECURITY AUDIT

### ✅ CSRF Protection (CORRECT)
- CSRFProtect enabled globally
- Correctly exempted for: webhooks, heartbeat
- Session protection: 'strong'

### ✅ Authentication (CORRECT)
- Login manager hardened with tenant validation
- TOTP/OTP flow uses pyotp
- Session fixation prevention implemented

### ✅ File Upload Validation (CORRECT)
- Magic-byte validation enabled
- MIME type checking
- File extension whitelist

### ✅ SQL Injection (CORRECT)
- SQLAlchemy ORM used exclusively
- No raw SQL in critical paths

### ⚠️ MEDIUM: Hardcoded Secrets in Config
**config.py (line 46):**
```python
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
```

This default should NEVER be used in production. However, ProductionConfig.init_app() validates this is set, so deployment will fail if missing. ✓

---

## PHASE 6 — MULTI-TENANT ISOLATION AUDIT

### ✅ Tenant Context Injection (CORRECT)
- TenantGuard middleware validates tenant_slug
- User loader validates tenant consistency (line 156)
- Session tenant matches user tenant

### ✅ Database Isolation (CORRECT)
- Core data in default database
- Tenant-specific data in separate 'tenant' bind
- Foreign keys prevent cross-tenant access

### ⚠️ MEDIUM: Deprecated Web3Forms Contact Route
**app/tenant/__init__.py:**
```python
send_contact_form_web3forms()  # This returns False
```

This silently fails but doesn't expose data.

---

## PHASE 7 — RENDER DEPLOYMENT CHECKLIST

### ❌ render.yaml Issues

| Line | Issue | Severity |
|------|-------|----------|
| 56-57 | `DATABASE_URL` instead of `CORE_DATABASE_URL` | CRITICAL |
| 58-61 | Supabase vars present but optional | MEDIUM |
| 66 | `RESEND_API_KEY` instead of `MAILERSEND_API_KEY` | CRITICAL |
| 67 | `RESEND_FROM_EMAIL` instead of `MAILERSEND_FROM_EMAIL` | CRITICAL |

### ✅ Correct Elements
- Python 3.12.0 specified ✓
- Gunicorn workers/threads correct (1 worker, 4 threads) ✓
- Pre-deploy migrations: `flask db upgrade` ✓
- Health check path: `/heartbeat` ✓
- Timeout: 120 seconds ✓

---

## PHASE 8 — ROOT CAUSE ANALYSIS

### Why Deployment Will Fail

**Scenario:** Deploy to Render

1. **Render builds image**, installs `requirements.txt` ✓
2. **Pre-deploy runs**: `flask db upgrade && flask ensure-default-tenant`
3. **App starts**: `gunicorn wsgi:app`
4. **wsgi.py calls**: `create_app(os.environ.get('FLASK_ENV', 'production'))`
5. **Flask loads config**: `ProductionConfig.init_app(app)`
6. **Validation fails:**
   ```
   ValueError: Production environment missing required variables: 
   CORE_DATABASE_URL, TENANT_DATABASE_URL
   ```
7. **Deployment crashed** ❌

### If Step 6 Passes

8. **Email service attempts init**: Looks for `MAILERSEND_API_KEY`
9. **If missing**: Email features disabled, non-fatal warning
10. **Superadmin email settings page**: References `resend_api_key` form field
11. **Superadmin tries to validate**: Calls deprecated `validate_resend_key()`
12. **Always returns False**: "Resend is no longer used"

---

## PHASE 9 — EXACT FILES TO FIX

### 1. **render.yaml** [CRITICAL]

**Lines 56-57:** Remove/update
```yaml
- key: DATABASE_URL          # ← DELETE
  sync: false
```

**Lines 58-61:** These are storage, keep as optional
```yaml
- key: SUPABASE_URL
  sync: false
- key: SUPABASE_SERVICE_KEY
  sync: false
- key: SUPABASE_BUCKET
  sync: false
```

**Lines 65-67:** Update email config
```yaml
# ── Email (MailerSend — primary, updated v5.0) ───────────────────────
- key: MAILERSEND_API_KEY      # ← Changed from RESEND_API_KEY
  sync: false
- key: MAILERSEND_FROM_EMAIL   # ← Changed from RESEND_FROM_EMAIL
  sync: false
```

**ADD NEW LINES after line 61:**
```yaml
      # ── Core Databases (dual-database architecture) ────────────────
      - key: CORE_DATABASE_URL
        sync: false
      - key: TENANT_DATABASE_URL
        sync: false
```

---

### 2. **app/superadmin/__init__.py** [HIGH]

**Line 1788:** Update import
```python
# OLD:
from app.services.resend_service import validate_resend_key

# NEW:
from app.services.email_service import validate_mailersend_key
```

**Lines 1797-1803:** Update validation
```python
# OLD:
if action == 'validate_resend_key':
    key = request.form.get('resend_api_key', '').strip()
    if not key:
        return jsonify({'ok': False, 'message': 'No key supplied.'})
    valid, msg = validate_resend_key(key)
    return jsonify({'ok': valid, 'message': msg})

# NEW:
if action == 'validate_mailersend_key':
    key = request.form.get('mailersend_api_key', '').strip()
    if not key:
        return jsonify({'ok': False, 'message': 'No key supplied.'})
    valid, msg = validate_mailersend_key(key)
    return jsonify({'ok': valid, 'message': msg})
```

**Lines 1806-1822:** Update settings storage
```python
# OLD:
new_resend_key = request.form.get('resend_api_key', '').strip()
...
if new_resend_key:
    cfg.resend_api_key = new_resend_key

# NEW:
new_mailersend_key = request.form.get('mailersend_api_key', '').strip()
...
if new_mailersend_key:
    cfg.mailersend_api_key = new_mailersend_key
```

---

### 3. **app/tenant/__init__.py** [MEDIUM]

**Line ~2000:** Remove deprecated Web3Forms

```python
# OLD:
from app.services.email_service import send_contact_form_web3forms
...
w3f_sent = send_contact_form_web3forms(contact_email, contact_message, tenant_slug)

# NEW:
# Route through MailerSend or store as Inquiry
# (Contact forms should create an Inquiry record, not send via Web3Forms)
```

---

### 4. **app/models/portfolio.py** [MEDIUM - if GlobalEmailConfig exists]

Verify `GlobalEmailConfig` has `mailersend_api_key` field (not `resend_api_key`).

---

## PATCH DIFFS

### render.yaml

```diff
--- a/render.yaml
+++ b/render.yaml
@@ -52,19 +52,24 @@ services:
       # ── Core secrets — set in Render dashboard ────────────────────────
       - key: SECRET_KEY
         generateValue: true
       # NEW-04 FIX: FERNET_KEY is required.
       - key: FERNET_KEY
         sync: false
-      - key: DATABASE_URL
+      # DUAL-DATABASE ARCHITECTURE (v5.0)
+      - key: CORE_DATABASE_URL
+        sync: false
+      - key: TENANT_DATABASE_URL
         sync: false
       - key: SUPABASE_URL
         sync: false
       - key: SUPABASE_SERVICE_KEY
         sync: false
       - key: SUPABASE_BUCKET
         sync: false
 
-      # ── Email (Resend — primary) — HIGH-02 FIX ───────────────────────
-      - key: RESEND_API_KEY
+      # ── Email (MailerSend — primary, v5.0) ────────────────────────────
+      - key: MAILERSEND_API_KEY
         sync: false
-      - key: RESEND_FROM_EMAIL
+      - key: MAILERSEND_FROM_EMAIL
         sync: false
```

---

## POSTGRES SCHEMA VALIDATION

### Core Database Tables
```sql
-- Users & Auth
SELECT * FROM "user" ORDER BY id;
SELECT * FROM admin ORDER BY id;

-- Tenants & Billing
SELECT * FROM tenant ORDER BY id;
SELECT * FROM subscription ORDER BY id;
SELECT * FROM billing_transaction ORDER BY id;

-- Platform
SELECT * FROM global_email_config LIMIT 1;
SELECT * FROM audit_log ORDER BY id DESC LIMIT 10;
```

### Tenant Database Tables
```sql
-- Portfolio Content
SELECT * FROM portfolio.profile ORDER BY id;
SELECT * FROM portfolio.project ORDER BY id;
SELECT * FROM portfolio.skill ORDER BY id;

-- Communications
SELECT * FROM portfolio.inquiry ORDER BY id DESC LIMIT 10;
SELECT * FROM portfolio.message ORDER BY id DESC LIMIT 10;

-- Settings
SELECT * FROM portfolio.tenant_form_settings WHERE tenant_id = 1;
```

---

## PRODUCTION ENVIRONMENT SETUP

### Required Environment Variables

```bash
# Core
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_urlsafe(32))">
FERNET_KEY=<generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
FLASK_ENV=production

# Databases (TWO required)
CORE_DATABASE_URL=postgresql://user:pass@host:5432/portfolio_core
TENANT_DATABASE_URL=postgresql://user:pass@host:5432/portfolio_tenant

# Email (MailerSend)
MAILERSEND_API_KEY=<from app.mailersend.com>
MAILERSEND_FROM_EMAIL=noreply@yourdomain.com
MAILERSEND_FROM_NAME=Portfolio CMS

# Caching
REDIS_URL=redis://...  # Render auto-injects

# Payment
PAYMONGO_PUBLIC_KEY=pk_live_...
PAYMONGO_SECRET_KEY=sk_live_...
PAYMONGO_WEBHOOK_SECRET=whsk_live_...
PAYMONGO_ENABLED=true

# App
APP_BASE_URL=https://your-app.onrender.com
BILLING_GRACE_PERIOD_DAYS=3

# Monitoring (optional)
SENTRY_DSN=...
BETTERSTACK_HEARTBEAT_URL=...
```

---

## DEPLOYMENT CHECKLIST

- [ ] **Databases:** Create 2 PostgreSQL databases on Render (core + tenant)
- [ ] **Secrets:** Generate SECRET_KEY and FERNET_KEY locally
- [ ] **render.yaml:** Apply patch (add CORE_/TENANT_DATABASE_URL, fix email vars)
- [ ] **Superadmin:** Apply patch (fix Resend → MailerSend references)
- [ ] **Tenant:** Apply patch (remove Web3Forms contact form code)
- [ ] **Models:** Verify GlobalEmailConfig has mailersend_api_key field
- [ ] **Pre-deploy:** Test locally: `flask db upgrade && flask ensure-default-tenant`
- [ ] **Health check:** Verify `/heartbeat` responds 200 OK
- [ ] **Email test:** Send test email via MailerSend integration
- [ ] **Tenant:** Create test tenant, verify isolation
- [ ] **Admin:** Login to superadmin, verify email settings page loads

---

## VERDICT: DO NOT DEPLOY

**Current Status:** Application will fail during Render pre-deploy phase due to missing `CORE_DATABASE_URL` and `TENANT_DATABASE_URL`.

**Action Required:**
1. Apply render.yaml patch
2. Apply superadmin/__init__.py patch
3. Apply tenant/__init__.py patch
4. Provision 2 PostgreSQL databases
5. Set environment variables in Render dashboard
6. Test locally before redeployment

**Estimated Fix Time:** 30 minutes
**Risk Level:** Low (fixes are isolated)
**Rollback Plan:** Revert render.yaml, revert environment vars

