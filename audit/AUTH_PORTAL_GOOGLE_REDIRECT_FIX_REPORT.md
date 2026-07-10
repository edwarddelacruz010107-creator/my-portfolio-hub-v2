# Auth Portal + Google OAuth Redirect Fix Report

## Issues fixed

1. **Sign In Google redirect mismatch**
   - The Sign In tab used `/auth/google/callback` while the Create Account flow used `/auth/google/signup/callback`.
   - Production OAuth setups often had the signup callback registered but not the login callback, causing Google `redirect_uri_mismatch`.

2. **Create Account tab/link depended too much on JavaScript**
   - The Sign In tab and lower "Create an account" link used button/hash switching only.
   - If JavaScript was delayed or blocked, switching tabs could fail.

3. **Duplicate form input IDs**
   - Sign In and Create Account forms both used `portal-username`.
   - This could confuse labels, autofill, browser validation, and scripts.

## Changes made

- Added production canonical Sign In OAuth routes:
  - `/auth/google/signin`
  - `/auth/google/signin/callback`

- Kept old routes for backward compatibility:
  - `/auth/google/login`
  - `/auth/google/callback`

- Updated the Sign In Google button to use:
  - `/auth/google/signin`

- Updated OAuth redirect URI generation to use `APP_BASE_URL` when configured.
  - This avoids wrong `http`/domain values behind production proxies.

- Updated Auth Portal tabs to real links:
  - `/auth/?tab=signin`
  - `/auth/?tab=signup`

- Updated lower helper links to real links too:
  - "Create an account"
  - "Sign in"

- Kept JavaScript tab switching for smooth UX, but now the page still works without JavaScript.

- Fixed duplicate signup field IDs:
  - `portal-signup-username`
  - `portal-signup-email`

## Production setup required in Google Cloud Console

Add these Authorized redirect URIs to your Google OAuth Client:

```text
https://myportfoliohub.online/auth/google/signin/callback
https://myportfoliohub.online/auth/google/signup/callback
```

Optional legacy URI if older links are still being used:

```text
https://myportfoliohub.online/auth/google/callback
```

Also set this environment variable in production:

```env
APP_BASE_URL=https://myportfoliohub.online
```

## Validation

- `python -m py_compile` passed for auth route files.
- `auth/portal.html` parsed successfully with Jinja2.
- Superadmin templates still parse successfully.
