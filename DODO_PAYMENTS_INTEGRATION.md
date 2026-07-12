# Dodo Payments Integration

Integrated hosted subscription checkout and verified webhooks.

## Required dashboard setup

1. Create six subscription products: Basic/Pro/Enterprise monthly and yearly.
2. Set the matching `DODO_*_PRODUCT_ID` environment variables.
3. Add webhook URL: `https://myportfoliohub.online/webhooks/dodo`.
4. Subscribe to `subscription.active`, `subscription.updated`, `subscription.renewed`, `subscription.on_hold`, `subscription.cancelled`, `subscription.failed`, `subscription.expired`, `payment.succeeded`, and `payment.failed`.
5. Copy the webhook secret into `DODO_PAYMENTS_WEBHOOK_SECRET`.
6. Run `flask db upgrade` after deployment.
7. Test in test mode before setting `DODO_PAYMENTS_MODE=live`.

The success redirect does not activate plans. Only a verified webhook changes subscription access.
