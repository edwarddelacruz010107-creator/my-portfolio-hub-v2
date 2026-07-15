# Portfolio Intelligence

## Canonical owner

`app/services/intelligence/domain.py` is the only scoring implementation. It is a pure, versioned rubric with no Flask, database, network, filesystem, crawler, or theme-renderer dependency. `intelligence_service.py` is the tenant-scoped adapter that collects stored facts and persists append-only snapshots. The SEO editor consumes the SEO dimension from this same service; it does not calculate a competing score.

Rubric version `portfolio-intelligence-2026.07-v1` weights ten dimensions:

| Dimension | Weight | Stored evidence |
|---|---:|---|
| Profile | 15 | Identity, biography length, location, public email, profile image |
| Projects | 15 | Published count, descriptions, outcomes/client evidence, destination fields |
| Services | 8 | Visible count, descriptions, feature/deliverable fields |
| Testimonials | 8 | Visible count, attribution, substantive content |
| Certificates | 7 | Visible count, credential evidence, description/skills context |
| Experience | 8 | Visible count, dates, responsibilities/achievements |
| SEO metadata | 14 | Title/description lengths, social image, indexability setting |
| Accessibility fields | 10 | Alternative text for stored profile/project images |
| Contact readiness | 8 | Canonical internal inquiry inbox and public reply email |
| Freshness | 7 | Latest stored content timestamp compared with the 90-day rubric boundary |

The total is the weighted mean of evaluated dimensions. A dimension without applicable stored evidence is excluded from the denominator rather than assigned zero. Each scored check exposes its exact stored-fact evidence, available points, status, explanation, and allowlisted editor route. Failed scored checks become recommendations sorted deterministically by impact, effort, points, and key.

## Evidence boundaries

Alternative-text fields are deterministic because image and alt values are stored together. Rendered heading order, rendered link labels, and computed contrast are `not_evaluated` until Phase 7 supplies validated theme manifests and rendered contract scans. The UI does not claim a crawler, external SEO audit, browser accessibility scan, or performance test ran.

The internal inquiry inbox is the canonical contact destination and is evaluated independently of optional external delivery. A disabled optional provider is shown as unavailable, not as failed delivery. A selected but incomplete provider is reported from its real configuration state and never exposed with its secret.

## Recalculation, caching, and history

Relevant content writes converge through the post-commit `log_activity` hook and call `recalculate_after_write(tenant_id)`. The collector filters every tenant-owned query by the authenticated tenant ID. Snapshot uniqueness is `(tenant_id, portfolio_hash, rubric_version)`, with an integrity-safe concurrent-writer path.

The hash contains canonical stored facts and only the freshness state that can change the score. It therefore remains stable across ordinary days and changes at the 90-day boundary, avoiding artificial daily history. Migration `0060_portfolio_intelligence.py` inserts no rows: history begins only when this feature evaluates real portfolio state after deployment. PostgreSQL and ORM guards reject snapshot updates/deletes.

## Release and rollback

1. Apply core migration `0060` after `0059`; it is an additive table-only deployment.
2. Deploy the application and verify a known tenant creates one snapshot on the first intelligence read or relevant write.
3. Edit and delete each supported entity and confirm the content hash and affected dimension change without cross-tenant rows.
4. Roll back application code while leaving `0060` applied. The unused table is compatible with the previous release.
5. Do not downgrade after snapshots exist if their launch history must be retained; forward-fix instead.

## Production verification gates

The source suite proves empty/partial/complete golden fixtures, all five theme IDs, determinism, monotonic additions, deletion invalidation, freshness boundaries, evidence/recommendation linkage, route allowlists, tenant query filters, append-only migration structure, canonical SEO reuse, and token-only Phase 6 UI styling.

Before release, run the migration and ORM suite against PostgreSQL, race identical first calculations across workers, verify authenticated and cross-tenant route negatives in Flask, render all light/dark/responsive states, and run axe, keyboard, screen-reader, zoom, and contrast checks. Phase 7 rendered-theme contract evidence must remain distinct from deterministic stored-field results.
