# Ledger Backfill and Reconciliation Runbook

This is an expand/migrate/compare operation. It must not be used to infer payments from catalog prices or current subscription state.

## Preconditions

1. Take and verify a PostgreSQL backup. Record the backup identifier and restore owner outside the application log.
2. Deploy migration `0057` while old financial columns remain intact.
3. Set `PAYMONGO_MODE` and `DODO_PAYMENTS_MODE` explicitly. Test-mode transactions never enter live revenue reports.
4. Pause manual approvals and provider webhook workers for the shortest practical snapshot window, or take a repeatable-read snapshot.
5. Record source row counts and per-provider legacy totals for comparison only. Legacy totals are not forced to match the new definitions.

## Dry run

Run:

```bash
flask ledger-backfill
```

The command inspects approved manual submissions and legacy provider payment snapshots. It reports `inspected`, `eligible`, `unreconciled`, and already-processed counts without writing. A row is eligible only when tenant, immutable payment ID, amount, original currency, and occurrence/review time are present. Non-USD history additionally requires its stored USD amount and FX snapshot.

Review all unreconciled reasons. Do not fill gaps from a plan catalog, a current exchange rate, or a subscription's current status.

## Apply

After approving the dry-run report:

```bash
flask ledger-backfill --apply
```

Each source receives one durable `ledger_backfill_items` disposition and fingerprint. Re-running the command is idempotent. Eligible rows append a transaction; ambiguous rows append an `unreconciled` disposition only.

## Reconciliation queries

Run these checks against the production clone first, then production:

- No duplicate provider event/accounting keys.
- No duplicate provider transaction/accounting keys.
- Provider net totals sum exactly to global net cash revenue.
- Posted non-USD rows all contain USD amount, FX rate, source, and effective time.
- Refund/chargeback rows are negative and linked to an original settlement; otherwise they remain review-required.
- Test-mode rows do not appear in live totals.
- Backfill source count equals posted plus unreconciled plus already-processed dispositions.
- Sample at least one Dodo, PayMongo, manual, yearly, non-USD, refund, and late-delivery trace from receipt to ledger to dashboard.

## Cutover

The compatibility facade already reads the ledger. During deployment, compare legacy exports to ledger results and explain differences using `finance-v1.0.0`: active plan value is not cash revenue, duplicate webhook/subscription rows collapse, refunds reduce net revenue, test transactions are excluded, and ambiguous FX is unavailable.

Resume producers only after the checks pass. Watch review-required count, cache freshness, webhook retry rate, and unique-constraint errors.

## Rollback

Roll back application code before contracting any legacy column. Do not delete or update ledger/audit rows. If a posting is wrong, append a linked correction with an actor and reason. Migration downgrade is allowed only before any production ledger row exists and only as part of a full release rollback approved by the database owner.

## Mandatory live gates

- Empty, oldest-supported, current-clone, and interrupted-clone migration rehearsals.
- PostgreSQL concurrent replay test with at least two independent sessions.
- Provider-signed Dodo and PayMongo test/live webhook captures with secret fields redacted from test artifacts.
- Query-count and latency measurement on production-scale generated fixtures.
- Restore rehearsal proving the pre-migration backup is usable.

