# Superadmin Email & Forms Production Fix

## Scope

This patch repairs the Superadmin **Email & Forms Settings** page for production and improves its layout across desktop, tablet, and mobile devices.

## Root causes fixed

1. The page depended on inline `onclick`, `onchange`, `onsubmit`, and inline JavaScript. Production Content Security Policy can block those handlers, making Save, Validate, Test Connection, provider toggles, diagnostics, and priority actions appear unresponsive.
2. The two-column page used a rigid sidebar width and several non-shrinking flex rows, causing horizontal overflow.
3. Resend validation referenced `requests` without importing it.
4. Gmail App Passwords pasted with spaces could fail authentication even when the value was valid.

## Changes

- Moved all page behavior to `app/static/js/superadmin-email-settings.js`.
- Removed all inline event handlers from the email settings template.
- Added robust JSON/session/error handling for production requests.
- Added responsive CSS in `app/static/css/superadmin-email-settings.css`.
- Added a responsive security/deliverability sidebar.
- Added mobile stacking for API-key, test-email, and action controls.
- Added live provider status refresh and status-pill creation.
- Added a **Use Gmail defaults** action.
- Auto-copies the Gmail username into Sender Email when appropriate.
- Strips spaces from Gmail App Passwords on both save and connection test.
- Added server-side SMTP encryption allow-listing.
- Imported `requests` for Resend validation.
- Kept credentials encrypted through the existing `GlobalEmailConfig` model.

## Production setup

### MailerSend

- Add an API key.
- Use a sender address from a verified MailerSend domain.
- Save, validate, enable the provider, and send a test email.

### Gmail SMTP

- Host: `smtp.gmail.com`
- Port: `587`
- Encryption: `STARTTLS`
- Username: full Gmail address
- Password: a 16-character Google App Password, not the normal account password
- Google 2-Step Verification must be enabled before creating an App Password.

### Important deployment requirement

Keep the same `FERNET_KEY` across redeployments. Changing it makes previously encrypted provider credentials impossible to decrypt, requiring the API key or SMTP password to be entered again.

## Validation performed

- 336 Python files parsed successfully.
- 118 Jinja templates parsed successfully.
- External JavaScript passed `node --check`.
- No inline event handlers remain on the Email & Forms page.
