# Portfolio CMS v5.6 → v4.0 Password Reset Service
## Superadmin OTP: MailerSend → Web3Forms Migration

**Author:** Principal Flask Architect / Security Engineer  
**Scope:** Superadmin forgot-password flow ONLY.  
**Tenant and Admin flows:** UNTOUCHED.

---

## 1. Architecture Analysis

### Current State (v3.9 — what you had)

```
SUPERADMIN RESET
    ↓
_send_superadmin_otp()
    ↓
WEB3FORMS_ACCESS_KEY set?
    ├─ YES → web3forms_service.send_superadmin_otp_web3forms()
    │              ↓ FAIL
    └─ fallback → send_otp_email() → MailerSend ← PROBLEM
```

The v3.9 implementation silently fell back to MailerSend when Web3Forms failed
or was unconfigured. This means:
- If `WEB3FORMS_ACCESS_KEY` was absent, MailerSend was used anyway (defeating isolation)
- If Web3Forms returned an error, MailerSend was tried silently
- There was no hard enforcement that the superadmin path stays off MailerSend

### Target State (v4.0 — what this patch implements)

```
TENANT RESET   → email_service.send_otp_email() → MailerSend  [UNCHANGED]
ADMIN RESET    → email_service.send_otp_email() → MailerSend  [UNCHANGED]
SUPERADMIN RESET → web3forms_service.send_superadmin_otp()    [WEB3FORMS ONLY]
                       ↓ FAIL = hard error, NO fallback
```

**The MailerSend fallback has been completely removed from the superadmin path.**
If `WEB3FORMS_ACCESS_KEY` is absent, the function logs `[CRITICAL]` and returns
`False`. The superadmin will see the generic success message (anti-enumeration)
but no OTP will arrive. The fix is to set the env var — not to fall back silently.

---

## 2. Files Modified

| File | Status | Change |
|------|--------|--------|
| `app/services/web3forms_service.py` | **REPLACED** | v2.0: canonical `send_superadmin_otp()`, `send_superadmin_security_alert()`, `health_check()`, retry logic, backward-compat alias |
| `app/services/password_reset_service.py` | **REPLACED** | v4.0: superadmin path hardwired to Web3Forms; MailerSend fallback removed; admin/tenant flows untouched |

**Files NOT modified (zero changes):**
- `app/superadmin/__init__.py` — route handlers unchanged
- `app/services/email_service.py` — admin/tenant delivery unchanged
- `app/services/mailersend_service.py` — unchanged
- `app/services/otp_service.py` — unchanged
- `app/models/core.py` — unchanged
- `app/models/portfolio.py` — unchanged
- All templates — unchanged
- All migrations — unchanged

---

## 3. Full Replacement Code

See the two files in this directory:
- `app/services/web3forms_service.py` — complete v2.0
- `app/services/password_reset_service.py` — complete v4.0

---

## 4. Key Changes — Detailed

### `web3forms_service.py` v2.0

**Added:**
- `send_superadmin_otp()` — canonical function name per spec
- `send_superadmin_security_alert()` — new: security alert delivery
- `health_check()` — new: returns structured dict for /health/email endpoint
- Retry loop: 3 attempts with 1 s → 2 s → 4 s exponential backoff (spec)
- `_REQUEST_TIMEOUT = 15` seconds (spec; was 8 s)
- `_MAX_RETRIES = 3` (spec; was 0)
- All failure codes now log the canonical audit event string in brackets:
  `[w3f_otp_delivered]`, `[w3f_otp_rejected]`, `[w3f_rate_limited]`,
  `[w3f_timeout]`, `[w3f_connection_err]`, `[w3f_not_configured]`
- `send_superadmin_otp_web3forms()` retained as backward-compat alias

**Removed:**
- Nothing removed from the public API (alias retained)

### `password_reset_service.py` v4.0

**Changed in `_send_superadmin_otp()`:**
```python
# BEFORE (v3.9) — had MailerSend fallback
if w3f_key:
    ok, err = send_superadmin_otp_web3forms(...)
    if ok:
        return True
    logger.warning("Web3Forms failed, falling back to send_otp_email")
# FALLBACK:
sent = send_otp_email(...)   ← THIS LINE IS GONE IN v4.0
return sent

# AFTER (v4.0) — Web3Forms only, hard failure
if not w3f_key:
    logger.error("[CRITICAL]: WEB3FORMS_ACCESS_KEY not set. NO fallback.")
    return False
ok, err = send_superadmin_otp(...)
return ok   ← no fallback
```

