# Founder Dashboard Architecture

## Purpose and boundary

Phase 9 replaces the legacy Superadmin overview with a restricted, read-only command center. The route does not query domain tables or calculate metrics. It validates allowlisted filters and calls `build_founder_dashboard()`, which composes five read models:

- tenant lifecycle and subscription state;
- exact financial ledger reporting;
- portfolio publication, inventory, engagement evidence, and intelligence snapshots;
- Phase 8 AI usage evidence;
- readiness, delivery, incident, and audit evidence.

Operational changes stay in their owning Billing, Subscription, AI, Notification, or Audit center. The dashboard contains links, not duplicate mutation endpoints.

## Authorization and exports

The read route requires the explicit `platform.founder_dashboard.read` capability. The current capability implementation grants it only to an authenticated Superadmin; it is separate so future role expansion cannot occur by route accident.

CSV export requires `platform.founder_dashboard.export`, a POST with CSRF, and strong reauthentication using the current password plus a fresh, non-replayed TOTP. The approval is tied to the same user and session for ten minutes and is cleared with the session at logout. Export contains only bounded aggregate metrics—no tenant names, emails, content, IP addresses, provider references, prompts, or logs. Every export appends an `ActivityLog` event and returns `private, no-store` with a sandboxed response CSP.

## Filters and time semantics

The supported ranges are 7, 30, 90, and 365 days. Comparison is either the immediately preceding equal-length interval or none. Payment-provider, AI-provider, and effective-plan filters are independent allowlists. Unknown values fall back to the supported default rather than becoming query input.

All interval boundaries are aware UTC timestamps with a half-open `[start, end)` contract. Payment and lifecycle metrics use provider/event occurrence time. Their later database recording timestamp contributes to the source watermark, so a late-arriving event invalidates cached composition while remaining assigned to its original occurrence interval.

Plan filters use the tenant's current effective lifecycle segment. They are not a historical plan-at-event dimension because that versioned dimension does not exist for every domain.

## Lifecycle definitions

Definition: `tenant-lifecycle-2026.07-v1`.

- New tenants: tenant rows created in the selected UTC interval and current plan segment.
- Activation events: distinct tenants with a versioned subscription transition to `active` in the interval.
- Conversion: tenants created in the interval with an evidenced activation by interval end, divided by tenants created in the interval.
- Active subscriptions: subscriptions active at interval end, respecting the current payment-provider assignment when filtered.
- Trials: current tenant lifecycle state `trial`; unavailable under a payment-provider filter because a trial has no payment provider.
- Churn: versioned terminal transitions during the interval among subscriptions whose latest status before interval start was `active`.

Conversion and churn are unavailable below the privacy threshold of five denominator records. They are also unavailable when current active subscriptions lack versioned activation evidence or a selected provider cannot be assigned across legacy active subscriptions. The dashboard exposes numerator, denominator, and coverage internally; it never treats missing history as zero churn.

## Finance definitions

Financial values remain owned by `app/services/ledger/analytics_service.py` and inherit `finance-v1.0.0`:

- gross cash is positive posted settlements and adjustments;
- net cash is all posted live USD reporting amounts, including linked negative corrections;
- refunds include refund, reversal, and chargeback accounting types as a positive displayed magnitude;
- MRR uses the latest posted settlement for each currently active non-trial, non-administrator subscription, dividing annual cycles by twelve;
- ARR is MRR multiplied by twelve.

Amounts use `Decimal` and original ledger FX evidence. The dashboard does not read legacy subscription floats or profile rates. Review-required rows remain separate source coverage and do not silently enter totals.

## Portfolio evidence

Definition: `portfolio-operations-2026.07-v1`.

The read model reports current published portfolios, published project inventory, visible service inventory, non-spam visitor inquiries received in the interval, delivered inquiries, and the latest Phase 6 completion score per tenant. Completion averages are suppressed below five evaluated tenants.

Project views, likes, and reactions are explicitly labeled cumulative because no versioned interval event stream exists. Service engagement is unavailable because no service-engagement source exists. Publication counts are current state, not claimed historical publication events.

## AI evidence

The Phase 8 append-only usage ledger provides request count, terminal failures, average latency, provider split, known cost microunits, and count of requests whose cost is unavailable. If no row has complete provider usage, known cost is `NULL`/Unavailable rather than zero.

## Operations, incidents, and audits

Definition: `platform-operations-2026.07-v1`.

Database and cache status come from the migration-aware readiness checks. Heartbeat and self-ping appear only when their actual state exists. CPU, memory, and disk stay unavailable until a monitoring source is configured. Email outbox and unprocessed webhook signals come from durable rows.

Incident lists are bounded to twelve items and contain only source, safe class/type, status, time, and a link to the owning center. Audit summaries are bounded to fifteen safe action headers across platform, finance, and AI logs. Descriptions, payloads, prompts, secrets, provider IDs, and tenant content are not included.

## Cache and query contract

The business composition cache has a 60-second TTL. Its key includes:

- dashboard definition version;
- all filters;
- the UTC end-minute bucket;
- a hash of the maximum recorded/updated timestamp in each contributing domain and both database binds.

This watermark provides mutation-driven invalidation without adding route writes. Readiness and operational state are rebuilt on each request rather than cached with business metrics. The dashboard records its assembly duration against a 750 ms source budget, bounds every incident/audit list, and uses migrations `0063` and tenant `0002` for the interval/segment indexes exercised by these reads.

Staging must run `EXPLAIN (ANALYZE, BUFFERS)` at representative tenant/event sizes and revise indexes based on evidence. Source assertions do not prove a production query plan.

## Deployment verification

- Apply core migration `0063` and tenant migration `0002_founder_dashboard_indexes` after backup.
- Reconcile every card against fixed fixtures and sampled production-clone rows.
- Test UTC month/day boundaries, late-arriving events, empty periods, zero comparison baselines, fewer-than-five cohorts, missing legacy events, and each filter combination.
- Confirm non-Superadmin, tenant-admin, anonymous, and impersonated sessions cannot access the dashboard or export.
- Confirm export requires password and a newly accepted TOTP, expires after ten minutes, rejects GET, emits CSRF protection and audit, and contains no PII.
- Verify Redis cache hit/miss, watermark invalidation after each domain write, stale/unavailable labels, and multi-worker consistency.
- Measure query count and p50/p95 latency with representative database sizes; retain query plans for the added indexes.
- Run keyboard, focus, screen-reader, 200% zoom, mobile, hostile-string, CSP, and no-inline browser checks.

## Rollback

The dashboard change is read-only and the two migrations add indexes only. Deploy the previous application to restore the legacy overview while leaving indexes in place. If necessary, drop tenant `0002` and core `0063` only after confirming no active query plan depends on them. No business facts or dashboard snapshots require data rollback.
