# Phase 2 — Shared UI component system

Source implementation completed: 2026-07-14

## Delivered

- Added a stable Jinja macro library covering actions, fields, feedback, containers, overlays, navigation, and data-display contracts.
- Added token-only shared component CSS with dark/light semantics, minimum touch targets, responsive tables/dialogs, and reduced-motion behavior.
- Added an external interaction controller for native dialogs/drawers, focus containment/return, dropdowns, tabs, switches, password controls, file labels, command filtering, dismissible feedback, mobile navigation, and opt-in submit guards.
- Programmatic notifications now use allowlisted variants plus DOM `textContent`; the shared controller contains no HTML injection APIs or code evaluation.
- Added a superadmin-only `/superadmin/component-system` gallery with synthetic labels, long copy, validation, loading, empty, error, table, overlay, and keyboard states.
- Migrated admin/superadmin flash feedback and destructive confirmation to shared macros. The tenant admin inherits that shell.
- Moved tenant notification polling out of the admin template and unified badge behavior in `dashboard-shell.js`.
- Rebuilt the 2FA verification page as an external-CSS/JS shared-component pilot while preserving its endpoint, CSRF field, `code`, and `backup_code` names.
- Migrated OAuth setup theme bootstrap/feedback away from inline implementation.
- Added a component API guide, explicit deprecation registry, pinned jsdom test dependency, source guards, and DOM interaction tests.

## Compatibility and rollback

No database, endpoint, form-name, webhook, or public URL contract changed. Legacy selectors remain available; new `ui-*` names are additive. The existing `confirmDelete` API delegates to the native shared dialog, so current list-page triggers continue to work.

Rollback by restoring the previous shell flash/modal markup and 2FA template, then removing the component CSS/JS links. No data rollback is required. Keep the additive macros/assets inert for one rollback release.

## Automated evidence

- `python tools/lint_design_tokens.py`: pass for 4 Phase 1/2 CSS modules.
- `npm run test:ui`: 4/4 pass.
- `python -m unittest tests.test_phase2_component_system -v`: 10/10 pass.
- `python -m unittest tests.test_phase1_design_system -v`: 9/9 pass.
- `node --check` for component, auth 2FA, dashboard shell, and admin scripts: pass.
- `python -m compileall -q app tools tests/test_phase2_component_system.py`: pass.
- Migrated-page scan: no inline script bodies, styles, handlers, or unsafe component DOM APIs.

## Deployment-gated evidence

The deployable Flask/Jinja/browser stack is unavailable in this workspace. Before release:

1. Render every macro with malicious strings, long translations, empty values, and server validation errors under Flask autoescaping.
2. Run axe and CSP enforcement on the gallery, 2FA, OAuth setup, and both dashboard shells.
3. Verify keyboard-only dialog/dropdown/tab navigation, focus return, outside click, screen-reader labels, forced colors, reduced motion, and 200% zoom in the supported browser matrix.
4. Compare characterized screenshots and form submissions for the migrated pages.
5. Measure legacy selector/runtime usage; do not delete registry entries without one release at zero usage.

jsdom verifies deterministic interaction state but is not a browser accessibility certification.
