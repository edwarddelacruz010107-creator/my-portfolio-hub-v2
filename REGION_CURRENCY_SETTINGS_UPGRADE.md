# Tenant Region & Currency Settings Upgrade

## Added
- Tenant Admin → Settings → Region & Currency card.
- Country selector using the existing supported-country allow-list.
- Preferred billing currency selector using the existing FX-supported currencies.
- Automatic country-to-currency suggestion toggle.
- Current USD conversion-rate preview with safe USD fallback.
- Dedicated CSRF-protected and rate-limited save route.
- Tenant-scoped ownership resolution, validation, activity logging, rollback, and timestamps.

## Data model
This upgrade uses the existing Tenant fields introduced by migration 0053:
- `country_code`
- `preferred_currency`
- `country_source`
- `country_updated_at`

No new migration is required.

## Billing safety
- Plan prices remain authoritative in USD.
- Tenant country/currency controls presentation and checkout suggestion only.
- Browser-submitted exchange rates or prices are never trusted.
- Existing invoices and historical payments are not modified.
