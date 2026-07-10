# Subscription, Currency, and Manual Payment Upgrade

## Delivered

- Superadmin tenant subscription manager for Trial, Basic, Pro, and Enterprise.
- Immediate activation, scheduled activation, pending, expired, and cancelled states.
- UTC start and expiration timestamps with automatic duration fallback.
- Lazy scheduled activation on tenant requests plus renewal-scheduler activation.
- USD as the authoritative plan-price currency.
- Selectable converted display/payment currency.
- Frankfurter no-key rate provider and optional CurrencyAPI provider.
- Database and in-process FX caching with stale-rate fallback.
- Server-computed, non-editable manual-payment amount.
- Required transaction reference and payment proof.
- Cloudinary persistence for proof images and PDFs when Cloudinary is selected.
- PayMongo checkout limited to PHP display currency to avoid charging a non-PHP amount.

## Deployment

No new billing table is required. Currency and plan pricing settings are stored in
`platform_settings`. Existing Subscription fields are used for scheduling.

Optional environment variable:

```env
CURRENCYAPI_KEY=
```

When left blank, use the Frankfurter provider from Superadmin Subscription Settings.
