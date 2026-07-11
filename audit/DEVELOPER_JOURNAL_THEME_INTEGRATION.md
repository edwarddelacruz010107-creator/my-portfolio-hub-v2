# Developer Journal Theme Integration

## Summary

The uploaded static portfolio HTML was converted into a production MyPortfolioHub CMS theme named **Single Portfolio — Developer Journal**.

- Theme ID: `developer_journal`
- Version: `1.1.0`
- Plan access: Free, Trial, Basic, Pro, Enterprise, and Administrator
- Theme category: Developer
- Theme icon: `⎇`

## Dynamic CMS bindings

The theme uses the shared `portfolio` contract from `app/theme_context.py` and supports:

- Tenant profile name, title, biography, location, phone, email, avatar, availability, and resume
- Grouped skills and proficiency data
- Projects with images, category filters, views, likes, technology tags, case-study links, demo links, and source links
- Work experience rendered as a commit-history timeline
- Services and feature tags
- Certificates and badges with an interactive preview modal
- Testimonials, ratings, avatars, roles, and carousel navigation
- Social links
- Tenant/default billing navigation
- Real backend contact-form submission
- Central favicon, canonical metadata, Open Graph, Twitter Cards, and structured data

## Production safety

- JavaScript is served from `/static/themes/developer_journal/theme.js` and does not rely on inline event handlers.
- The completion pass also binds experience achievements and locations, service subtitles,
  project prototype links and completion dates, full certificate credential metadata,
  every supported social profile, and animated tenant statistics.
- Content remains visible if JavaScript fails; reveal animation cannot create a blank theme.
- The mobile menu, project filtering, certificate modal, testimonial carousel, theme mode, and contact form are CSP-safe.
- Image URLs use the centralized portfolio context, including Cloudinary and other configured storage providers.
- The theme is included in `SUPPORTED_THEME_IDS`, so preview and Apply Theme use the existing verified persistence flow.

## Files added

- `themes/developer_journal/theme.json`
- `themes/developer_journal/templates/index.html`
- `themes/developer_journal/SOURCE_ORIGINAL.html`
- `app/static/themes/developer_journal/theme.js`
- `app/static/themes/developer_journal/preview.svg`
- `tests/test_developer_journal_theme.py`

## Files updated

- `app/theme_engine.py`
- `app/theme_context.py`
- `tests/test_favicon_system.py`
- `tests/test_comprehensive_seo.py`
- `README.md`

## Deployment

After deployment, use **Superadmin → Theme Catalog → Sync from Disk** once. The new theme will then be available in Studio → Themes. It is configured as a free theme, so Trial tenants can apply it without a plan upgrade.