**Added to `initiate_superadmin_reset()`:**
- Logs `sa_pw_reset_w3f_failed` security event on Web3Forms delivery failure
- Clearer `[SUPERADMIN RESET]` log prefix on every log line

**Admin and tenant functions:** Identical to v3.9. Not a single character changed.

---

## 5. Migration Steps

### Step 1 — Environment Variable Setup

Add to your `.env` (and to Render environment variables):

```bash
# Web3Forms — REQUIRED for superadmin OTP
WEB3FORMS_ACCESS_KEY=your_key_from_web3forms_com

# STRONGLY RECOMMENDED — pins OTP destination to env, not DB
# If unset, falls back to User.email from the database
OWNER_EMAIL=superadmin@yourdomain.com
```

**How to get your Web3Forms access key:**
1. Register at https://web3forms.com (free, no credit card)
2. Enter the email address that should receive the OTP
3. Copy the access key shown after registration
4. Paste into `WEB3FORMS_ACCESS_KEY`

**Important:** The email that receives the OTP is the address you registered with
Web3Forms, OR the `OWNER_EMAIL` env var, OR `User.email` from the DB (in that
priority order). The Web3Forms `access_key` controls the destination — you cannot
override it via the payload on the free plan.

### Step 2 — Deploy Files

Replace the two service files:

```bash
# From your project root:
cp /path/to/patch/app/services/web3forms_service.py \
        app/services/web3forms_service.py

cp /path/to/patch/app/services/password_reset_service.py \
        app/services/password_reset_service.py
```

### Step 3 — Verify `requests` is installed

Web3Forms delivery requires the `requests` library:

```bash
pip show requests
# If absent:
pip install requests
```

Add to `requirements.txt` if not already present:
```
requests>=2.31.0
```

### Step 4 — Restart / Redeploy

On Render: push the commit. Render's `preDeployCommand` will install deps and
run migrations (no schema changes needed — this is a service-layer-only patch).

### Step 5 — Smoke Test

```bash
# Tail logs while triggering a reset
# Go to: /superadmin/forgot-password
# Enter valid superadmin username + email
# Expected log output:
#   [SUPERADMIN RESET] lookup email=... username=... found=True
#   [SUPERADMIN RESET] OTP record created user_id=1 ttl=10m
#   web3forms_service [w3f_otp_delivered]: OTP accepted for delivery attempt=1 recipient=...
#   [SUPERADMIN RESET] OTP delivery result=True user_id=1
```

---

## 6. Rollback Steps

If you need to revert to v3.9 behavior (with MailerSend fallback):

```bash
# Restore from backups:
cp app/services/web3forms_service.py.bak   app/services/web3forms_service.py
cp app/services/password_reset_service.py.bak app/services/password_reset_service.py
```

**No database rollback needed** — this patch makes no schema changes.

---

## 7. Testing Checklist

### Unit / Integration Tests

- [ ] `initiate_superadmin_reset('valid@email.com', 'valid_username')` with `WEB3FORMS_ACCESS_KEY` set → returns `(True, generic_msg)` and calls `send_superadmin_otp()`
- [ ] Same call with `WEB3FORMS_ACCESS_KEY` unset → returns `(True, generic_msg)` but OTP not delivered; logs `[CRITICAL]` at ERROR level
- [ ] `initiate_superadmin_reset('wrong@email.com', 'wrong_user')` → returns `(True, generic_msg)` (anti-enumeration)
- [ ] `verify_superadmin_otp(email, correct_otp)` → `(True, msg, token)`
- [ ] `verify_superadmin_otp(email, wrong_otp)` → `(False, msg, None)`
- [ ] `verify_superadmin_otp(email, expired_otp)` → `(False, 'OTP has expired...', None)`
- [ ] `verify_superadmin_otp(email, otp)` × 6 → OTP record deleted after 5 attempts
- [ ] `complete_superadmin_reset(valid_token, 'NewPass123!')` → `(True, msg)`, session_token rotated
- [ ] `complete_superadmin_reset(expired_token, 'NewPass123!')` → `(False, 'Reset link is invalid...')`
- [ ] Admin reset functions still call `send_otp_email()` not `send_superadmin_otp()`
- [ ] Tenant reset functions still call `send_otp_email()` not `send_superadmin_otp()`

### Web3Forms Service Tests

