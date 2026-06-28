# SECRETS ROTATION REPORT — Portfolio CMS v5.2

**Generated:** 2026-06-18  
**Severity:** CRITICAL — Secrets were committed to version control

---

## Exposed Credentials

The `.env` file was committed with live production secrets. All credentials below **MUST be rotated immediately**.

> **Note:** Actual secret values are NOT printed here. The exposure was identified from the committed `.env` file. Treat all values as compromised.

---

## Rotation Checklist

### 1. `SECRET_KEY` (Flask session signing key)
- **File:** `.env`
- **Risk:** An attacker with this key can forge Flask sessions and cookies, bypassing authentication.
- **Action:**
  1. Generate new key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
  2. Set new value in Render dashboard → Environment Variables
  3. **All active sessions will be invalidated** (users must re-login — expected)

### 2. `FERNET_KEY` (API key encryption)
- **File:** `.env`
- **Risk:** Decrypts stored API keys in the database (PayMongo, etc.)
- **Action:**
  1. Generate new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  2. **Re-encrypt all stored API keys** before deploying — existing encrypted values will become undecryptable
  3. Set new value in Render dashboard

### 3. `SUPERADMIN_PASSWORD`
- **File:** `.env` — value was `superadmin12345!@`
- **Risk:** Full superadmin access to all tenants, billing, and settings
- **Action:**
  1. Immediately log into `/superadmin/login` and change the password
  2. Or: run `flask create-superadmin` with `SUPERADMIN_PASSWORD=<new-strong-password>` to reset
  3. Use a password manager to generate a 24+ character random password
  4. Enable TOTP 2FA on the superadmin account

### 4. `BETTERSTACK_HEARTBEAT_URL` + `HEARTBEAT_SECRET`
- **File:** `.env` — actual BetterStack URL exposed
- **Risk:** Attacker can send false heartbeats, suppressing alerts when the app goes down
- **Action:**
  1. Log into BetterStack → regenerate the heartbeat URL
  2. Set new `BETTERSTACK_HEARTBEAT_URL` and `HEARTBEAT_SECRET` in Render dashboard

### 5. `ADMIN_EMAIL`
- **File:** `.env` — `delacruzedward735@gmail.com` exposed
- **Risk:** Email address disclosed — phishing target, spam
- **Action:** Update email in Render dashboard; consider using a dedicated admin email address

### 6. `APP_BASE_URL`
- **File:** `.env` — `https://myportfoliohub.online` exposed
- **Risk:** Low (domain is public anyway) — but confirms target for attackers
- **Action:** No rotation needed; ensure WAF/rate limiting is enabled

---

## Files Containing Secrets (now redacted)

| File | Secret Keys Contained | Status |
|------|----------------------|--------|
| `.env` | SECRET_KEY, FERNET_KEY, SUPERADMIN_PASSWORD, BETTERSTACK_HEARTBEAT_URL, HEARTBEAT_SECRET, ADMIN_EMAIL | **REDACTED in output** |

---

## Prevention Measures

1. **Pre-commit hook** — Install `git-secrets` or `detect-secrets`:
   ```bash
   pip install detect-secrets
   detect-secrets scan > .secrets.baseline
   ```
2. **GitHub secret scanning** — Enable in repository settings
3. **`.gitignore`** — Verify `.env` and `.env.*` are ignored (already in `.gitignore` ✓)
4. **Render dashboard** — Set all secrets in Render env vars panel, not in the codebase

---

## Post-Rotation Verification

After rotating all secrets, verify:
```bash
# Check no secrets in git history
git log --all --full-history -- .env
git show HEAD:.env  # should show <REDACTED> values

# Verify health endpoint returns 200
curl https://your-domain.render.com/health

# Verify superadmin login works with new password
curl -X POST https://your-domain.render.com/superadmin/login \
  -d "username=superadmin&password=<new-password>"
```

