# Phase 1 — Global design system

Source implementation completed: 2026-07-14

## Delivered

- Established `design-system.css` as the canonical platform token and typography entry point, with an explicit layer order and semantic dark/light color API.
- Added semantic canvas/surface/text, brand, focus, selected, disabled, status, chart, and Dodo/PayMongo/manual provider roles.
- Added container, gutter, breakpoint documentation, spacing, radius, elevation, z-index, type, touch-target, motion, and reduced-motion contracts.
- Self-hosted version-pinned Syne, DM Sans, and JetBrains Mono WOFF2 assets with their OFL licenses. Platform templates no longer request Google Fonts.
- Made `main.css` and `public-design-system.css` compatibility consumers of the canonical API, and migrated legacy landing/default typography roles to supported local families.
- Added an external pre-paint `theme-bootstrap.js` with validated storage values, alias support, storage-failure tolerance, and system preference fallback.
- Moved the public shell's theme/navigation behavior out of the template into `public-shell.js`.
- Added a shared accessibility/behavior contract to all five supported portfolio themes while preserving theme-owned visual identity.
- Added a superadmin-only `/superadmin/design-system` reference using synthetic labels and external CSS only.
- Added `DESIGN_SYSTEM.md`, the explicit `DESIGN_TOKEN_DEPRECATIONS.md` registry, and a reusable token lint for new platform CSS.

## Compatibility and rollback

This phase has no database or public URL changes. Legacy CSS roots and selectors remain in place where a verified page-family migration has not occurred; the registry assigns their removal to Phases 2 and 7. The platform shells load the new entry point before family-specific styles, allowing existing selectors to override during the expansion window.

Rollback by removing the new design-system/theme-bootstrap links and restoring the previous remote-font tags. Retain the local font files and documentation; they are inert in an older application image. No data rollback is needed.

## Automated evidence

- `python tools/lint_design_tokens.py`: pass.
- `python -m unittest tests.test_phase1_design_system -v`: 9/9 pass.
- `python -m unittest tests.test_phase0c_routing_readiness_content -v`: 7/7 pass.
- `python -m unittest tests.test_deterministic_migrations -v`: 6/6 pass.
- `python -m compileall -q app tools tests/test_phase1_design_system.py`: pass.
- `node --check app/static/js/theme-bootstrap.js`: pass.
- `node --check app/static/js/public-shell.js`: pass.
- Platform-template Google Fonts scan: zero matches.

## Deployment-gated evidence

The workspace does not contain the deployable Flask dependency set or a browser runner. The following remain mandatory before release:

1. Render the reference route and representative public/admin/superadmin pages in light/dark at mobile, tablet, and desktop widths.
2. Capture approved visual snapshots for long labels, validation, empty, loading, and error states.
3. Run automated WCAG 2.2 AA contrast and axe checks, keyboard focus traversal, forced colors, reduced motion, and 200% zoom.
4. Verify WOFF2 responses, cache headers, CSP, and no remote font requests in the production browser network log.
5. Confirm legacy theme selection/storage behavior across current supported browsers.

Source-level checks are regression guards and do not constitute visual or accessibility certification.
