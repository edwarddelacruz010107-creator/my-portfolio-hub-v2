# Subscription Analytics Upgrade

- Rebuilt Superadmin Subscription Overview with responsive analytics cards.
- Fixed empty Active/Pending/Churn values by bridging the current metric keys.
- Bound revenue formatting to the currency symbol and code configured in Plan Settings.
- Added separated recorded revenue for Dodo Payments, PayMongo, and approved manual payments.
- Added provider revenue shares and active-subscription counts.
- Added trial, expired, cancelled, and pending-review insights.
- Added webhook health for the last 30 days and both Dodo/PayMongo endpoint guidance.
- Added provider-aware tenant subscription table and recent webhook monitor.

Revenue is labelled as recorded revenue because the existing schema stores payment snapshots rather than a complete immutable transaction ledger for every automated renewal.
