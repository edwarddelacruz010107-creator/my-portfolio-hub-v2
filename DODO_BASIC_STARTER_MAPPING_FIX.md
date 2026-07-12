# Dodo Basic/Starter Product Mapping Fix

The application stores the public **Basic** plan internally as `starter`, while
Render uses `DODO_BASIC_MONTHLY_PRODUCT_ID` and
`DODO_BASIC_YEARLY_PRODUCT_ID`.

The checkout resolver now maps:

- `starter` / `basic` -> `DODO_BASIC_*_PRODUCT_ID`
- `pro` -> `DODO_PRO_*_PRODUCT_ID`
- `business` / `enterprise` -> `DODO_ENTERPRISE_*_PRODUCT_ID`
- `monthly` / `month` -> `MONTHLY`
- `yearly` / `annual` / `annually` -> `YEARLY`

This prevents checkout from incorrectly looking for
`DODO_STARTER_MONTHLY_PRODUCT_ID`.
