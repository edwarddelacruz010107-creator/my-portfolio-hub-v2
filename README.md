# Portfolio Hub — Auth Surface Unification Patch

## Apply
1. Overwrite `app/auth/__init__.py` with the version in this patch.
2. Overwrite `app/templates/auth/portal.html` with the version in this patch.
3. Add `app/templates/superadmin/login.html` (new file) to your project.
4. Delete the 3 files listed in DELETIONS.txt.
5. Restart the app. No `alembic upgrade` needed — see MIGRATION_NOTES.md.

## What this fixes
Your `/auth` portal (portal.html) already existed as the unified,
glassmorphism sign-in/register screen — but a FAILED login on
`/auth/login`, `/admin/login`, or any `/<tenant>/auth/login` silently
dropped the user onto a second, older, visually different template
(`auth/login.html`) instead of re-showing the unified portal with the
error. That's the real "two login pages" bug. This patch:

  - Makes every non-superadmin login failure re-render `auth/portal.html`
    (same design, same tabs, error now shows inline — no jarring template
    swap), with the form action correctly pinned to whichever endpoint
    served the request so tenant context isn't lost on resubmit.
  - Gives superadmin its own isolated, unlinked login template instead of
    either the old shared `login.html` or the public portal — deliberately,
    per your decision, to keep the platform-owner privilege tier off the
    public-facing, publicly-linked, rate-limit-shared auth surface.
  - Removes the 3 templates that are now (or already were) fully dead code.

See MIGRATION_NOTES.md for the full rationale and what was intentionally
left untouched (your OTP flow, email dispatcher, CSRF setup, and the admin
blueprint's canonical forgot-password implementation were already correct
and are not duplicated — no changes needed there).
