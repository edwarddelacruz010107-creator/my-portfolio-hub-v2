# Financial Data Reset

This release adds a guarded one-time Flask CLI command for clearing development/test billing data before production launch.

## Preview only

```bash
flask reset-financial-data --confirm RESET-FINANCIAL-DATA --dry-run
```

## Perform the reset

```bash
flask reset-financial-data --confirm RESET-FINANCIAL-DATA
```

On Render, run the command from a Shell session after deploying this release.

## Reset

- Dodo/PayMongo webhook event logs
- manual payment submissions
- billing and subscription notifications
- non-Administrator subscription records
- non-Administrator tenants and profiles to a fresh trial
- revenue, MRR, ARR, charts, and provider totals recalculate from zero

## Preserved

- tenants and users
- protected Administrator/default portfolio
- plans, prices, currency settings, discounts
- Dodo and PayMongo environment configuration
- payment methods, QR codes, and bank details
- themes, portfolio content, uploads, and non-billing notifications

The command is transactional. If any step fails, the database changes are rolled back.
