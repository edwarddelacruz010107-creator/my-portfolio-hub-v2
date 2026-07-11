# OAuth Account Setup and Editable Username Upgrade

## Purpose

This upgrade gives tenant administrators control over their local username and
adds a safe one-time setup flow for accounts created through Google or GitHub.

## Final behavior

- A new Google/GitHub signup creates the trial tenant and signs the user in.
- Before opening Studio, the user chooses a unique username and creates a local
  MyPortfolioHub password.
- The password is hashed with Werkzeug and is never stored as plain text.
- Later Google/GitHub sign-ins do not ask for the local password.
- When a verified OAuth email already belongs to an existing local account, the
  provider is linked and the user goes straight to Studio. No setup page is
  shown and the existing password is preserved.
- Tenant administrators can edit their username from Studio → Settings.

## Database changes

Migration `0054_oauth_local_account_setup` adds:

- `users.local_password_enabled`
- `users.oauth_setup_required`

Older provider-only accounts with legacy opaque password placeholders are
marked for the one-time setup automatically. Existing local accounts linked to
OAuth (`auth_provider='both'`) are not changed.

## Security controls

- CSRF-protected forms
- Password-policy validation
- Case-insensitive username uniqueness
- Restricted username character set
- OAuth identity matching remains unchanged
- Superadmin OAuth remains blocked
- Existing account-by-email linking remains password-free during OAuth login
- Admin routes block incomplete OAuth accounts until setup is finished

## Deployment

Run after deploying:

```bash
flask db upgrade
```

No new environment variable is required.
