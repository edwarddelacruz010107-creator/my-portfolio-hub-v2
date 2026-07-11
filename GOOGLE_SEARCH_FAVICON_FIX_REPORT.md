# MyPortfolioHub Google Search Favicon Fix

## Scope

This patch centralizes the official MyPortfolioHub icon across the Flask root landing page, public pages, tenant portfolio themes, authentication pages, admin/superadmin pages, robots rules, response headers, and optional Nginx deployment configuration.

Source branding asset: the supplied 500×500 transparent PNG with the MPH gradient mark.

## Root cause found

The old favicon setup was inconsistent and unreliable for Google Search:

1. There was no explicit public `/favicon.ico` Flask route.
2. The root landing page was a standalone template and used only `/static/image/icon.ico`.
3. The shared public base emitted several conflicting icon declarations.
4. Some declared files did not exist, including:
   - `app/static/img/brand/favicon.ico`
   - `app/static/img/brand/apple-touch-icon.png`
   - `app/static/img/brand/icon-192.png`
5. Several files with `.png` extensions were actually ICO containers, so their file extension and MIME expectations disagreed:
   - `app/static/img/brand/favicon.png`
   - `app/static/img/brand/icon-512.png`
   - `app/static/img/brand/apply-touch-icon.png`
6. Every tenant theme maintained its own favicon tags, including a typo path (`apply-touch-icon.png`), instead of using one shared head component.
7. `robots.txt` allowed the public site generally, but did not explicitly document access to `/favicon.ico` and `/static/`.
8. The optional Nginx config gave all non-hashed static files a one-year immutable cache, which is too aggressive for stable favicon filenames that may be replaced.

The existing Content Security Policy was not the blocker: `img-src` and `default-src` already allow `'self'`.

## Final implementation

### Canonical asset directory

`app/static/images/favicon/`

Files:

- `favicon.ico` — multi-resolution ICO: 16, 32, 48, 64, 128, and 256 pixels
- `favicon-16x16.png`
- `favicon-32x32.png`
- `favicon-48x48.png`
- `favicon-96x96.png`
- `apple-touch-icon.png` — 180×180
- `icon-192x192.png`
- `icon-512x512.png`
- `site.webmanifest`

All PNG assets are true PNG files, square, and generated from the supplied MPH brand icon.

### Public root route

A concrete Flask route is registered before the tenant catch-all blueprint:

```text
GET /favicon.ico
→ 200 image/vnd.microsoft.icon
```

The route has no authentication, database query, or tenant lookup dependency. It also returns:

```text
Cache-Control: public, max-age=86400
X-Content-Type-Options: nosniff
```

### Shared head component

All relevant templates now use:

`app/templates/partials/_favicons.html`

The partial emits root-safe URLs for:

- 48×48 PNG
- 96×96 PNG
- `/favicon.ico`
- shortcut icon
- Apple touch icon
- web manifest

The root landing page, `/projects`, `/themes`, shared public base, all four tenant themes, and standalone auth/admin templates now use the same component.

### Robots and CSP

Dynamic `/robots.txt` now explicitly allows:

```text
Allow: /favicon.ico
Allow: /static/
```

Private application routes remain disallowed. No CSP weakening was required because all favicon resources are same-origin.

### Cache behavior

Flask applies a one-day cache to `/favicon.ico` and `/static/images/favicon/*`.

The optional Nginx configuration now gives the favicon directory a one-day cache instead of inheriting the generic one-year immutable static policy.

## Public URLs after deployment

- `https://myportfoliohub.online/favicon.ico`
- `https://myportfoliohub.online/static/images/favicon/favicon-48x48.png`
- `https://myportfoliohub.online/static/images/favicon/favicon-96x96.png`
- `https://myportfoliohub.online/static/images/favicon/apple-touch-icon.png`
- `https://myportfoliohub.online/static/images/favicon/icon-192x192.png`
- `https://myportfoliohub.online/static/images/favicon/icon-512x512.png`
- `https://myportfoliohub.online/static/images/favicon/site.webmanifest`

## Automated validation performed

- Flask application factory loaded successfully in testing mode.
- 337 Python files passed AST syntax validation.
- 129 Jinja templates parsed successfully.
- 19 favicon regression tests passed.
- `/favicon.ico` returned HTTP 200 with `image/vnd.microsoft.icon`.
- Static PNG and manifest URLs returned HTTP 200 with correct MIME types.
- Favicon requests did not redirect to login.
- Favicon requests did not return HTML.
- Root, `/projects`, `/themes`, and `/administrator-portfolio/` rendered the canonical favicon set.
- All four public theme previews rendered the canonical favicon set.
- Favicon route worked with a custom tenant-domain Host header.
- `robots.txt` allowed favicon and static crawling.
- CSP retained same-origin image support.
- Favicon PNG dimensions and manifest JSON were validated.
- No conflicting legacy favicon tags remain in templates.

## Deployment

No database migration or new environment variable is required.

Deploy the project normally, then verify every public URL above returns HTTP 200. Use a hard refresh for browser-tab testing because favicon caching is aggressive.

## Google Search Console verification

1. Open the URL Inspection tool for `https://myportfoliohub.online/`.
2. Run **Test live URL**.
3. Confirm the page is crawlable and the rendered HTML contains the favicon links.
4. Click **Request indexing**.
5. Keep `https://myportfoliohub.online/sitemap.xml` submitted in the Sitemaps report.
6. Recheck the search result after Google recrawls the homepage.

Google can continue showing the globe temporarily because favicon processing and cache refresh are not immediate. The implementation must first be reachable and crawlable; requesting indexing does not guarantee an instant visual update.
