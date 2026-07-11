# Mobile Dashboard and Project Responsive Fix

## Scope

- Superadmin mobile sidebar/burger interaction
- Tenant/Admin mobile sidebar/burger interaction
- Landing-page project cards on phones
- Public project case-study pages for tenant and administrator portfolios

## Root cause

The sidebar interaction lived in inline JavaScript inside the Admin and Superadmin base templates. Production Content Security Policy can block inline scripts, leaving the burger visible but inactive. Admin and Superadmin also used different mobile breakpoints, which created inconsistent behavior between 768 px and 900 px.

## Changes

- Added `app/static/js/dashboard-shell.js` as a same-origin CSP-safe controller.
- Added `app/static/css/dashboard-mobile.css` and standardized the mobile drawer breakpoint at 900 px.
- Added accessible focus management, Escape handling, backdrop close, route-link close, orientation handling, ARIA state, and scroll locking.
- Moved toast, modal, and notification shell behavior into the external script.
- Added cache-versioned dashboard assets.
- Improved landing project card spacing, cover ratios, badges, actions, metadata, text wrapping, and touch behavior.
- Improved public case-study layout for narrow screens, including hero typography, project media, rich text, tables, embeds, comparison media, action buttons, side panels, and related-project cards.

## Validation

- Python compilation passed.
- JavaScript syntax validation passed.
- 127 Jinja templates parsed successfully.
- No `__pycache__`, `.pyc`, or `.pyo` files remain.
