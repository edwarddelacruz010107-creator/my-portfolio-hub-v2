# Dodo Payments Superadmin Visibility Fix

## Root cause
The Dodo environment variables were declared at module scope in `config.py` after the Flask configuration classes. Flask only imports uppercase attributes from the selected configuration class, so `current_app.config` never received the Dodo settings even when they existed in Render.

## Fixes
- Moved all Dodo settings into `BaseConfig`.
- Added a Dodo Payments status card to Superadmin > Billing > Payment Methods.
- Shows mode, API key state, webhook secret state, and configured product count.
- Tenant billing now labels the automated checkout button as Dodo Payments when Dodo is active.
- Existing PayMongo and manual payment methods remain available.

## Required Render variables
Set `DODO_PAYMENTS_ENABLED=true` in addition to the API key, webhook secret, and product IDs.
