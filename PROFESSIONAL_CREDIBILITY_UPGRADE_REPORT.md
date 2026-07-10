# MyPortfolioHub Professional Credibility Upgrade

## Scope

This release implements the requested credibility, visual-discipline, project case-study, free-tier, and SEO improvements. The domain name and top-level domain were intentionally left unchanged.

## 1. Trust and company credibility

- Added `/about-company` with platform purpose, audience, security approach, location, support contact, and policy links.
- Added public `/privacy` and `/terms` pages.
- Added configurable `COMPANY_NAME`, `SUPPORT_EMAIL`, and `COMPANY_LOCATION` settings.
- Linked the company and legal pages from the public navigation/footer.
- Removed the inactive Careers placeholder from the landing footer.

## 2. Professional theme standards

The curated four-theme catalog remains:

- Default
- Developer Pro
- Blockform Brutal
- Schematic Spec

Changes:

- Each theme now uses no more than two text font families.
- Added search-indexing and keyword metadata to all themes.
- Added profile and project image alt-text support.
- Added reduced-motion behavior.
- Removed Developer Pro's custom cursor, boot preloader, matrix canvas, scanline, and intrusive pointer effects.
- Reduced Blockform's rotations, shadow offsets, colored section backgrounds, and aggressive hover movement while preserving its identity.

## 3. Trial project allowance

Trial accounts can now create up to **10 projects**. The limit is synchronized across:

- Core plan features
- Runtime capability enforcement
- Trial-plan defaults
- Superadmin subscription settings

## 4. Structured project case studies

Project editing now supports:

- Rich, sanitized project overview
- Challenge/problem section
- Solution/process section
- Results/impact section
- Before and after images with alt text
- Interactive prototype URL and embedded preview
- Project-specific client testimonial
- Project meta title and description
- Cover-image alt text
- Case-study visibility toggle

The public case-study page renders these sections responsively and uses a restricted iframe sandbox plus CSP-approved prototype providers.

## 5. Native SEO and accessibility tools

Added **Studio → SEO & Sharing** with:

- Portfolio meta title
- Portfolio meta description
- Focus keywords
- Search-engine index/no-index control
- Profile image alt text
- Social share image upload/removal
- Search-result preview
- SEO completion checklist

Project editors also include project-specific SEO and alt-text controls. Metadata is used by the default base template and all four curated themes.

## 6. Security controls

- Added an allow-list rich-text sanitizer.
- Scripts, styles, iframes, embedded objects, SVG, event handlers, unsafe attributes, and unsafe URL schemes are removed from authored rich text.
- Rich content is sanitized before storage and again before rendering.
- Removed inline image-error handlers from the project detail page and replaced them with CSP-compatible JavaScript listeners.
- Prototype frames use sandboxing and strict referrer policy.

## Deployment

1. Deploy the updated code.
2. Apply migrations:

```bash
flask db upgrade
```

3. Confirm these production variables:

```env
COMPANY_NAME=MyPortfolioHub
SUPPORT_EMAIL=hello@myportfoliohub.online
COMPANY_LOCATION=Philippines
```

4. Restart the service and test:

- `/about-company`
- `/privacy`
- `/terms`
- `/studio/seo`
- `/studio/projects/new`
- A public project case-study URL

## Database migration

Revision:

```text
0052_project_case_studies_and_seo
```

It adds the new profile SEO and project case-study fields. The startup schema validator also contains idempotent compatibility additions for existing production databases.
