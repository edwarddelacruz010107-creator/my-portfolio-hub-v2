# SEO Live Editor Upgrade

- Added tenant-scoped `/studio/seo/live-save` endpoint with CSRF protection and rate limiting.
- SEO title, description, keywords, profile image alt text, and indexability now autosave after a short debounce.
- Social share images upload immediately after selection/drop and can be removed live.
- Search/social previews and the 4-item SEO checklist update instantly while typing.
- Added visible Saving / Saved / Error status feedback.
- Existing full Save button remains as a manual fallback.
- Portfolio cache is invalidated after each persisted SEO change.
