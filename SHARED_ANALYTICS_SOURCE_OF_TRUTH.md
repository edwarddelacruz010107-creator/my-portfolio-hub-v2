# Shared Superadmin Analytics Source of Truth

Platform Overview and Subscription Monitor now consume `build_superadmin_analytics()` from:

`app/services/analytics/dashboard_analytics_service.py`

The shared service centralizes:

- deduplicated active subscriptions
- normalized MRR and ARR
- recorded revenue by Dodo, PayMongo, and manual payments
- original provider currency snapshots
- pending, expiring, expired, cancelled, and trial counts
- churn and active-tenant rates
- six-month normalized revenue trend
- tenant growth trend
- webhook health

This prevents Overview and Subscription Monitor from showing different values for the same metric.
