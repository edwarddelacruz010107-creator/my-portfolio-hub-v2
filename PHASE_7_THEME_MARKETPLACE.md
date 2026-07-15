# Phase 7 — Theme Marketplace

Status: complete at the deterministic verification boundary on 2026-07-15.

## What changed

- Upgraded all five installed theme manifests to a versioned compatibility, asset, section, CSP, provenance, and typed-token contract.
- Added startup contract validation and retained the curated installed-theme allowlist.
- Externalized installed theme CSS and executable JavaScript, removed remote theme assets, and added shared runtime, customization, SEO, and legal partials.
- Rebuilt Developer Pro around real serialized portfolio data, safe DOM construction, honest empty states, conditional resume/social links, and escaped biographies.
- Added a provenance-labeled anonymous design-fixture preview that cannot deliver contact submissions or link to fake project destinations.
- Replaced the admin theme picker with an accessible installed marketplace supporting search, category filtering, preview, plan-aware selection, and customization entry points.
- Added tenant-scoped customization drafts, immutable publish history, rollback-as-new-version, typed sanitization, and public/draft CSS endpoints.
- Ensured selection counters increment only for actual changes and documented them as selection events. Popularity remains hidden below 25 events; unsupported trending/recent signals are omitted.
- Removed unsupported education/achievement placeholder schemas from the live theme context.
- Added migration `0061` without backfill or destructive schema changes.

## Why it changed

The previous theme surface mixed file metadata, catalog overrides, large inline implementations, unlabeled sample previews, and selection counters that could rise when reapplying the same theme. Customization had a plan flag but no durable draft/publish history. Phase 7 establishes one installed registry, one validated theme contract, one safe token pipeline, and evidence boundaries for preview and marketplace claims.

## Risk assessment

- Database risk: low to moderate. Migration `0061` is additive and contains no data backfill; PostgreSQL immutability triggers must be verified in staging.
- Rendering risk: moderate. All five templates changed asset loading and shared partial usage. Startup validation fails closed on malformed deployments.
- Authorization risk: low after controls. Customization routes derive tenant identity from the authenticated session/profile; public CSS requires an active core tenant and matching selected theme.
- XSS/CSP risk: reduced. Typed CSS, autoescaped content, JSON hydration, external scripts, no theme event handlers, and no remote theme assets are enforced by tests.
- Analytics risk: low. The legacy counter is retained for compatibility but is relabeled as selection events and no public popularity claim is emitted without the minimum threshold.
- Operational limitation: the current workspace lacks Flask, Jinja, SQLAlchemy, PostgreSQL, Redis, and a browser runtime. Full runtime and visual checks remain deployment gates.

## Automated verification

- 79 `unittest` contracts across Phases 0C–5 and 7: passed.
- 16 Phase 6 deterministic scoring contracts: passed.
- 4 JavaScript DOM/component contracts: passed.
- Total deterministic contracts: 99 passed.
- Phase 7 theme tests: 17 passed, including five themes across empty, minimal, full, hostile, and long content.
- Installed theme manifest validator: 5 passed.
- Design-token lint: 7 registered CSS files passed.
- Python compile/AST checks: passed.
- JavaScript syntax checks across platform and installed theme scripts: passed.

## Regression checklist

- [x] Existing theme IDs and selected-theme storage remain compatible.
- [x] Trial/free/pro/administrator plan checks remain centralized in `ThemeEngine`.
- [x] Invalid or retired theme IDs do not reach filesystem selection.
- [x] Existing public preview route remains available and is now safer.
- [x] Admin preview remains read-only and uses real tenant data plus private draft CSS.
- [x] Public portfolios load published customization only for the selected theme.
- [x] Contact previews cannot submit to a real endpoint.
- [x] SEO partial is loaded by all five themes.
- [x] Theme assets are local and declared.
- [x] Prior deterministic and DOM tests remain green.

## Manual testing checklist

- [ ] Apply migration `0061` to a PostgreSQL staging copy and verify downgrade on a disposable clone.
- [ ] Start the complete Flask application with production-like Redis and database configuration.
- [ ] Confirm startup refuses one intentionally invalid copied manifest in a non-production test environment.
- [ ] For every plan tier, verify picker locks, preview access, selection, and customization access.
- [ ] For every installed theme, render empty, minimal, full, hostile-string, and long-content tenants.
- [ ] Capture desktop, tablet, and mobile screenshots; inspect navigation, empty states, focus, contrast, motion reduction, and long-text wrapping.
- [ ] Save a draft, preview it, publish it, reload the live portfolio, publish again, roll back, and verify immutable history.
- [ ] Attempt cross-tenant draft/version/CSS access and confirm denial or 404.
- [ ] Verify anonymous design-fixture contact forms are disabled and real tenant contact forms still deliver through the canonical service.
- [ ] Confirm Dodo, PayMongo, authentication, analytics, SEO, landing, Admin, SuperAdmin, and tenant dashboards remain healthy.

## Rollback strategy

1. Disable theme customization entry points at the application layer while leaving existing published CSS readable.
2. Deploy the previous application version; additive manifest keys and migration tables are ignored by older code.
3. If schema rollback is required, export customization history, stop customization writes, and downgrade `0061` only after code no longer imports the new models.
4. Keep `selected_theme` unchanged. If a theme asset deploy is incomplete, the existing engine fallback resolves the public portfolio to `default`.
