# Theme Marketplace Architecture

## Scope

Phase 7 turns the existing five-theme registry into an installed marketplace without introducing a second registry or accepting arbitrary theme uploads. The supported IDs remain:

- `default`
- `developer_pro`
- `blockform_brutal`
- `schematic_spec`
- `developer_journal`

`app/theme_engine.py` is the sole filesystem registry and `ThemeCatalogEntry` remains its database overlay. Unknown, retired, incomplete, inactive, or plan-disallowed IDs fail closed or resolve to the default theme.

## Versioned theme contract

Every installed `theme.json` declares manifest schema `1.0.0` and compatibility contract `theme-contract-1.0`. The contract requires:

- stable ID and semantic theme version;
- a safe portfolio template entry point;
- self-hosted, declared style, script, and preview assets;
- the complete portfolio section capability matrix;
- typed configurable tokens;
- a CSP declaration with no remote hosts, inline scripts, or event handlers;
- screenshot provenance labeled as a design fixture;
- compatibility and migration notes.

`app/services/themes/contract.py` performs read-only validation. `ThemeEngine.init_app()` validates all supported themes before installing the Jinja loader and fails startup when an installed contract is invalid. The validator never writes metadata or repairs files at runtime.

## Asset and execution policy

Theme scripts and styles are self-hosted. Executable inline scripts, inline event handlers, remote script/style URLs, user-controlled CSS declarations, `|safe`, Tailwind CDN, Picsum images, fake projects, and placeholder resumes are not part of the installed theme path. JSON hydration blocks are allowed only with `type="application/json"`; external scripts parse them and build UI with safe DOM APIs.

Shared theme components provide the runtime accessibility contract, customization stylesheet link, canonical portfolio SEO, and privacy/legal navigation.

## Public preview policy

Anonymous previews use an in-memory design fixture and never query a tenant profile. They are labeled “Design fixture preview,” carry `noindex` headers, contain no live project or credential URLs, and receive an empty contact action. Preview JavaScript disables submission when no real contact endpoint exists.

Bundled preview SVGs carry manifest provenance `labeled_design_fixture`. Public cards always use these installed assets; catalog uploads are not treated as anonymous proof of customer content.

## Selection and marketplace signals

Selection reuses the existing tenant profile column and plan gate. The profile update is committed and re-read before success is reported, covering duplicate legacy profile rows for the same tenant. The legacy `install_count` field now records only actual selection changes, not repeated application of the active theme.

That counter is explicitly described as selection events rather than unique installations. A “popular” candidate is withheld below 25 selection events; public marketplace pages do not expose the operational counter. Trending and recently-updated labels are omitted because the current database does not contain defensible time-windowed unique-tenant signals.

## Typed customization lifecycle

Theme manifests expose allowlisted token definitions with one of three types:

- six-digit hexadecimal color;
- bounded length with an exact declared unit;
- allowlisted enum.

Unknown tokens, CSS functions, URLs, shorthand colors, delimiter injection, out-of-range lengths, and wrong units are rejected. Sanitized declarations are rendered under `:root` using only manifest-owned CSS variable names.

Each tenant/theme stream has one mutable private draft and an append-only sequence of published versions. Publishing snapshots the draft. Rollback creates a new immutable version and points the draft at the restored values; history is never rewritten. PostgreSQL uses a stream-scoped advisory transaction lock for version allocation, backed by a unique tenant/theme/version constraint.

The public stylesheet endpoint returns only the published tokens for an active tenant’s currently selected active theme. The authenticated admin endpoint returns a no-store draft stylesheet for previews. Neither endpoint accepts raw declarations from the request.

## Database contract

Migration `0061_theme_customization_history.py` follows `0060` and creates:

- `theme_customization_drafts` — unique `(tenant_id, theme_id)` mutable working copy;
- `theme_customization_versions` — unique `(tenant_id, theme_id, version_number)` immutable history.

No historical rows are fabricated. PostgreSQL adds a trigger rejecting updates and deletes on published versions; SQLAlchemy adds the same guard for supported ORM paths.

## Verification contract

`tests/test_phase7_theme_marketplace.py` runs without Flask or a database. It validates all five themes against empty, minimal, full, hostile-string, and long-content shapes, plus manifests, assets, CSP rules, token injection, isolation, append-only history, locking, plan validation, preview provenance, external UI assets, honest empty states, popularity thresholds, and unknown-ID rejection.

Deployment must still run the full application stack with PostgreSQL, Redis, Jinja/Flask rendering, and browser screenshots for empty/minimal/full portfolios at desktop and mobile widths. Deterministic source contracts do not replace that deployment gate.

## Rollback

Application rollback is safe while migration `0061` remains applied: older code ignores the additive tables and new manifest keys. If database rollback is required, first export customization versions, stop writes, deploy code that no longer references customization models/routes, then downgrade `0061`. Theme selection continues to use the pre-existing profile column throughout.