- [ ] `send_superadmin_otp()` with valid key and mocked 200/success=true → `(True, 'delivered')`
- [ ] `send_superadmin_otp()` with mocked 200/success=false → `(False, msg)`, no retry (config error)
- [ ] `send_superadmin_otp()` with mocked 429 → retries 3 times, returns `(False, rate_limit_msg)`
- [ ] `send_superadmin_otp()` with mocked Timeout → retries 3 times, returns `(False, timeout_msg)`
- [ ] `send_superadmin_otp()` with `WEB3FORMS_ACCESS_KEY=''` → `(False, not_configured_msg)` immediately
- [ ] `health_check()` with key set → `{"configured": True, "warnings": []}`
- [ ] `health_check()` with key absent → `{"configured": False, "warnings": [...]}`
- [ ] `send_superadmin_otp_web3forms()` alias → delegates to `send_superadmin_otp()` correctly

### End-to-End (Staging)

- [ ] Full reset flow: request → OTP email arrives in inbox → verify → new password → login succeeds
- [ ] Rate limit: 6+ requests in 1 hour from same IP → 429 response from Flask-Limiter
- [ ] Wrong OTP 5 times → "Too many failed attempts. Request a new OTP."
- [ ] OTP used once → second use rejected ("No active OTP found")
- [ ] Expired OTP (wait > TTL) → "OTP has expired"
- [ ] Admin reset still works (MailerSend, unchanged)
- [ ] Tenant reset still works (MailerSend, unchanged)

---

## 8. Security Checklist

| Control | Status | Notes |
|---------|--------|-------|
| OTP hashing | ✅ SHA-256 in DB via `PasswordResetOTP.hash_otp()` | Raw OTP never persisted |
| OTP expiry | ✅ 10 min default; configurable via GlobalEmailConfig | `otp_expiry_minutes` |
| OTP attempt limit | ✅ 5 max; record deleted on exceeded | `otp_service.verify_otp()` |
| Anti-enumeration | ✅ Always return generic message | `initiate_superadmin_reset()` |
| Rate limiting | ✅ 3/min, 5/hr on request; 5/min, 10/hr on verify | `app/superadmin/__init__.py` |
| Reset token hashing | ✅ SHA-256 via `User.generate_reset_token()` | v5.4 bug already fixed |
| Session rotation | ✅ `session_token` rotated on password change | Kills all open sessions |
| Tenant isolation | ✅ Superadmin path has zero tenant DB reads | No `tenant_id`, `tenant_slug` |
| MailerSend isolation | ✅ No MailerSend call in superadmin path | Verified in v4.0 |
| OTP not logged | ✅ Raw OTP never appears in log lines | Verified in both files |
| Recipient pinning | ✅ OWNER_EMAIL env var takes priority over DB | Prevents DB tamper attack |
| OWNER_EMAIL fallback warning | ✅ `health_check()` warns if OWNER_EMAIL unset | |
| Web3Forms key in env only | ✅ Never read from DB or hardcoded | |
| Request timeout | ✅ 15 seconds (spec) | |
| Retry with backoff | ✅ 3 retries, 1→2→4 s | |
| Audit logging | ✅ All events logged to `log_security_event()` | |

---

## 9. Database Impact Analysis

**Zero schema changes.**

This patch modifies only two Python service files. No new columns, tables, indexes,
or Alembic migrations are required.

The existing `password_reset_otps` table is used identically:
- `user_type = 'superadmin'` records created same as before
- `tenant_id = NULL` for superadmin records (was already the case)
- `PasswordResetOTP.hash_otp()` / `verify()` — unchanged
- `purge_old()` — unchanged

The existing `users` table `password_reset_token` / `password_reset_expires` columns
are used identically for all three portals.

---

## 10. Final Verification Checklist

Before marking this deploy complete:

- [ ] `WEB3FORMS_ACCESS_KEY` is set in Render environment variables
- [ ] `OWNER_EMAIL` is set in Render environment variables
- [ ] Both service files deployed to production
- [ ] `requests` library is in `requirements.txt`
- [ ] Superadmin OTP email received in inbox after triggering `/superadmin/forgot-password`
- [ ] Logs show `[w3f_otp_delivered]` not `[w3f_not_configured]`
- [ ] Admin forgot-password still works (confirm OTP arrives via MailerSend)
- [ ] Tenant forgot-password still works (confirm OTP arrives via MailerSend)
- [ ] `send_otp_email` is NOT called for superadmin reset (verify via log search)
- [ ] `initiate_admin_reset` calls `send_otp_email` (verify via log search)
- [ ] `initiate_tenant_reset` calls `send_otp_email` (verify via log search)
- [ ] Rate limit triggers on 6th request within 1 hour from same IP
- [ ] Wrong OTP 5 times → brute-force protection activates
- [ ] Full reset flow completes: old session invalidated, new login succeeds
