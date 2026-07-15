# Phase 6 - Portfolio Intelligence

**Status:** source implementation complete  
**Migration:** `0060_portfolio_intelligence.py`

## Delivered

- One pure, deterministic `portfolio-intelligence-2026.07-v1` rubric covering profile, projects, services, testimonials, certificates, experience, SEO, accessibility fields, contact readiness, and freshness.
- Explainable stored-fact evidence for every scored point, exact allowlisted editor links, and deterministic impact/effort recommendations.
- Explicit `not_evaluated` states for rendered headings, link labels, and contrast; no crawler, accessibility scan, or external SEO claims.
- Tenant-scoped fact collection and hash/version cache with concurrency-safe append-only snapshot history beginning only after launch.
- Post-write recalculation for every relevant entity and freshness-aware invalidation without artificial daily snapshots.
- Authenticated Portfolio Intelligence workspace with score definition/version/time, evaluated coverage, dimension cards, SEO/canonical/social/indexability preview, accessibility evidence boundaries, and prioritized actions.
- The existing SEO editor now consumes and refreshes the canonical SEO rubric dimension instead of maintaining a second four-field calculation.
- Portfolio completion notifications now use the canonical total and link to the intelligence workspace.

## Automated evidence

- 16 Phase 6 golden, theme, invariant, deletion, freshness, determinism, evidence, route, tenant-isolation, history, SEO reuse, and design-token tests pass.
- Design-token lint passes all six registered Phase 1-6 CSS surfaces.
- Python source compilation passes for the new model, migration, service, route, and tests.

## Deployment-gated evidence

Flask/Jinja/SQLAlchemy/PostgreSQL and a deployable browser are not installed in this workspace. Live migration/rollback, ORM immutability, concurrent snapshot insertion, real authenticated/cross-tenant route behavior, template rendering, accessibility, responsive layouts, and Phase 7 rendered-theme scans remain release gates. See `PORTFOLIO_INTELLIGENCE.md`.

## Rollback boundary

Roll application code back while preserving additive migration `0060`; earlier code ignores the snapshot table. Do not delete launch history through a downgrade after real calculations exist. Unknown or unavailable rendered evidence remains non-scoring until a later versioned rubric explicitly adds a validated source.
