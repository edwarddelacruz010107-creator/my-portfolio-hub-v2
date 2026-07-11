# Tenant Email Services Production Fix

## Root cause

The tenant Email Services template used inline `onclick`/`onchange` handlers and a large inline script. Production Talisman emits a nonce-based Content Security Policy, so browser CSP enforcement blocked those handlers. The Test Connection, provider toggle, setup guide, drag ordering, and Save Order controls therefore appeared clickable but did nothing.

A second tenant-context risk existed because the routes used `current_user.tenant_id` even when a superadmin opened a tenant Studio. The page could target a different tenant from the one displayed.

## Fix

- Moved all behavior to `app/static/js/tenant-email-services.js`.
- Replaced inline handlers with `data-*` actions.
- Added CSRF-safe same-origin requests and robust non-JSON/session-expiry handling.
- Added test recipient selection and real loading/error feedback.
- Added drag ordering plus Up/Down controls for phones and tablets.
- Added a normal POST fallback for Save Order.
- Validated that all three providers appear exactly once before saving priority.
- Resolved the active tenant from Studio context for dashboard, save, toggle, priority, test, and status operations.
- Wrapped provider tests so production exceptions return a useful JSON error instead of an HTML 500 response.
- Providers are no longer marked Connected merely because credentials were saved; Connected now means a successful test.
