# Blockform Brutal Theme Integration

This build adds the uploaded Blockform HTML as a fully CMS-bound Portfolio CMS theme.

## New theme

- Theme ID: `blockform_brutal`
- Display name: `Blockform Brutal`
- Required plan: `Pro`
- Preview image: `/static/themes/blockform_brutal/preview.svg`
- Template: `themes/blockform_brutal/templates/index.html`
- Source reference: `themes/blockform_brutal/source_reference/uploaded_blockform_static.html`

## Bound CMS features

The theme is wired to the shared `portfolio` theme context and supports:

- Profile name, title, subtitle, bio, short bio, location, phone, email
- Profile/avatar image
- Resume/CV download link
- Social links: GitHub, LinkedIn, X/Twitter, Facebook, Instagram, Behance, email
- Project cards with image, featured badge, category, views, likes, tech stack, case study, demo, and source links
- Skill categories with proficiency bars
- Services with feature tags
- Work experience/timeline
- Testimonials with rating, author/avatar, and role display
- Certificates and badge images with issuer, date, description, skills, and verification link
- Billing navigation link, same as the default theme
- AJAX contact form using the existing contact backend
- Responsive mobile navigation
- Dark/light mode toggle
- CSP-safe inline JavaScript nonce support

## Safety notes

The `.reveal` elements are visible by default. JavaScript only enhances animation. This avoids the previous blank-theme problem where content stayed hidden if JS or CSP failed.
