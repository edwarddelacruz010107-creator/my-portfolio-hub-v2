# Archived CSS

`admin-v3-enhancements.css.unused` — confirmed via grep across all .py/.js/.html files
to have ZERO <link> references anywhere in the codebase as of this audit (2026-06-26).

It is NOT dead weight you can ignore, though: it redefines `.data-table`,
`.badge-status`, and `.form-input` with values that CONFLICT with the
canonical definitions in `admin.css` (different font-weight, different
background colors, several `!important` overrides). If this file is ever
linked into a template again without reconciling those conflicting rules
first, admin/superadmin pages will silently render inconsistent table
headers, badges, and form fields depending on load order.

Recommendation: delete this file once confirmed unneeded, or fold any
genuinely missing rules into admin.css's canonical definitions first.
