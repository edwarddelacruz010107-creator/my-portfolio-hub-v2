# Shared UI component system

Status: Phase 2 contract v1, 2026-07-14

## Entry points

- Jinja macros: `app/templates/components/ui.html`
- Token-only styles: `app/static/css/components-v1.css`
- External behavior: `app/static/js/components-v1.js`
- Superadmin gallery: `/superadmin/component-system`
- Interaction tests: `tests/js/components_v1.test.js`

The four platform bases load the CSS and JavaScript once. Standalone pages opt in while they are migrated. All subsequent product phases must use this contract for new UI rather than adding page-local equivalents.

## Macro families

| Family | Macros |
|---|---|
| Actions | `button`, `icon_button` |
| Inputs | `input`, `password_input`, `otp_input`, `search_input`, `currency_input`, `textarea`, `select`, `choice`, `switch`, `file_upload`, `field_errors` |
| Feedback | `alert`, `toast`, `badge`, `inline_status`, `progress`, `skeleton`, `empty_state` |
| Containers | `card`, `stat_card`, `data_table`, `responsive_list`, `tabs`, `tab_panel`, `accordion`, `pagination` |
| Overlays | `dialog`, `confirmation_dialog`, `drawer`, `dropdown`, `command_palette` |
| Navigation | `breadcrumbs`, `topbar`, `sidebar`, `account_menu`, `notification_bell`, `mobile_nav_toggle` |
| Data display | `provider_mark`, `money`, `timestamp`, `trend_value`, `chart_shell` |

Caller blocks are used where arbitrary child markup is required. Stable parameters cover state and variants; arbitrary raw attribute strings are intentionally not accepted.

## Security and escaping

Macro parameters rely on Jinja autoescaping. The component file contains no `safe` filter, `Markup` conversion, inline event handler, script, or style attribute. URLs are emitted only into fixed `href`/form contexts and remain subject to the calling controller's route/authorization rules.

Programmatic notifications create DOM nodes and assign `textContent`; they never interpolate messages into HTML. Variants are allowlisted. The command search reads text only. No component evaluates code or consumes stored arbitrary URLs.

The caller is responsible for:

- passing server-form validation messages as strings;
- formatting money server-side from fixed-scale domain values (the macro never calculates money);
- passing authorized named-route URLs;
- never using `|safe` around component output or user-provided input;
- preserving CSRF fields in forms.

## Accessibility and behavior

- Controls use semantic elements, associated labels, `aria-describedby`, `aria-invalid`, live-region roles, and minimum touch targets.
- Dialogs use native `dialog` with a labeled surface, focus entry, Tab containment, Escape/backdrop close, and focus return.
- Dropdowns expose expanded state, close on Escape/outside click, and support Up/Down focus movement.
- Tabs use roving tabindex, connected panels, Arrow/Home/End keys, and deterministic hidden state.
- Accordion uses native `details`/`summary` and works without JavaScript.
- Switch state is mirrored to a named hidden input.
- Password visibility exposes pressed state and updates its accessible label.
- Motion is tokenized and disabled by reduced-motion preferences.
- Mobile rules preserve readable dialogs, scrollable tables, and 44px controls.

## Loading, empty, and error contract

Buttons expose `disabled` plus `aria-busy` while loading. Forms can opt in to `data-ui-submit-guard`. Skeletons have status labels. Alerts select assertive roles only for errors. Chart shells render explicit `loading`, `empty`, `error`, or ready states and never substitute fabricated values.

## Migration status

Phase 2 migrated:

- admin and superadmin shell flash messages;
- admin and superadmin confirmation dialogs;
- shared notification polling out of the admin template;
- the two-factor verification auth page;
- OAuth setup feedback and theme bootstrap;
- the component and design reference surfaces.

The tenant admin shell inherits the migrated admin base. Billing, analytics, and public bases now load the contract for subsequent phase work. Remaining selector families are tracked in `COMPONENT_DEPRECATIONS.md` and are not removed without characterized zero usage.

## Verification

Run:

```text
python tools/lint_design_tokens.py
npm ci
npm run test:ui
python -m unittest tests.test_phase2_component_system -v
```

The DOM tests cover malicious notification strings, allowlisted variants, dialog focus return, Escape, dropdown state, tab keyboard navigation, switch form mirroring, and password state. A deployable browser remains required for axe, computed touch targets, visual snapshots, 200% zoom, and CSP reports.
