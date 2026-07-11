# MyPortfolioHub Comprehensive Technical SEO Audit and Fix

**Build:** Comprehensive SEO / structured-data remediation  
**Domain:** `https://myportfoliohub.online`  
**Database migration:** Not required

## Executive result

The platform now uses one centralized SEO service for the public landing page,
public discovery pages, tenant portfolios, and project case studies. Conflicting
page-level metadata and fragmented JSON-LD were removed from the four active
portfolio themes.

The implementation addresses:

- ProfilePage structured-data errors
- Organization and WebSite identity markup
- CreativeWork project markup
- Breadcrumb structured data
- Canonical URLs
- Open Graph metadata
- Twitter Card metadata
- Dynamic sitemap coverage
- robots.txt rules
- favicon crawlability
- tenant and custom-domain metadata
- noindex handling for private, preview, search, and filtered pages

## Root causes found

1. **ProfilePage markup was inconsistent.** Tenant themes did not share one
   schema contract, and some pages could omit or fragment the required Person
   entity.
2. **Platform pages lacked a consistent Organization/WebSite graph.** The root
   landing page and standalone project/theme browsers had separate head markup.
3. **Canonical URLs were inconsistent.** Some pages used the current request URL
   including mutable query parameters, while others had no canonical tag.
4. **Open Graph and Twitter metadata drifted between templates.** Tenant themes
   and platform pages did not share one renderer.
5. **Theme previews were crawlable.** Sample profile content could be indexed as
   though it were a real tenant portfolio.
6. **Private application surfaces relied only on robots.txt.** They did not
   consistently send an `X-Robots-Tag` response header.
7. **The sitemap did not provide one complete, custom-domain-aware inventory of
   public pages and published case studies.**
8. **A legacy `/feed` alias used a temporary redirect instead of a permanent
   canonical redirect.**
9. **The Explore creator image alt text referenced an undefined project object.**

## Central SEO architecture

### New service

`app/services/seo.py`

This service builds metadata dictionaries and Schema.org graphs for:

- `platform_seo()` — Organization, WebSite, WebPage, optional BreadcrumbList
- `portfolio_seo()` — ProfilePage, Person, project CreativeWork entries,
  BreadcrumbList
- `project_seo()` — WebPage, CreativeWork, Person, BreadcrumbList

The service:

- removes query strings and fragments from canonical identities
- respects verified tenant custom domains
- never inserts placeholder images into Person structured data
- uses real profile/project metadata and tenant SEO settings
- marks drafts, disabled profiles, and preview pages `noindex`
- outputs only absolute image and canonical URLs

### New reusable template components

- `app/templates/partials/_seo_meta.html`
- `app/templates/partials/_portfolio_seo.html`

These render canonical, robots, Open Graph, Twitter Card, and JSON-LD metadata
with the existing CSP nonce.

## Structured data implemented

### Platform pages

- Organization
- WebSite
- WebPage
- BreadcrumbList on nested platform pages

Organization includes:

- official platform name and URL
- 512×512 official logo
- support email/contact point
- configured country/location
- optional official social profiles through `COMPANY_SOCIAL_URLS`

### Tenant portfolio pages

- ProfilePage
- Person as `mainEntity`
- CreativeWork references for visible projects
- BreadcrumbList on platform-host tenant routes

The ProfilePage graph now guarantees that the referenced Person has a non-empty
`name`, stable `@id`, canonical URL, job title, description, and optional real
image/social links.

### Project case-study pages

- WebPage
- CreativeWork
- Person author/creator
- BreadcrumbList

Project metadata includes title, description, image, category, keywords,
published/modified dates when available, author, and canonical case-study URL.

### SoftwareApplication decision

SoftwareApplication review markup is not emitted by default. It activates only
when a genuine review body, author, and rating are explicitly configured and the
same review is visibly published. This avoids manufacturing ratings or emitting
an incomplete application rich-result entity.

## Canonical URL rules

- Homepage: root production URL
- Platform pages: one self-referential canonical URL
- Search/filter pages: canonical to the clean collection URL and `noindex`
- Paginated project/theme pages: self-referential page canonical
- Tenant portfolios: platform tenant URL or verified primary custom domain
- Project pages: matching tenant platform/custom-domain case-study URL
- Theme previews: self URL but `noindex, nofollow, noarchive`
- Legacy `/feed`: permanent `301` to `/projects`

## Sitemap behavior

`/sitemap.xml` now includes:

- homepage
- Explore
- Projects
- Themes
- Pricing
- About the Company
- Privacy Policy
- Terms of Service
- all active and indexable tenant portfolios
- all published, case-study-enabled tenant projects

