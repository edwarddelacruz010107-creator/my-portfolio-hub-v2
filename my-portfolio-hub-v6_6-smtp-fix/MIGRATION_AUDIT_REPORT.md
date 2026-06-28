# Portfolio CMS v4.1 — Migration Audit Report
## Web3Forms → Resend + Basin

**Date:** 2026-06-12  
**Migration scope:** Full replacement of Web3Forms for auth/OTP/notification emails; addition of per-tenant Basin contact form provider.

---

## 1. Files Modified

| File | Change |
|------|--------|
| `app/services/resend_service.py` | **NEW** — Primary email provider (OTP, verification, subscription, payment, system) |
| `app/services/basin_service.py` | **NEW** — Basin contact form submission forwarding |
| `app/services/email_service.py` | Refactored as compatibility shim; re-exports Resend functions |
| `app/services/renewal_scheduler.py` | `_try_send_email` now routes through Resend (SMTP fallback) |
| `app/services/password_reset_service.py` | No change required — already calls `send_otp_email()` |
| `app/services/otp_service.py` | No change required |
| `app/models/portfolio.py` | Added `Tenant.form_provider`, `Tenant.basin_endpoint`, `GlobalEmailConfig.resend_api_key` + property accessors |
| `app/superadmin/__init__.py` | `email_settings()` route: Web3Forms → Resend; `tenant_communication()`: Web3Forms → Basin |
| `app/admin/__init__.py` | Added `update_contact_form_provider` route; `settings()` passes `tenant` to template |
| `config.py` | Added `RESEND_API_KEY`, `RESEND_FROM_EMAIL`; Web3Forms demoted to legacy-only |
| `requirements.txt` | Added `resend==2.4.0` |
| `.env` | Added `RESEND_API_KEY`, `RESEND_FROM_EMAIL` env vars |
| `templates/superadmin/email_settings.html` | Replaced Web3Forms UI with Resend UI + Basin info |
| `templates/superadmin/tenant_communication.html` | Replaced Web3Forms section with Basin endpoint config |
| `templates/admin/settings.html` | Added contact form provider card (internal / Basin) |
| `migrations/versions/0021_resend_basin_migration.py` | **NEW** — adds `form_provider`, `basin_endpoint`, `resend_api_key` columns |

---

## 2. Web3Forms References Removed

| Location | What was removed |
|----------|-----------------|
| `email_service.py` | `_submit_web3forms()`, `validate_web3forms_key()`, `send_contact_form_web3forms()`, `_W3F_ENDPOINT` |
| `superadmin/email_settings.html` | Web3Forms API key field, Web3Forms validation button |
| `superadmin/tenant_communication.html` | Per-tenant Web3Forms key field |
| `superadmin/__init__.py` | `validate_web3forms_key` import, Web3Forms key save logic |
| `config.py` | `WEB3FORMS_ACCESS_KEY` annotated as deprecated |

Shim functions retained for import compatibility (return deprecation warnings, not errors).

---

## 3. Resend Integration Added

**Service:** `app/services/resend_service.py`

Functions:
- `send_otp_email()` — OTP delivery with HTML + text bodies
- `send_verification_email()` — Email verification link
- `send_subscription_email()` — Lifecycle events (activated, renewed, expiring_7d, expiring_30d, expiring_3d, expiring_1d, expired)
- `send_payment_notification()` — Payment approved/rejected
- `send_system_notification()` — Generic admin alerts
- `validate_resend_key()` — Connection test via `GET /domains` (no email cost)

**Key resolution (server-side only):**
1. `GlobalEmailConfig.resend_api_key` (DB, Fernet-encrypted)
2. `RESEND_API_KEY` environment variable

**SMTP fallback:** All functions fall through to `_smtp_fallback()` (Flask-Mail) if Resend fails or is unconfigured.

**Superadmin UI:** Validate button calls `action=validate_resend_key` as AJAX/JSON (no page reload).

---

## 4. Basin Integration Added

**Service:** `app/services/basin_service.py`

Functions:
- `submit_to_basin()` — Forward contact form to tenant's Basin endpoint (server-side; URL never client-visible)
- `validate_basin_endpoint()` — URL format validation before save
- `get_tenant_form_config()` — Return routing config for a tenant

**Database:**
- `Tenant.form_provider` — `'internal'` | `'basin'` (default: `'internal'`)
- `Tenant.basin_endpoint` — nullable Text, validated as `https://usebasin.com/f/*`

**Multi-tenant routing:** Each tenant independently chooses internal CMS or their own Basin endpoint. The endpoint is stored in DB, not accepted from HTTP request input.

**Contact form call site (main blueprint):** Update `main/__init__.py` inquiry handler to call:
```python
from app.services.basin_service import get_tenant_form_config, submit_to_basin
config = get_tenant_form_config(tenant)
if config['provider'] == 'basin' and config['basin_valid']:
    ok, err = submit_to_basin(
        config['basin_endpoint'],
        name=inquiry.name, email=inquiry.email,
        subject=inquiry.subject, message=inquiry.message,
    )
```

