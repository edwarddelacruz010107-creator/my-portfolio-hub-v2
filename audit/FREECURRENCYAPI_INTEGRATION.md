# FreecurrencyAPI Billing Integration

## Implemented

- Added `freecurrencyapi` as a selectable FX provider in Superadmin → Subscription Settings.
- Uses `https://api.freecurrencyapi.com/v1/latest` with USD as the authoritative base currency.
- Sends the API key through the `apikey` HTTP header; it is not placed in the request URL, stored in the database, or exposed to templates.
- Supports `CURRENCY_PROVIDER=freecurrencyapi` and `FREECURRENCYAPI_KEY` environment variables.
- Automatically selects FreecurrencyAPI for new installations when a key is present, unless another provider is explicitly selected.
- Preserves Frankfurter as the no-key fallback and the last valid cached rate as the final fallback.
- Handles HTTP 429 quota/rate-limit responses without exposing credentials.
- Added `scripts/test_freecurrencyapi_rates.py` for deployment diagnostics.
- Updated production environment templates and documentation.

## Deployment

Set these values in Render, not in source control:

```env
CURRENCY_PROVIDER=freecurrencyapi
FREECURRENCYAPI_KEY=your-regenerated-key
```

Then open Superadmin → Subscription Settings, choose **FreecurrencyAPI**, select the display currency, and click **Save Currency** or **Refresh Rate**.

Test from a Render Shell with:

```bash
python scripts/test_freecurrencyapi_rates.py
```

## Security

Any API key pasted into chat, screenshots, logs, or source code must be revoked and regenerated before production use.
