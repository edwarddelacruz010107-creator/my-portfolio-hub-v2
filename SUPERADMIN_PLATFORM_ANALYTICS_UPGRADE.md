# Superadmin Platform Analytics Upgrade

## Scope
- Rebuilt `/superadmin/` overview analytics with a modern operational dashboard.
- Added six-month recorded-revenue trend and tenant-growth charts without external chart libraries.
- Added provider-separated revenue for Dodo Payments, PayMongo, and approved manual payments.
- Added MRR, active-rate, expiring-subscription, pending-payment, and churn-signal metrics.
- Added plan-distribution visualization and improved responsive behavior.

## Revenue correctness
Provider-localized charges are normalized to the currency configured in Plan Settings. A localized provider amount such as PHP 65.98 for a USD 1.00 Basic plan is not displayed as USD 65.98.

## Deployment
No database migration or additional JavaScript dependency is required.
