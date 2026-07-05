# Portfolio Hub — Stabilization Pass Deliverables

## 1. Theme serializer architecture (Phase 1 — production bug fixes)

- `app/services/theme_serializers.py` — new central serializer module.
  Dict-output, alias-mapped, JSON-safe. Replaces ad-hoc getattr chains.
- `app/theme_context.py` — rewired to use the serializers. Signature
  UNCHANGED (all 4 call sites still work as-is):
  `app/tenant/__init__.py`, `app/admin/routes/profile_appearance.py`,
  `app/public/services/theme_preview_data.py`, `app/__init__.py`.
- `app/public/services/theme_preview_data.py` — sample data rewritten
  to mirror real ORM column names; added missing certificate sample
  data (previously omitted from the marketing preview entirely).

### Confirmed production bugs fixed
1. **Testimonials rendered with blank name/role/avatar on every tenant.**
   `theme_context.py` read `.name`/`.role`/`.avatar_url`, none of which
   exist on the `Testimonial` model. Real columns:
   `author_name` / `author_title` / `author_company` / `author_avatar`
   (app/models/tenant_data.py:461-464). `rating` was never read at all.
2. **Certificate skill badges always rendered empty.**
   `theme_context.py` read `.skills_list`, which doesn't exist. Real
   column is `.skills` (comma-separated text,
   app/models/tenant_data.py:559).

All 3 files: pyflakes-clean, boot-tested against realistic ORM-shaped
fake objects and the real 4 call-site kwarg patterns, and verified
against actual Jinja dot-access behavior on dict output (confirmed
template-transparent across all 3 installed themes: default,
developer_pro, futuristic_cyber).

## 2. Superadmin CSS consolidation audit (Phase 4 — in progress)

`superadmin.css` (1942 lines) and `superadmin-unify.css` (1708 lines)
are BOTH loaded in `app/templates/superadmin/base.html` and redefine
37 identical top-level selectors between them. `superadmin-unify.css`
is an incomplete one-directional design-token migration (rem values ->
`var(--space-N)`, fixed border-radii -> `var(--radius-xl)`) layered on
top via cascade rather than a full replacement.

- `css_audit/superadmin-consolidated.css` — cascade-resolved merge of
  all 37 shared selectors into single rule blocks. Behaviorally
  IDENTICAL to current production rendering (verified property-by-
  property, not just visually assumed).
- `css_audit/css_fallthrough_report.txt` — **17 of 37 selectors**
  (`.form-card`, `.status-pill`, `.instruction-card` among them) had
  properties -- some structural (display/flex-direction/gap), not
  cosmetic -- that exist ONLY in the legacy `superadmin.css` and were
  being kept alive purely by cascade fallthrough. A naive "delete the
  old file" approach would have silently broken these.
- `css_audit/css_diff_report.json` — full raw diff data (identical /
  superset / conflicting classification per selector).

### Not yet done (explicitly deferred, not silently skipped)
- The consolidated selectors have NOT been spliced back into
  `superadmin.css` / `base.html` yet, and `superadmin.css` /
  `superadmin-unify.css` have NOT been deleted. Do that only after a
  template-by-template class-reference check across all 37 superadmin
  templates -- that's the actual regression gate for this change, not
  another CSS diff.
- `admin.css` (2729 lines) is also loaded into the superadmin base and
  is very likely tenant-admin panel scope leakage -- flagged, not
  removed, pending your confirmation nothing in superadmin templates
  intentionally depends on it.
- Security audit (Phase 7) not started -- the `.env` file present in
  this upload is a live exposure question worth prioritizing whenever
  you're ready to switch tracks.
