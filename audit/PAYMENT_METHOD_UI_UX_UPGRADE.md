# Superadmin Payment Method UI/UX Upgrade

## Updated page

- `/superadmin/billing/payment-methods/new`
- `/superadmin/billing/payment-methods/<id>/edit`

## Improvements

- Replaced the long label/input list with four structured sections.
- Added responsive desktop, tablet, and mobile layouts.
- Added a sticky tenant-facing live preview card.
- Added method-aware fields for e-wallet, bank transfer, PayMongo, and crypto.
- Added drag-and-drop QR upload with local preview.
- Added modern active/default switches.
- Added clearer tenant-visible and internal-only labels.
- Added field-level validation messages and helper text.
- Added a sticky save/cancel action bar.
- Preserved existing form fields, routes, CSRF protection, and backend behavior.

## Files

- `app/templates/superadmin/billing_payment_method_form.html`
- `app/static/css/payment-method-form.css`
- `app/static/js/payment-method-form.js`

## Validation

- All Jinja templates parsed successfully.
- Python source compiled successfully.
- JavaScript syntax validation passed.
- No Python cache artifacts are included.
