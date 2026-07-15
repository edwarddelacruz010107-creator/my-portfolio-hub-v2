# Phase 5 - Billing Center

**Status:** source implementation complete  
**Migration:** `0059_billing_center.py`

## Delivered

- Versioned plan catalog snapshots with cycles, entitlements, provider mappings, and effective-date fields; new invoices preserve the sold plan version.
- Exact minor-unit dual writes and a bounded, resumable, backup-first legacy float conversion that refuses to infer currency.
- Explicit, provider-adapted subscription lifecycle transitions with idempotent append-only status evidence.
- Invoice line items, tax/discount metadata, original money evidence, status history, atomic PostgreSQL numbering, and ORM/database immutability enforcement.
- Concurrency-safe coupon redemption and manual proof review, mandatory review reasons, deterministic action keys, and immutable audit evidence.
- Idempotent retry/dunning records with bounded backoff and safe failure state.
- Ledger-backed superadmin Billing Center for revenue/provider definitions, subscriptions, transactions, invoices, manual submissions, coupons, retries, refunds, and reconciliation.
- Tenant Billing Center for plan/entitlements, status/renewal, provider, exact transactions, invoices, authorized receipts, safe failures, and payment attempts.
- Provider detail with masked external references and preserved original currency; Dodo, PayMongo, and manual remain separate.
- Authorized ReportLab PDF receipts rendered only from immutable server records and visually inspected through Poppler.
- `BILLING_CENTER_ENABLED` cutover plus a one-release `BILLING_LEGACY_READ_ONLY` rollback seam.

## Automated evidence

- 14 Phase 5 exact-money, migration, catalog, lifecycle, concurrency, dunning, provider, authorization, receipt, rollback, and design-system tests pass.
- 68 Phase 0C-5 and deterministic-migration source/domain tests pass.
- 7 shared-component/notification DOM security and behavior tests pass.
- Design-token lint passes all five registered CSS surfaces.
- Python application/migration/test compilation and all JavaScript syntax checks pass.
- A4 receipt validation: one page, PDF 1.4, no JavaScript/forms, Poppler render succeeds, and visual review shows no clipping or overlap.

## Deployment-gated evidence

Flask/Jinja/SQLAlchemy/PostgreSQL/Redis and provider sandboxes are not installed in this workspace. Migration/backfill rehearsal, actual row-lock races, database triggers, signed provider contract fixtures, application authorization, exact ledger-to-invoice reconciliation, query plans/load, accessibility, browser layouts, and multi-worker retry behavior remain required before release. The full checklist is in `BILLING_CENTER.md`.

## Rollback boundary

Disable `BILLING_CENTER_ENABLED` and keep `0059` applied. The previous release can continue reading legacy columns while new exact structures remain intact. Do not downgrade after Phase 5 invoices, status events, float backups, or retry records exist; forward-fix, keep the legacy view read-only for one stable window, and contract only after zero measured legacy use.
