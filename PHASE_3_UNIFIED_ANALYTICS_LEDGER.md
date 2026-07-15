# Phase 3 — Unified Analytics and Financial Ledger

**Status:** source implementation complete  
**Definition version:** `finance-v1.0.0`  
**Migration:** `0057_add_append_only_payment_ledger.py`

## Delivered

- Append-only payment transactions and financial audit events with ORM mutation guards and PostgreSQL update/delete rejection triggers.
- Scoped unique provider event and transaction boundaries for Dodo, PayMongo, and manual sources.
- Exact minor-unit/original-currency storage plus fixed-scale USD and reproducible FX evidence.
- Linked refunds, chargebacks, reversals, and review-required handling when an original settlement or FX snapshot is missing.
- PayMongo and Dodo adapters connected to verified webhook paths; manual posting connected to the approval transaction.
- Retry-safe webhook receipts, sanitized summaries, provider timestamp state ordering, and explicit test/live environments.
- Provenance-first, idempotent dry-run/apply backfill with unreconciled dispositions.
- One SQL-aggregated financial facade used by Platform Overview and Subscription Monitor. Legacy plan-price and payment-float revenue calculations are no longer display sources.
- Version/freshness/source-coverage metadata, live-only reporting, bounded generation-invalidated caching, and honest unavailable churn output.
- Reset protection that refuses to delete source financial history once immutable ledger rows exist.

## Verification evidence

- 14 Phase 3 exact-money, replay, FX, refund, ordering, adapter, immutability, producer, and facade contract tests pass.
- 32 Phase 0C/1/2/migration source regressions pass.
- 4 shared component DOM behavior/security tests pass.
- Python compilation succeeds for the changed application, migration, and test modules.

The Phase 3 suite proves `$1` from each source is exactly `$3`, 100 identical deliveries remain `$1`, yearly normalization uses exact decimals, out-of-order delivery stays in the provider occurrence interval, and unsafe metadata is excluded.

## Deployment gates not run here

No Flask/SQLAlchemy/PostgreSQL runtime is installed in this workspace. The migration, transactional ORM paths, real provider signatures, PostgreSQL uniqueness race, query plans, latency budgets, Redis serialization/invalidation, and browser rendering require the deployment-owner rehearsals in `LEDGER_BACKFILL_RUNBOOK.md` before production rollout.

## Rollback boundary

The Phase 2 package remains the pre-ledger application rollback. After ledger rows are written, do not downgrade destructively; roll application code back while preserving `0057`, then forward-fix. Corrections to financial facts are append-only.

