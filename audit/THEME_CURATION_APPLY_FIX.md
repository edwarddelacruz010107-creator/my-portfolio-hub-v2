# Theme Curation and Apply Fix

## Retained production themes

- `default` — Default Clean
- `developer_pro` — Developer Pro
- `blockform_brutal` — Blockform Brutal
- `schematic_spec` — Schematic Spec

All other theme folders and static preview folders were removed, including `futuristic_cyber`.

## Theme apply reliability fixes

- Added a server-rendered POST form for every Apply Theme button, so switching still works when JavaScript or an inline script is blocked.
- Kept AJAX enhancement for progress/toast feedback, but now requires an explicit JSON success response.
- Added CSRF token directly to each apply form.
- Theme selection is written to all duplicate profile rows belonging to the same tenant, then reloaded from the canonical profile and verified.
- Canonical profile lookup now prefers the Profile row whose `tenant_id` matches the core Tenant row.
- Public default and tenant portfolio renderers use the same canonical profile lookup as Admin.
- Public portfolio cache is cleared after switching.
- Retired database theme selections are normalized to `default` when the picker is opened.

## Catalog cleanup

Superadmin **Theme Catalog → Sync from Disk** now:

- registers only the four curated themes;
- removes catalog rows for retired themes;
- resets portfolios still pointing to retired themes back to `default`;
- clears the theme metadata cache.