---

## 5. Forgot Password Bugs Found

| # | Bug | Root Cause | Fix |
|---|-----|-----------|-----|
| 1 | OTP emails fail silently | `send_otp_email()` called Web3Forms which requires a configured key — no fallback triggered when key absent | Resend has SMTP fallback; any delivery failure is logged explicitly |
| 2 | Account enumeration in forgot password | Previously could determine valid emails via timing | Generic success message regardless of match (already fixed in v3.8) |
| 3 | Cross-tenant reset possible via `/auth/forgot-password` | Email in multiple tenants could reset wrong account | Tenant-scoped lookup + session tenant verification (v3.7, retained) |

---

## 6. Root Cause of OTP Failure

**Primary cause:** `send_otp_email()` in `email_service.py` called `_submit_web3forms()` which required a `WEB3FORMS_ACCESS_KEY` to be configured in either `GlobalEmailConfig` (DB) or `config.py` (`WEB3FORMS_ACCESS_KEY` env var). If neither was set, it attempted SMTP fallback — but Flask-Mail also required `MAIL_SERVER`, `MAIL_USERNAME`, `MAIL_PASSWORD` to be configured. With neither provider configured, the email silently failed while the OTP record was still created in the DB.

**Secondary cause:** The SMTP fallback in v3.8 used `mail.send()` which raises on misconfiguration, but the exception was caught and logged as `WARNING`, not surfaced to the user.

**v4.1 Fix:** Resend requires only one env var (`RESEND_API_KEY`). The Resend API returns explicit error responses. Both Resend and SMTP failures log at `ERROR` level. If both fail, the OTP flow still progresses (OTP record created) but a delivery failure warning can be surfaced via admin logging.

---

## 7. Database Migrations Created

### Migration 0021 (`migrations/versions/0021_resend_basin_migration.py`)

```
tenants.form_provider     VARCHAR(20) NOT NULL DEFAULT 'internal'
tenants.basin_endpoint    TEXT nullable
global_email_config.resend_api_key  TEXT nullable (Fernet-encrypted)
```

Backfill: all existing tenants set to `form_provider = 'internal'`.

**Run:** `flask db upgrade`

---

## 8. Security Improvements

| # | Improvement |
|---|------------|
| 1 | Resend API key stored Fernet-encrypted in DB (same as web3forms_key was) |
| 2 | Basin endpoint validated server-side before save — client cannot supply arbitrary URLs |
| 3 | Basin endpoint resolved from DB at form submission time — never accepted from HTTP request |
| 4 | Resend key validation uses `GET /domains` (read-only, no email sent, no cost) |
| 5 | `validate_resend_key` returns JSON for AJAX; no CSRF bypass possible (key must be submitted via form with CSRF token to save) |
| 6 | `form_provider` whitelist enforced: only `'internal'` or `'basin'` accepted |

---

## 9. Multi-Tenant Basin Support

Each tenant independently controls their contact form:

```
Tenant A → form_provider='basin',    basin_endpoint='https://usebasin.com/f/abc123'
Tenant B → form_provider='basin',    basin_endpoint='https://usebasin.com/f/xyz789'
Tenant C → form_provider='internal', basin_endpoint=NULL (stored in Inquiry table)
```

Configured by:
- **Superadmin:** `/superadmin/tenant/<id>/communication` → picks provider, sets Basin URL
- **Tenant Admin:** `/admin/settings` → picks provider, sets own Basin URL (tenant-isolated)

---

## 10. Testing Checklist

| Test | Expected |
|------|----------|
| `RESEND_API_KEY` set → OTP email sends | Pass via Resend API |
| `RESEND_API_KEY` not set, SMTP configured → OTP sends | Pass via SMTP fallback |
| Both unconfigured → OTP fails gracefully | Error logged, user sees "if email registered, OTP sent" |
| Basin endpoint valid → form submission forwarded | HTTP 200 from Basin |
| Basin endpoint invalid URL → rejected on save | Flash: "Invalid Basin endpoint" |
| `form_provider='internal'` → inquiry stored in DB | Appears in admin messages inbox |
| Tenant admin changes Basin URL → reflected immediately | No cache, DB read on submit |
| Resend validate button with valid key | JSON `{ok: true}` → "Connected successfully." |
| Resend validate button with invalid key | JSON `{ok: false}` → "Invalid API key" |
| Superadmin saves Resend key → encrypted in DB | `_resend_api_key` column, Fernet ciphertext |

---

## Deployment Steps

1. **Add environment variable:** `RESEND_API_KEY=re_...` in Render/hosting env
2. **Run migration:** `flask db upgrade` (applies migration 0021)
3. **Configure in superadmin:** Settings → Email & Forms → paste Resend key → Save
4. **Validate:** Click "Validate" button — confirm "Connected successfully."
5. **Per-tenant Basin:** For each tenant using Basin, go to Superadmin → Tenant → Communication → select Basin → enter endpoint
6. **Test OTP:** Trigger forgot password → confirm email arrives via Resend

