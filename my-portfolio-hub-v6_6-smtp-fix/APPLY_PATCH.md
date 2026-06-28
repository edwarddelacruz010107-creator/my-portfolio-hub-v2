# Superadmin OTP → Web3Forms Patch
**Portfolio CMS v5.6 → v3.9 password_reset_service**

---

## What This Patch Changes

| File | Change |
|---|---|
| `app/services/web3forms_service.py` | **NEW FILE** — Web3Forms HTTP relay, isolated to superadmin OTP |
| `app/services/password_reset_service.py` | **PATCHED** — `initiate_superadmin_reset()` now calls `_send_superadmin_otp()` which routes Web3Forms first, falls back to existing SMTP/MailerSend chain |
| `.env` | **ADD** two new variables (see ENV_ADDITIONS.txt) |

**Nothing else is touched.** Admin/tenant reset flows are unchanged.

---

## Root Cause of Original Issue

`send_otp_email()` uses the shared SMTP → MailerSend provider chain. When:
- SMTP is configured but the provider (e.g. Gmail, SendGrid) issues a 429 / auth error
- OR MailerSend quota is exhausted from tenant contact-form traffic

...the superadmin OTP silently fails. The error appears as "too many requests" from the SMTP layer or a MailerSend 422/429. Web3Forms operates as a **completely independent relay** with its own quota and no dependency on smtplib or MailerSend.

---

## Delivery Flow After Patch

```
initiate_superadmin_reset()
        │
        ▼
_send_superadmin_otp()          ← NEW isolation wrapper
        │
        ├─[WEB3FORMS_ACCESS_KEY set]──► Web3Forms HTTP API
        │                                    │
        │                               Success ──► return True
        │                               Failure ──► fall through
        │
        └─[fallback]─────────────────► send_otp_email()
                                            │
                                       SMTP → MailerSend chain
                                       (existing behavior)
```

Admin/tenant flows remain on the direct `send_otp_email()` path — unaffected.

---

## Step-by-Step Apply Instructions (Windows / PowerShell)

### Step 1 — Back up existing files
```powershell
Copy-Item "app\services\password_reset_service.py" `
          "app\services\password_reset_service.py.bak"
```

### Step 2 — Copy new files into your project
```powershell
# New file — Web3Forms helper
Copy-Item "patch_output\app\services\web3forms_service.py" `
          "app\services\web3forms_service.py"

# Patched file — password reset orchestration
Copy-Item "patch_output\app\services\password_reset_service.py" `
          "app\services\password_reset_service.py"
```

### Step 3 — Add env variables
Open your `.env` file and add the two lines from `ENV_ADDITIONS.txt`:
```
WEB3FORMS_ACCESS_KEY=your_key_here
OWNER_EMAIL=owner@yourdomain.com
```

### Step 4 — Get your Web3Forms access key (if you don't have one)
1. Go to https://web3forms.com
2. Enter the email address that should receive OTPs
3. Click **Create Access Key** — key is emailed immediately
4. Paste it as `WEB3FORMS_ACCESS_KEY` in `.env`

### Step 5 — Test locally (optional but recommended)
```powershell
# In your venv
python -c "
from app.services.web3forms_service import validate_web3forms_config
issues = validate_web3forms_config()
print('Config OK' if not issues else issues)
"
```

### Step 6 — Deploy to Render
Add the two env vars in Render dashboard → Environment:
- `WEB3FORMS_ACCESS_KEY` = your key
- `OWNER_EMAIL` = your email

Then trigger a redeploy (or push to trigger auto-deploy).

---

## Rollback

If anything breaks, restore the backup:
```powershell
Copy-Item "app\services\password_reset_service.py.bak" `
          "app\services\password_reset_service.py"
# Remove web3forms_service.py (it's unused without the patched prs.py)
Remove-Item "app\services\web3forms_service.py"
```

The patch has zero schema changes, so no migration rollback is needed.

---

## Security Notes

1. **OTP never logged** — `web3forms_service.py` logs delivery status only, not the OTP value.
2. **Access key masked in logs** — only the first 6 chars are included in error messages.
3. **OWNER_EMAIL pin** — fixes the recipient at the env level; a compromised DB record cannot redirect the OTP.
4. **Timeout enforced** — 8s hard timeout prevents Gunicorn worker blocking on Web3Forms outage.
5. **Rate limits preserved** — the `@limiter.limit('3 per minute')` decorator on `forgot_password_request()` is unchanged.
6. **No new middleware** — web3forms_service.py is a pure helper; no Flask extension or middleware added.
7. **Requests is already a dependency** — used by mailersend_service.py; no new package needed.

---

## Verification After Deploy

1. Go to `/superadmin/forgot-password/request`
2. Enter your superadmin username and email
3. Check your inbox (the email registered with Web3Forms) for the OTP
4. Check Render logs for:
   ```
   web3forms_service: OTP accepted for delivery recipient=...
   _send_superadmin_otp: delivered via Web3Forms to=...
   ```
5. Complete the reset flow to confirm end-to-end

If Web3Forms is not configured (key missing), logs will show:
```
_send_superadmin_otp: WEB3FORMS_ACCESS_KEY not set — using send_otp_email fallback
```
And the existing SMTP/MailerSend chain runs as before — no regression.
