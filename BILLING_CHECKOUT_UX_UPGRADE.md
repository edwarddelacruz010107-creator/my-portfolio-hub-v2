# Billing Checkout UX Upgrade

## Implemented
- Dodo checkout returns to a hidden Studio endpoint instead of the public tenant billing page.
- The hidden endpoint redirects tenants to the Studio dashboard with success, cancelled, failed, or processing state.
- Dashboard shows a responsive payment status card.
- Successful returns poll the local subscription state every five seconds.
- Paid access is still activated only by a verified Dodo webhook.
- Active subscription confirmation updates the banner without forcing another page navigation.
- Cancelled and failed states keep the tenant's current plan unchanged and link back to Billing.
- Internal `starter` plan values are displayed as `Basic` in the status API.

## Routes
- `GET /studio/billing/dodo/return`
- `GET /studio/billing/subscription-status`

## Security
The browser redirect is never accepted as proof of payment. The dashboard checks only the application's local subscription record, which must be updated by the signed webhook handler.