For a verified tenant custom domain, the sitemap is scoped to that tenant's
portfolio and published project URLs. Duplicate URLs are removed and XML values
are escaped safely.

## robots.txt and index control

`/robots.txt` explicitly allows:

- `/`
- `/favicon.ico`
- `/static/`
- `/uploads/`

It excludes authenticated and operational surfaces such as Studio,
Superadmin, Auth, Billing, API, Heartbeat, Webhooks, and Impersonation.

Those internal routes also receive:

```text
X-Robots-Tag: noindex, nofollow, noarchive
```

The matching logic was written carefully so `/administrator-portfolio/` is not
mistaken for the private `/admin` route.

## Open Graph and Twitter Cards

Every public platform, portfolio, and project page now provides:

- title
- description
- canonical URL
- content type
- site name
- image and image alt text when available
- Twitter card type
- Twitter title, description, and image

Tenant SEO & Sharing settings remain the source for custom profile titles,
descriptions, social images, keywords, image alt text, and indexing preference.

## Favicon verification

The existing centralized favicon implementation remains intact:

- `/favicon.ico`
- 48×48 and 96×96 PNG favicons
- Apple touch icon
- web manifest
- crawlable local URLs
- one-day cache behavior

No favicon or CSP weakening was introduced.

## Important modified files

```text
app/__init__.py
app/context_processors.py
app/main/__init__.py
app/public/routes.py
app/public/templates/public/_base.html
app/public/templates/public/explore.html
app/public/templates/public/index.html
app/public/templates/public/projects.html
app/public/templates/public/themes.html
app/services/seo.py
app/templates/base.html
app/templates/main/index.html
app/templates/main/project.html
app/templates/partials/_portfolio_seo.html
app/templates/partials/_seo_meta.html
config.py
env.production.template
ENV_ADDITIONS.txt
README.md
robots.txt
scripts/audit_public_seo.py
tests/test_comprehensive_seo.py
themes/default/templates/index.html
themes/developer_pro/templates/index.html
themes/blockform_brutal/templates/index.html
themes/schematic_spec/templates/index.html
```

## Automated validation

Completed successfully:

- Flask application startup and route registration
- root homepage rendering
- public Explore, Projects, and Themes rendering
- administrator portfolio rendering
- canonical tag uniqueness
- Open Graph and Twitter metadata presence
- ProfilePage → Person `mainEntity` integrity
- Organization/WebSite/WebPage graph integrity
- CreativeWork and BreadcrumbList project graph integrity
- JSON-LD JSON parsing
- theme preview noindex headers and metadata
- private route X-Robots-Tag behavior
- robots.txt rules
- dynamic sitemap output
- favicon route and assets
- Python syntax compilation
- Jinja syntax parsing

Test result:

```text
43 passed
342 Python files parsed
132 Jinja templates parsed
```

## Deployment

1. Deploy the updated project.
2. No database migration is needed for this SEO pass.
3. Confirm production has:

```env
APP_BASE_URL=https://myportfoliohub.online
COMPANY_NAME=MyPortfolioHub
SUPPORT_EMAIL=hello@myportfoliohub.online
COMPANY_LOCATION=Philippines
COMPANY_SOCIAL_URLS=
```

4. Keep `COMPANY_SOCIAL_URLS` empty until official company profiles exist.
5. Do not configure review variables unless the same genuine review is visibly
   shown on the public website.
6. Run the post-deploy smoke test:

```bash
python scripts/audit_public_seo.py --base-url https://myportfoliohub.online
```

To include a published project:

```bash
python scripts/audit_public_seo.py \
  --base-url https://myportfoliohub.online \
  --portfolio /administrator-portfolio/ \
  --project /administrator-portfolio/project/YOUR-PROJECT-SLUG
```

## Search Console verification

After deployment:

1. Inspect the homepage and run **Test Live URL**.
2. Inspect `/administrator-portfolio/` and one published case-study URL.
3. Request indexing for each important URL.
4. Resubmit `/sitemap.xml`.
5. Open the Enhancements reports for Profile page and Breadcrumbs.
6. Test the same URLs in Google's Rich Results Test.
7. Validate the complete JSON-LD graph in Schema Markup Validator.
8. Recheck the favicon URLs and allow Google time to recrawl its favicon cache.

## Expected production URLs

```text
https://myportfoliohub.online/robots.txt
https://myportfoliohub.online/sitemap.xml
https://myportfoliohub.online/favicon.ico
https://myportfoliohub.online/administrator-portfolio/
https://myportfoliohub.online/projects
https://myportfoliohub.online/themes
```
