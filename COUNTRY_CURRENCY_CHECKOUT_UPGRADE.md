# Country-Aware Billing Checkout Upgrade

## Scope

This release modernizes the tenant manual-payment experience and adds a
server-controlled country-to-currency conversion flow.

## Tenant experience

- Responsive, modern checkout layout with a clear amount-due summary.
- Country selector with Philippines preselected for unconfirmed accounts.
- Currency changes automatically when the tenant changes country.
- The converted amount refreshes through an authenticated same-origin endpoint.
- The USD base amount and exchange-rate source are shown transparently.
- Transaction reference and proof upload remain mandatory.
- The amount field remains locked and server-calculated.

## Billing integrity

The following values are recalculated on the server at final submission:

- plan and billing cycle
- USD base amount
- discount
- selected country
- derived currency
- exchange rate
- final local amount

The browser-submitted amount is ignored. A payment snapshot stores:

- `amount_paid`
- `amount_usd`
- `currency_code`
- `exchange_rate`
- `country_code`
- `expected_amount`

## Tenant profile data

The confirmed billing preference is stored on the core tenant record:

- `country_code`
- `preferred_currency`
- `country_source`
- `country_updated_at`

Unconfirmed existing tenants are not silently labelled as Philippine users.
The UI defaults to Philippines only until the user confirms a selection.

## Deployment

```bash
flask db upgrade
```

Required environment variables when using FreecurrencyAPI:

```env
CURRENCY_PROVIDER=freecurrencyapi
FREECURRENCYAPI_KEY=your-regenerated-secret
```

Do not commit real API keys to source control.

## Accounting snapshot integrity

Manual-payment approval now issues the invoice using the exact captured local
currency amount, exchange rate, and currency code from the payment submission.
Coupon redemption is calculated against the authoritative USD base amount,
while the subscription and invoice preserve the tenant's settlement currency.
This prevents later FX refreshes or a global display-currency change from
rewriting the historical payment amount.

## Supported country/currency presets

The checkout includes the Philippines first, followed by supported presets for
North America, Europe, East and Southeast Asia, and Oceania. Unsupported or
international locations may choose **Other / International (USD)**. The country
selection is a billing preference, not silent IP geolocation.
