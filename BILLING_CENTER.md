# Billing Center

## Ownership and definitions

- `app/services/billing/plan_service.py` owns plan catalog resolution, billing cycles, entitlement snapshots, provider product mappings, effective dates, and deterministic catalog versions.
- `app/services/billing/lifecycle_service.py` owns subscription state transitions and provider-state adapters.
- `app/services/billing/invoice_service.py` is the only invoice issuer/voider. `receipt_service.py` renders authorized PDFs exclusively from persisted invoice data.
- `app/services/billing/discount_service.py` owns quote/redemption math; redemption locks the campaign row and relies on database uniqueness for replay races.
- `app/services/billing/dunning_service.py` owns retry evidence and policy. Provider adapters may execute a charge only for a claimed `BillingAttempt.idempotency_key`.
- `app/services/billing/center_service.py` owns tenant and superadmin billing read models. Revenue values come from the Phase 3 ledger definitions.

Cash revenue remains the sum of posted ledger entries. Active plan value, invoice totals, pending submissions, retry attempts, and missing invoices are not counted as paid revenue.

## Exact money and provider provenance

New subscription and manual-submission writes retain the legacy float for one rollback release and also store `amount_paid_minor`, ISO currency, and exponent. New invoices optionally store the original charged minor units/currency/exponent. Payment transactions remain the authoritative provider-level source.

The reviewed legacy conversion is `Decimal(str(value))`, rounded half-up once to the explicit currency exponent. `financial_float_backups` stores the original text and conversion disposition before exact columns are populated. A row without explicit currency provenance becomes `review_required`; the backfill never guesses currency.

Dodo, PayMongo, and manual records stay separate through provider columns, original identifiers, environments, and currencies. UI summaries may add posted reporting amounts, but never merge provider identities. External references are stored in full and displayed partially masked.

## Plan catalog and lifecycle

Every new invoice stores a JSON sold-plan snapshot plus its deterministic catalog version. The snapshot contains the plan code, display name, billing cycle, price/currency, entitlement facts, provider mappings, and configured effective dates. Historical invoices without a Phase 5 snapshot display `legacy-unversioned`; their stored invoice facts remain valid and are not retroactively embellished.

Lifecycle transitions are explicit and append a unique `SubscriptionStatusEvent`. Verified Dodo, PayMongo, and manual decisions pass through provider adapters/service calls. Replayed provider events return the existing event. Invalid transitions fail visibly and must be reconciled rather than silently forcing state.

Proration policy for this release is **change at renewal; no automatic mid-cycle proration**. Existing additive renewals remain compatible. A future provider-specific proration feature requires its own catalog policy version, quote evidence, ledger event fixtures, and invoice lines before activation.

## Invoices, receipts, refunds, and retries

Invoice numbers use the existing atomic PostgreSQL sequence. Issuance creates a fixed-scale invoice, line item, sold-plan snapshot, and append-only status event in one savepoint. PostgreSQL and ORM guards reject updates to invoice numbers, financial fields, provider evidence, and plan snapshots. Voiding requires an actor, reason, and idempotency key; corrections use a new invoice and linked ledger correction.

Receipts are ReportLab A4 PDFs generated after tenant/superadmin authorization. Amounts, taxes, discounts, and totals are never recalculated in the browser. Responses are private/no-store and provider references are masked.

Refunds, reversals, and chargebacks remain linked negative ledger facts; no original settlement is mutated. Retry/dunning rows have one database-unique idempotency key, bounded attempts, recorded safe failure state, and scheduled backoff. The provider adapter must pass the same key to the provider.

## Authorization and operational actions

- Tenant invoice download filters by both invoice ID and authenticated tenant ID.
- Tenant history queries only the active tenant ID.
- Every superadmin billing route uses `@superadmin_required`.
- Manual proof retrieval remains the Phase 0 private, audited, no-store viewer.
- Manual approve/reject requires a reason. The service locks the submission, rejects non-pending decisions, records immutable financial/lifecycle audit evidence, and relies on ledger uniqueness for a competing approval.
- Provider detail pages expose original currency and masked provider/event references only.

## Migration and rollback

1. Back up both databases and confirm the current heads.
2. Apply `0059_billing_center.py` as an expand migration while the previous application is still compatible.
3. Deploy with `BILLING_CENTER_ENABLED=false`, `BILLING_LEGACY_READ_ONLY=true`; verify old reads.
4. Run `backfill_financial_floats(batch_size=...)` repeatedly, recording converted/review-required counts and reconciling backup rows to source IDs.
5. Enable dual-write traffic and compare ledger, invoice, subscription, and manual-submission exact values. Resolve every mismatch without inferring facts.
6. Enable `BILLING_CENTER_ENABLED=true`. Keep `/billing/legacy` read-only for one stable rollback window.
7. Roll back application code by disabling the feature flag while preserving migration `0059`. Do not destructively downgrade after new invoices/lifecycle events/attempts exist.
8. Remove legacy floats and views only in a later contract migration after measured zero legacy reads/writes.

## Production verification checklist

1. Rehearse `0059` and application rollback on empty, oldest-supported, current-clone, and interrupted PostgreSQL copies.
2. Race invoice issuance, coupon last-use, manual approval, lifecycle replay, and retry creation; verify one durable result per key.
3. Reconcile each provider sandbox capture to its invoice and ledger row, including refunds/cancellations/renewals and original currency.
4. Validate current/legacy plan, coupon, no-tax/tax, monthly/yearly, cancellation, renewal, retry exhaustion, and no-proration fixtures.
5. Render representative PDFs with long tenant/plan/reference content; inspect every page and verify tenant/superadmin authorization negatives.
6. Run query plans and p95 budgets for billing searches, tenant history, missing-invoice reconciliation, and provider breakdowns at production scale.
7. Run axe, keyboard, screen-reader, zoom, reduced-motion, light/dark, phone/tablet/desktop, and print/download browser checks.
8. Monitor invoice issuance failures, missing ledger links, review-required transactions, failed/dead attempts, manual review conflicts, and backfill dispositions during canary.

## Deployment-gated limitations

The implementation workspace has no Flask/SQLAlchemy/PostgreSQL/Redis runtime. Live migration, ORM event, row-lock/race, provider sandbox, route-rendering, authorization integration, query-plan/load, and browser/accessibility checks are mandatory deployment gates. The source suite verifies deterministic domain and integration contracts but does not replace those environment-backed checks.
