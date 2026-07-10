# Google Structured Data Fix

## Problem
The portfolio homepage generated invalid JSON-LD because social profile URLs were assembled as quoted strings and then HTML-escaped by Jinja. Google received values containing `&#34;` instead of real JSON quotation marks, causing:

- Unparsable structured data
- Incorrect value type
- Rich-result ineligibility

Other JSON-LD values also used the HTML `escape` filter, which does not safely serialize arbitrary text as JSON.

## Fixes applied

- Replaced HTML escaping inside JSON-LD with Jinja's `tojson` serializer.
- Serialized `sameAs` as a real JSON array instead of manually joining quoted strings.
- Serialized Person, ProfilePage, ItemList, CreativeWork, and WebSite dynamic values safely.
- Added the canonical portfolio URL to the ProfilePage `mainEntity` Person.
- Kept the existing public page behavior and visual design unchanged.

## Changed files

- `app/templates/main/index.html`
- `app/templates/base.html`

## Validation performed

- Jinja syntax parsing passed for the changed templates.
- Python `compileall` passed for the application package.
- Confirmed no `|e` or `|escape` filters remain in the affected JSON-LD blocks.

## After deployment

1. Open Google Rich Results Test and test the deployed portfolio URL.
2. In Google Search Console, run URL Inspection and select **Test live URL**.
3. When the error is gone, click **Request indexing** or **Validate fix** where available.
4. Google may take several days to refresh the indexed copy even after the live test passes.
