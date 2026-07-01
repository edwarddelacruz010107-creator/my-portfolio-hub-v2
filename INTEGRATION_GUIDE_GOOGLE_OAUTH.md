# Google OAuth (Sign-In) — Integration Guide

**Scope actually built:** Google Sign-In as a **second login method for existing,
SuperAdmin-provisioned tenant-admin users**. No public registration. No
auto-created tenants/users. Superadmin portal untouched (password + TOTP only).

---

## 1. Root-cause / architecture note (why scope was narrowed)

The original spec asked for open self-serve signup + Google auth with automatic
tenant creation. Audit of the actual codebase found:

- No `/register` route exists anywhere (`app/auth/__init__.py` only has
  login/2fa/logout/forgot-password/reset-password).
- Tenants + their one admin `User` are created **atomically by SuperAdmin**
  (`app/superadmin/routes/tenants.py:223-330`), with plan/trial/billing decided
  at creation time (`payment_method='admin-provisioned'`).
- `User.is_admin` defaults `True` and every user is tenant-scoped — there's no
  "customer" role for a self-serve signup to land in.

Building auto-provisioning as originally specified would have opened a path to
create tenants/subscriptions with no plan or payment gate. Scope was confirmed
with you and narrowed to: **Google Sign-In links to an account that already
exists.** Nothing about tenant provisioning changed.

---

## 2. Files changed

| File | Change |
|---|---|
| `requirements.txt` | + `Authlib>=1.3.0` |
| `config.py` | + `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_ENABLED` (computed) |
| `app/extensions.py` | + `oauth = OAuth()` singleton |
| `app/__init__.py` | `oauth.init_app(app)` + conditional `oauth.register('google', ...)` via OIDC discovery |
| `app/models/core.py` | `User` gains `google_id`, `auth_provider` (default `'local'`), `avatar_url` |
| `migrations/versions/0041_add_google_oauth_fields.py` | New idempotent migration for the 3 columns above, `down_revision = '0040_invoices'` (current head) |
| `app/auth/__init__.py` | Extracted `_authorize_and_login()` from `_handle_login()` (role/tenant/reset checks — now shared by password + Google paths, zero duplicated logic). `_render_login_page()` / `_handle_login()` gained `allow_google` param, default **True everywhere except `require_superadmin=True`**. Bottom of file imports `app.auth.oauth` to register its routes. |
| `app/auth/oauth.py` | **New.** `GET /auth/google/login`, `GET /auth/google/callback` |
| `app/superadmin/routes/core_auth.py` | Explicit `allow_google=False` on the superadmin login call (belt-and-suspenders on top of the default) |
| `app/templates/auth/login.html` | "Continue with Google" button + divider, rendered only when `show_google_login` is true |
| `env.production.template` | Documented the two new env vars |

No file outside this list was touched. Billing, tenant isolation, 2FA, admin/tenant/superadmin blueprints are unmodified.

---

## 3. Security properties (what's enforced, and where)

1. **No auto-provisioning** — `app/auth/oauth.py:google_callback()` looks up
   `User.query.filter_by(email=...)`; if `None`, it redirects with a flash and
   does **not** touch `db.session`. There is no code path from Google login to
   `Tenant()`/`Subscription()`/`User()` construction.
2. **Verified email required** — rejects if Google's `email_verified` claim is
   falsy, before any DB lookup.
3. **State/CSRF** — handled by Authlib (`authorize_redirect` / `authorize_access_token`)
   via the Flask session; not hand-rolled.
4. **Identity-hijack guard** — if the matched `User.google_id` is already set to
   a *different* `sub` than the one presented, the login is rejected rather than
   silently re-linked. Prevents a second Google account from taking over an
   existing tenant admin's row via a future OAuth flow.
5. **Superadmin is hard-blocked twice**: (a) the button is never rendered on
   `/superadmin/login` (`allow_google=False`), and (b) `google_callback()`
   explicitly rejects any matched user where `is_superadmin` is true, even if
   someone hits the URL directly. Superadmin stays password + TOTP only.
6. **Same authorization gate as password login** — `_authorize_and_login()` is
   the single function both flows call: `require_admin`, tenant-isolation
   (`user.tenant_slug != tenant_slug`), and `require_password_reset` are
   identical for both. This was a deliberate refactor (not a rewrite) so the
   two paths can't drift apart later.
7. **Account lockout respected** — `AccountLockout.is_locked()` is checked in
   the Google flow before any session is established, same as password login.
8. **2FA respected automatically** — `_complete_login()` (unchanged) branches
   on `user.totp_enabled` regardless of which flow called it. A Google-linked
   user with TOTP enabled still hits the 2FA step.
9. **Password auth is never disabled** — linking Google sets
   `auth_provider='both'`; `password_hash` is never cleared or touched.

---

## 4. Required setup (env vars)

```
GOOGLE_CLIENT_ID=<from Google Cloud Console → Credentials>
GOOGLE_CLIENT_SECRET=<from Google Cloud Console → Credentials>
```

Google Cloud Console → APIs & Credentials → OAuth 2.0 Client ID → **Web application**:
- Authorized redirect URI: `https://<your-domain>/auth/google/callback`
- For local dev also add: `http://localhost:5000/auth/google/callback`

Leave both blank in any environment (dev, staging) where you don't want the
feature live — `GOOGLE_OAUTH_ENABLED` computes to `False`, the button is
hidden, and `/auth/google/login` redirects to `/auth/login` with a flash
instead of a 500.

---

## 5. Deploy steps

```bash
pip install -r requirements.txt        # pulls in Authlib
flask db upgrade                       # applies 0041_add_google_oauth_fields
# set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET on Render
```

No data migration/backfill needed — all three new columns are additive and
either NULL or a safe default for every existing row.

---

## 6. Manual test checklist (recommend before merging to main)

- [ ] `GOOGLE_CLIENT_ID`/`SECRET` unset → button absent on `/auth/login`, `/<tenant>/auth/login`, `/<tenant>/admin/login`; **always** absent on `/superadmin/login`.
- [ ] With credentials set: existing tenant-admin's email → Google login succeeds, `google_id`/`auth_provider='both'` populated, subsequent password login still works.
- [ ] Google account whose email has **no** matching `User` row → rejected with the "ask your administrator" flash, no DB row created (check `users` table row count unchanged).
- [ ] Google email not marked `email_verified` by Google → rejected before lookup.
- [ ] Superadmin's email, via `/auth/google/login` directly (bypassing the hidden button) → rejected with "Superadmin accounts must sign in with a password."
- [ ] User with `totp_enabled=True` → Google login still routes to the 2FA step, not straight to dashboard.
- [ ] Locked-out user (`AccountLockout.is_locked`) → Google login rejected with the same lockout message as password login.
- [ ] Cross-tenant: tenant-B admin hitting tenant-A's `/auth/google/login` (or landing on tenant-A's callback via `next`) → tenant-isolation check in `_authorize_and_login` denies it, same as password flow.
