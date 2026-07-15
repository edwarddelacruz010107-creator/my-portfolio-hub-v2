# Global design system contract

Status: Phase 1 platform contract, 2026-07-14

## Authority and load order

`app/static/css/design-system.css` is the authoritative platform token and typography entry point. Platform shells load it exactly once before their family-specific compatibility CSS. New platform modules consume the canonical semantic tokens; they must not create a new `:root` layer.

The intended order is:

1. synchronous `theme-bootstrap.js` in the document head;
2. `design-system.css`;
3. page-family compatibility CSS such as `main.css`, `public-design-system.css`, `admin.css`, or `superadmin.css`;
4. narrowly scoped page CSS.

The declared layer order is `reset, tokens, base, components, utilities, compatibility`. The current monolithic rules are intentionally retained for rollback safety; new modules are migrated into named layers page family by page family rather than by a global selector rewrite.

## Typography

| Role | Token | Family | Shipped weights |
|---|---|---|---|
| Brand, page title, display heading | `--font-display` | Syne | 400–800 |
| Body, form, navigation, table, system text | `--font-body` | DM Sans | normal 300–700; italic 300–500 |
| Code, IDs, numeric technical labels | `--font-mono` | JetBrains Mono | 400, 500, 700 |

Assets are self-hosted as WOFF2 under `app/static/fonts`. They are pinned from `@fontsource/syne` 5.2.7, `@fontsource/dm-sans` 5.2.8, and `@fontsource/jetbrains-mono` 5.2.8. Their OFL licenses are stored in `app/static/fonts/licenses`. Platform templates do not contact Google Fonts.

## Canonical semantic API

- Surfaces: `--color-canvas`, `--color-surface-1` through `--color-surface-4`, `--color-overlay`.
- Text: `--color-text-primary`, `--color-text-secondary`, `--color-text-tertiary`, `--color-text-disabled`.
- Interaction: `--color-brand`, `--color-brand-emphasis`, `--color-brand-strong`, `--color-focus-ring`, `--color-selected`, `--color-disabled-bg`, `--color-disabled-border`.
- Status: `--color-positive`, `--color-warning`, `--color-negative`, `--color-info`.
- Data: `--color-chart-1` through `--color-chart-5`.
- Providers: `--color-provider-paymongo`, `--color-provider-dodo`, `--color-provider-manual`.

Each semantic color has a dark and light value. Component CSS must select a semantic role, not duplicate a theme-specific hex value. Legacy names such as `--bg`, `--text`, `--accent`, `--success`, and the public `--ph-*` namespace resolve to these canonical roles during migration.

## Layout and behavior

- Containers: `--container-sm` through `--container-xl`, with `--content-gutter`.
- Documented breakpoints: `--breakpoint-sm` through `--breakpoint-xl`. CSS variables cannot be interpolated into media-query conditions, so the values are the review contract rather than direct query operands.
- Spacing: `--space-1` through `--space-12`.
- Radius: `--radius-xs` through `--radius-full`.
- Elevation: `--shadow-xs` through `--shadow-xl`, `--shadow-card`, and the z-index scale `--z-base` through `--z-toast`.
- Motion: `--dur-fast`, `--dur`, `--dur-slow`, `--ease`, `--ease-out`, `--ease-spring`; reduced-motion media rules collapse animations and transitions.
- Accessibility: minimum touch target `--touch-target-min`; focus uses `--color-focus-ring`; disabled controls use semantic disabled tokens without lowering text contrast via opacity.

## Theme boundary

Tenant portfolio themes retain their own visual identity and color variables. Every supported theme loads `theme-contract.css`, which supplies shared focus, inherited form typography, touch-target, hidden-state, reduced-motion, and forced-color behavior. It deliberately does not replace theme-owned palette or layout choices. Phase 7 will move this behavioral contract into versioned theme manifests.

## Theme persistence

`theme-bootstrap.js` validates only `light` or `dark`, tolerates unavailable storage, and falls back to the system preference. Each shell declares its storage key with `data-theme-storage`; legacy aliases are read with `data-theme-aliases`. Admin theme writes remain centralized in `theme-engine.js`.

## Enforcement and reference

Run `python tools/lint_design_tokens.py` for new platform CSS. It rejects competing roots, raw colors, raw spacing declarations, and un-tokenized font families in the Phase 1 pilot module. Expand its explicit path list when a new module joins the contract.

The superadmin-only `/superadmin/design-system` route renders tokens, typography, controls, fields, statuses, empty state, and responsive table behavior with synthetic labels. It never queries or invents business metrics.

Browser snapshots, automated contrast measurement, 200% zoom, and cross-browser font rendering require the deployable Flask/browser environment and remain release gates; source-level checks do not substitute for them.
