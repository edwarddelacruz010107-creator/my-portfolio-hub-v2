# Financial Metric Definitions

**Definition version:** `finance-v1.0.0`  
**Reporting currency:** USD  
**Reporting time zone:** UTC  
**Authoritative source:** posted, live-mode rows in `payment_transactions`

These definitions apply to Platform Overview, Subscription Monitor, Billing, and every later financial display. Cash revenue and recurring run rate are intentionally separate.

| Metric | Definition | Exclusions |
|---|---|---|
| Gross cash revenue | Sum of positive posted settlements and positive adjustments by provider `occurred_at`. | Test-mode, review-required, failed/authorized-only events, refunds, chargebacks, reversals. |
| Net cash revenue | Sum of all posted settlements, refunds, chargebacks, reversals, and adjustments by `occurred_at`. | Test-mode and review-required rows. |
| Provider revenue | Net cash revenue grouped by `dodo`, `paymongo`, or `manual`. Provider buckets must reconcile exactly to net cash revenue. | Same exclusions as net cash revenue. |
| MRR | Latest posted recurring settlement for each currently active, non-trial, non-administrator subscription. Yearly settlements are divided by 12 using `Decimal`. | Plan list prices, test transactions, trials, expired/cancelled subscriptions, review-required rows. |
| ARR | MRR multiplied by 12. | It is not cash collected and must not be added to revenue. |
| Active subscription | Status is `active`, plan is neither Trial nor Administrator, start is not future, and expiry is not past at the UTC observation time. | Pending, scheduled-future, expired, cancelled, trial, Administrator. |
| Trial | Explicit tenant/profile trial state before the stored UTC trial end. | Paid active subscriptions. |
| Churn rate | Subscriptions cancelled or expired in an interval divided by subscriptions active at interval start. | Displayed as unavailable until an interval-start snapshot/history denominator exists. |
| Manual approval | One approved submission may create at most one settlement posting, keyed by its immutable submission ID. | Pending/rejected submissions. |
| Refund | A negative posting linked to its original settlement. Partial refunds use their own provider refund ID. | Mutation of the original settlement is prohibited. |

## Currency policy

- Original amounts are stored as integer minor units with ISO currency and exponent.
- USD amounts use fixed-scale numeric values; binary floats are rejected inside ledger math.
- USD originals derive USD directly from minor units.
- Non-USD rows are posted only with a stored USD amount, positive FX rate, named rate source, and effective timestamp.
- Incomplete FX or missing original-settlement links produce `review_required` rows. They remain traceable but are excluded from USD totals.
- Current rates and current plan prices are never used to manufacture historical revenue.

## Ordering, identity, and corrections

- Financial intervals use provider `occurred_at`, not delivery or processing order.
- Receipt and provider timestamps are stored separately in UTC.
- Provider event and transaction IDs have provider/environment/accounting-type uniqueness constraints. The database is the final replay and concurrency boundary.
- Subscription state accepts only events at or after the last recorded provider state timestamp. Late financial events are still posted to their original interval.
- Settled and audit rows are append-only at both ORM and PostgreSQL trigger layers.
- Corrections are new reversal, refund, chargeback, or adjustment rows with actor/reason evidence.

## Freshness and coverage

Each facade response includes the definition version, generated time, latest recorded time, staleness label, and counts of posted versus review-required live rows. The cache is bounded to 120 seconds and moves to a new generation immediately after a successful append.

