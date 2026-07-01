# Auth System Upgrade — Audit Report

**Scope:** Add modern email + password signup, email verification, and Google
OAuth 2.0 sign-in to the existing Portfolio CMS SaaS platform WITHOUT
touching admin login, superadmin login, tenant isolation, sessions,
subscriptions, billing, dashboard access, or the permission system.

**Delivery format:** additive files + copy-paste patches. Nothing overwrites
an existing file. Every schema change is a NEW Alembic revision (`0041_add_google_oauth_fields`).

---

## What this patch changes

### New files (drop-in)

| Path | Purpose |
|---|---|
| `migrations/versions/0041_add_google_oauth_fields.py` | Adds nullable OAuth / verification columns to `users`; backfills existing rows to `email_verified=True`, `auth_provider='local'`. |
| `app/auth/oauth.py` | Authlib OAuth registry + `init_oauth(app)`. |
| `app/auth/routes_signup.py` | `/auth/register`, `/auth/verify-email/*`, `/auth/google`, `/auth/google/callback`. |
| `app/services/auth/registration_service.py` | Local signup (user + tenant + verification token). |
| `app/services/auth/verification_service.py` | Issue / verify / send email-verification tokens. |
| `app/services/auth/google_oauth_service.py` | Google account resolve / link / provision. |
| `app/templates/auth/register.html` | SaaS signup UI with Google button + password meter. |
| `app/templates/auth/verify_email_sent.html` | "Check your inbox" screen. |
| `app/templates/auth/verify_email_result.html` | Verification success / failure. |
| `app/templates/auth/_google_button.html` | Partial to include on login.html. |

### Copy-paste patches (manual, small)

1. **`app/models/core.py`** — inside `class User(...)`: paste the block from
   `app/models_patch/USER_MODEL_ADDITIONS.py`.
2. **`app/forms/__init__.py`** — append the block from
   `app/forms_patch/FORMS_ADDITIONS.py`.
3. **`app/auth/__init__.py`** — bottom-of-file: add one import line
   (see `app/auth/AUTH_INIT_ADDITIONS.py`, Edit 1).
4. **`app/__init__.py`** — inside `create_app`, immediately before
   `app.register_blueprint(auth_blueprint, url_prefix='/auth')`, add two lines
   (see `app/auth/AUTH_INIT_ADDITIONS.py`, Edit 2).
5. **`app/templates/auth/login.html`** — add `{% include "auth/_google_button.html" %}`
   near the top of the login form.
6. **`requirements.txt`** — append `Authlib>=1.3.0`.
7. **`.env`** / secrets — add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   optionally `GOOGLE_REDIRECT_URI`.

### Not touched (verified)

- `app/auth/__init__.py`: `login`, `verify_2fa`, `logout`, `forgot_password`,
  `reset_password`, TOTP lockout helpers, tenant stamping, session hygiene.
- Superadmin blueprint, superadmin login, superadmin OTP flow.
- Admin blueprint, admin OTP forgot-password flow.
- Tenant blueprint, tenant isolation checks, `RESERVED_SLUGS`.
- Billing / subscription / discount / invoice code.
- `AccountLockout`, `PasswordPolicy`, `log_security_event`, `log_activity`.

---

## Safety rules enforced in code

1. **Superadmin cannot be created via signup.** `registration_service` and
   `google_oauth_service` HARD-CODE `is_superadmin=False`.
2. **Superadmin cannot sign in with Google.** `google_oauth_service`
   raises `GoogleAuthError` and emits a `critical` security-log event if a
   Google callback resolves to (or attempts to link) a superadmin.
3. **Signup never joins the default tenant.** A fresh tenant is provisioned
   per signup with a unique, non-reserved slug.
4. **Google account linking preserves tenant + billing.** If a local user
   already exists with the same email, we set `google_id` + `email_verified`;
   `tenant_id`, `tenant_slug`, `is_admin`, subscriptions, and billing state
   are untouched.
5. **Email-verified check.** Google callbacks with `email_verified=false`
   are refused, so an attacker cannot squat on an unverified Google email.
6. **CSRF.** All new forms are Flask-WTF (project's default CSRF is on).
7. **Rate limiting.** All new routes use the existing `limiter` with
   sensible per-minute + per-hour caps.
8. **OAuth state + nonce.** Handled by Authlib; not reimplemented.
9. **Open redirect protection.** OAuth `next` parameter passes through the
   existing `_is_safe_url` check.
10. **Token storage.** Email verification tokens are stored as sha256(raw),
    matching the pattern already used by `password_reset_service.py`.

---

## Migration behaviour

`0041_add_google_oauth_fields`:

- All new columns are nullable OR have safe server defaults.
- Post-`add_column` backfill: every existing user is set to
  `email_verified = TRUE` and `auth_provider = 'local'`, so no legacy
  admin or superadmin gets locked out.
- `google_id` and `email_verification_token` unique indexes are partial
  (`WHERE ... IS NOT NULL`) on PostgreSQL, so multiple NULLs are allowed.
- `downgrade()` drops only the new columns + indexes; nothing else is
  touched.

Run:
```bash
flask db upgrade
```

---

## Manual test matrix

| Case | Expected |
|---|---|
| Existing admin logs in via `/auth/login` | Works unchanged, dashboard loads. |
| Existing superadmin logs in via `/superadmin/login` | Works unchanged. |
| Tenant admin logs in via `/<slug>/auth/login` | Works unchanged. |
| `/auth/register` new email | Account + tenant created; verification email sent; redirected to "check inbox". |
| Verification link | Marks `email_verified=True`, one-time use, expires in 24h. |
| Resend verification | Rate-limited, does not disclose account existence. |
| `/auth/google` first time | Creates user + tenant with `auth_provider='google'`, logs in. |
| `/auth/google` again | Fast path via `google_id`, logs in. |
| `/auth/google` with email that matches an existing local user | Links `google_id`, preserves tenant + subscriptions. |
| `/auth/google` with email matching a superadmin | Rejected; critical security log entry; no session created. |
| Google callback with `email_verified=false` | Rejected. |
| Missing `GOOGLE_CLIENT_ID` | Google routes redirect to `/auth/login` with a friendly flash; button hidden on templates. |
| 2FA-enabled user password login | Existing 2FA flow untouched. |
| Password reset | Existing OTP + link flows untouched. |
| Logout | Existing session teardown untouched. |

---

## Install order

```bash
# 1. Deps
pip install -r requirements.txt          # Authlib picked up

# 2. Apply the copy-paste patches (steps 1–5 above)

# 3. Schema
flask db upgrade                          # runs 0041

# 4. Secrets
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
# GOOGLE_REDIRECT_URI is optional; if set, it MUST match the Google Console value.

# 5. Boot as usual (gunicorn / flask run)
```

Google Cloud Console → OAuth 2.0 Client → **Authorized redirect URIs** must
contain the exact callback:
```
https://<your-domain>/auth/google/callback
```
Add `http://localhost:8000/auth/google/callback` for local dev.

---

## Known follow-ups (not in this patch, by design)

- "Remember me" already flows through Flask-Login. The DB column
  `remember_token` was added for future token rotation but is unused today.
- Login-activity alert emails (new IP / new UA) — the fields `last_login_ip`
  and `last_login_user_agent` are now populated on every login; wiring an
  alert email is a separate follow-up.
- Sign-in with Apple / Microsoft — the OAuth registry is trivially
  extendable in `app/auth/oauth.py`.
