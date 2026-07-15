# Phase 9 — Founder Dashboard

Status: complete at the deterministic verification boundary on 2026-07-15.

## Delivered

- Replaced the route-local legacy overview calculations with one versioned founder dashboard assembler.
- Added separate lifecycle, ledger, portfolio, AI, and operations read models with definition versions and source freshness.
- Added 7/30/90/365-day UTC ranges, equal previous-interval comparison, current-plan segmentation, and separate payment/AI provider filters.
- Reconstructed churn from versioned interval-start subscription status and terminal transitions rather than guessing from current rows.
- Suppressed conversion/churn/completion below a five-record privacy threshold and when lifecycle/provider coverage is incomplete.
- Preserved exact ledger ownership for gross/net cash, refunds, MRR, ARR, and provider split.
- Added honest publication, contact, cumulative project engagement, unavailable service-engagement, and Phase 6 completion evidence.
- Added Phase 8 request/cost/latency/failure reporting with explicit unavailable-cost counts.
- Added migration-aware database/cache readiness, real heartbeat/self-ping evidence, durable email/webhook signals, and unavailable CPU/RAM/disk states where monitoring is absent.
- Added bounded incident and audit summaries linking to owning operational centers.
- Added an explicit founder read/export capability and a CSRF-protected, aggregate-only CSV export requiring current password plus fresh TOTP and appending an audit event.
- Removed the dashboard's inline CSS, inline JavaScript, inline styles, and client-built export behavior.
- Added source-watermark cache invalidation, a 60-second TTL, assembly timing against a 750 ms budget, and index-only core/tenant migrations.

## Automated evidence

- 15 Phase 9 contracts: passed.
- 115 unittest contracts across Phases 0C–5 and 7–9: passed.
- 16 Phase 6 deterministic function contracts: passed.
- Seven JavaScript DOM/security contracts: passed.
- Nine registered CSS files passed design-token lint.
- Full Python compilation and AST scan: passed.
- Core migration graph: one connected head at `0063`.
- Tenant migration graph: one connected head at `0002_founder_dashboard_indexes`.

## Limitations and deployment gates

- Flask, Jinja, SQLAlchemy, Alembic, PostgreSQL, Redis, and a browser runtime are unavailable in this workspace, so runtime route/render/query evidence remains a deployment gate.
- Current-plan filters are not historical plan-at-event dimensions.
- Project engagement is cumulative; service engagement is unavailable.
- CPU, memory, and disk are unavailable until an actual monitoring source is configured.
- Source-defined indexes are hypotheses until production-like PostgreSQL plans prove them.
- Legacy subscription lifecycle/provider gaps intentionally suppress affected rates.

## Manual checklist

- [ ] Apply core `0063` and tenant `0002` on a PostgreSQL staging clone; capture upgrade, downgrade, lock, and query-plan evidence.
- [ ] Reconcile each dashboard card against fixed fixtures and a sampled production clone.
- [ ] Test every time range, comparison mode, provider, and plan combination at UTC boundaries and with late-arriving events.
- [ ] Verify privacy suppression at denominator 0–4 and display at 5+.
- [ ] Verify no route-local calculations remain and legacy URLs still resolve.
- [ ] Test cache invalidation from tenant, payment, lifecycle, portfolio, inquiry, and AI writes across multiple workers.
- [ ] Verify unauthorized roles and impersonated sessions cannot read or export.
- [ ] Verify export rejects GET and stale reauth, requires CSRF/password/fresh TOTP, contains only aggregate rows, and appends an audit event.
- [ ] Confirm readiness probes are bounded and CPU/RAM/disk remain unavailable without configured telemetry.
- [ ] Run responsive, keyboard, screen-reader, zoom, reduced-motion, hostile-content, and CSP browser tests.

## Rollback

Deploy the prior application; no dashboard business data was written. The added indexes may remain. If an index rollback is required, downgrade tenant `0002` then core `0063` after confirming no deployed query depends on them.
