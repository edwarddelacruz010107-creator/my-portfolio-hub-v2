# Component deprecation registry

Status: expand/migrate/contract registry, 2026-07-14

| Legacy surface | Shared replacement | Owner / planned migration | Removal gate |
|---|---|---|---|
| `.btn*`, `.icon-btn`, `.ph-btn*`, `.domain-btn*` | `ui.button`, `ui.icon_button` | Phase 2 page families; Phases 5/7 for billing/themes | Characterization parity and zero rendered usage for one release. |
| `.form-input`, `.form-select`, page-local field wrappers | `ui.input`, `ui.select`, `ui.textarea`, `ui.choice`, `ui.switch`, specialized inputs | Tenant forms in Phase 2; billing/AI forms in Phases 5/8 | Form names, validation, autocomplete, and hostile-string render tests pass. |
| `.toast*`, `.flash*`, `showToast` implementations | `ui.toast`, `MPHUI.notify` | Shells migrated; page scripts during their owning phase | No page creates toast HTML or defines a competing notifier. |
| `.alert*`, `.alert-inline`, page-local validation banners | `ui.alert`, `ui.field_errors` | Auth pilot migrated; remaining pages with their domain phase | Role/live-region and visual parity verified. |
| `.modal*`, `.proof-modal*`, `.review-modal*` | `ui.dialog`, `ui.confirmation_dialog`, `ui.drawer` | Shell confirmation migrated; billing review in Phase 5 | Focus entry/containment/return, Escape, backdrop, auth, and submit idempotency pass. |
| Page-local dropdown/tab/accordion scripts | `ui.dropdown`, `ui.tabs`, `ui.accordion` + `components-v1.js` | Superadmin/billing/theme screens | Keyboard and outside-click tests plus one-release zero usage. |
| `.empty-state*`, `.skeleton*`, stat/table/card variants | Shared feedback/container macros | Phases 3–9 as each data surface is rebuilt | Honest empty/loading/error parity and responsive snapshots. |
| Admin/superadmin sidebar/topbar markup | `ui.sidebar`, `ui.topbar`, navigation macros | Incremental shell contract after route-map/browser parity | Active-route, mobile drawer, role, impersonation, and notification behaviors pass. |
| Theme-local shared behavior | Theme contract components | Phase 7 | Every installed theme passes the manifest browser suite. |

Deleting by class-name search alone is prohibited. The same legacy class can be emitted dynamically or owned by a theme. Use route/template characterization, rendered usage measurement, and a rollback release.
