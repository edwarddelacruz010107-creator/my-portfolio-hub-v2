# Trial Billing Plan and History Fix

## Resolved

- Trial tenants no longer display Basic as their current subscription.
- Paid plan cards are not marked current while the tenant is still on trial.
- A clear trial-status banner shows remaining days and the trial end date.
- Trial lifecycle is recorded in subscription history as a zero-cost system trial.
- Existing accounts are backfilled lazily when billing plans or history is opened.
- New local, Google, GitHub, pending-signup, and Superadmin-created trial tenants receive a trial history row at creation.
- History is responsive and uses mobile-friendly cards.
- Billing plan interaction JavaScript was moved to a same-origin static file for production CSP compatibility.
- Plan cards, payment methods, billing cycles, banners, and prices adapt to tablet and mobile widths.

## Important behavior

`Profile.plan` remains a paid-plan fallback and is no longer treated as the active plan while `Tenant.subscription_state` is `trial`. Trial history rows use `status=trial` and are intentionally excluded from paid-subscription resolution.
