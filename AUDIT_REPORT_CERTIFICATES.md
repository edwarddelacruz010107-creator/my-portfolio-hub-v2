# AUDIT REPORT — Certificates & Badges System (v6.7)

Base: `my-portfolio-hub-v6_6-patched (1)` (phase4b-admin_superadmin-SPLIT)
Scope: full-stack feature addition, additive-only, zero edits to unrelated
route/model/query behavior.

## 1. What was built

| Layer | File | Status |
|---|---|---|
| Model | `app/models/tenant_data.py` — `Certificate` class | new |
| Model re-export | `app/models/__init__.py`, `app/models/portfolio.py` | updated |
| Repository | `app/repositories/certificate_repository.py` | new |
| Repository registry | `app/repositories/__init__.py` | updated |
| Service | `app/services/certificate_service.py` | new |
| Form | `app/forms/__init__.py` — `CertificateForm` | updated |
| Admin routes | `app/admin/routes/certificates.py` (list/new/edit/delete/toggle-featured/toggle-visible/reorder) | new |
| Route registration | `app/admin/routes/__init__.py` | updated |
| Admin templates | `app/templates/admin/certificates.html`, `certificate_form.html` | new |
| Sidebar nav | `app/templates/admin/base.html` | updated (one link added) |
| Public data (tenant) | `app/tenant/__init__.py` — `portfolio()` route | updated |
| Public data (default tenant) | `app/__init__.py` — `_render_default_portfolio()` | updated |
| Theme context | `app/theme_context.py` — `build_portfolio_view()` | updated |
| Frontend section | `themes/default/templates/index.html` — new `#certificates` section + nav links | updated |
| Styling | `app/static/css/style.css` — `.certificates-grid`/`.certificate-card` block | updated |
| Migration | `migrations/versions/0036_certificates.py` | new |
| Alembic tenant env | `migrations/tenant/env.py` | updated (registers `Certificate`) |

## 2. Architecture conformance

- **Routes → Services → Repositories → Models**, no exceptions. `certificates.py`
  routes contain zero ORM query construction beyond the repository's `.query`
  escape hatch (same convention as `testimonials.py`) and zero direct file I/O
  — both delegate to `certificate_service`.
- **Tenant isolation**: `Certificate` carries `tenant_id` + `tenant_slug`, no
  `ForeignKey` (this codebase's tenant-data tables live in a **separate physical
  database** under `__bind_key__ = "tenant"` — a cross-DB FK is not possible,
  confirmed by every existing tenant-data model). Every read/write path is
  tenant-scoped:
  - Admin list/reorder: `certificate_repository.list_for_tenant(slug)` /
    `reorder_certificates(slug, ...)` — filters by `tenant_slug` before any
    id is touched, so a forged `id` in a reorder payload can only ever affect
    rows already scoped to the caller's tenant.
  - Admin edit/delete/toggle: `_require_tenant_object()` (existing helper,
    unmodified) — blocks cross-tenant access and logs the attempt.
  - Public portfolio: both `tenant.portfolio()` and `_render_default_portfolio()`
    filter `tenant_slug=<resolved tenant>` and `is_visible=True` — unpublished
    or other-tenant certificates never reach a template context.
- **Upload pipeline**: reuses `app.utils.save_image` / `delete_image` exactly
  as `testimonials.py`/`projects_uploads.py` do — no parallel storage system.
  Certificate images/badges land in `UPLOAD_FOLDER/certificates/`, are magic-byte
  validated (existing `HIGH-08` check in `save_image`), and are counted against
  the tenant's `max_media_uploads` plan quota (new: extends
  `_tenant_media_upload_count()` usage — see §4).
- **No wildcard imports, explicit exports** — `CertificateRepository`/
  `certificate_repository` added to `app/repositories/__init__.py.__all__`.

## 3. Deliberate deviations from the original spec, with rationale

| Spec asked for | What was built instead | Why |
|---|---|---|
| `db.ForeignKey("tenants.id")` on the model | No FK, app-layer `tenant_id`/`tenant_slug` filtering | `tenants` lives in `core_db`; `Certificate` lives in `tenant_data_db` (separate bind/physical DB). A cross-DB FK isn't valid SQLAlchemy/SQL. This matches `Testimonial`/`Service`/`Project` exactly. |
| One combined "tenant + default tenant admin dashboard" | Single `admin` blueprint, tenant-resolved via `_active_tenant_slug()` | This codebase already serves both tenant and default-tenant admins through one blueprint (`app/admin/blueprint.py`) — there is no separate "default tenant admin" blueprint to duplicate into. Building a second one would be the actual architecture break. |
| Certificates rendering on "tenant portfolio page and default tenant portfolio" (implying all themes) | Wired into `themes/default` only; `developer_pro` and `futuristic_cyber` untouched | Neither of those two themes renders **Testimonials or Services either** — they're minimal themes that never had those sections built out. Certificates is now at parity with the two other existing "extra content" sections, not behind them. Bolting a certificates section onto two themes that don't share Testimonials/Services' CSS system risked producing visually inconsistent, rushed sections — flagging this explicitly rather than shipping that. |
| `templates/public/sections/certificates.html` as a separate includable partial | Inlined directly into `themes/default/templates/index.html`, matching how Testimonials/Services/Projects are done in this codebase | This app's theme system doesn't use an includable-partials pattern for sections (checked: no `{% include %}` for testimonials/services in `index.html`) — a section partial would be new, unprecedented architecture, not existing convention. |

## 4. One security fix delivered as a byproduct (in-scope, not a scope-creep)

`_tenant_media_upload_count()` (in `app/admin/blueprint.py`) only counted
Profile/Project/Testimonial image usage. Left unmodified, certificate images
and badges would have been **invisible to the plan-quota system** — a
tenant on the Basic plan's `max_media_uploads` cap could have uploaded
unlimited certificate images/badges as a bypass. `certificates.py` extends
the count locally (`_certificate_upload_slots_used()`) rather than editing
the shared helper in `blueprint.py`, so no existing call site's behavior
changes — this is additive-only, verified by the diff.

## 5. Known blocking issue — NOT resolved by this delivery

`migrations/versions/` currently has **3 unmerged Alembic heads**
(`0011_add_paymongo_subscription`, `0028_add_email_only_provider`,
`0035_theme_catalog_extended`) — confirmed by revision-graph walk during this
session. `0036_certificates.py` chains off `0035_theme_catalog_extended` and
will apply cleanly **once** the heads are merged, but a bare
`flask db upgrade` will fail with "Multiple head revisions are present" until
then. Resolving that divergence was explicitly out of scope here — merging
heads is a schema-wide operation that deserves its own single-purpose,
reviewed migration, not a side effect of a feature PR. Recommended next step:

```
flask db merge -m "merge heads pre-0036" \
    0011_add_paymongo_subscription \
    0028_add_email_only_provider \
    0035_theme_catalog_extended
flask db upgrade
```

Run this against a disposable/staging DB first — a 3-way merge revision has
never been exercised in this codebase's history yet, per `PHASE4_AUDIT.md`.

## 6. Verification performed this session

- `py_compile` on all 13 new/touched Python files — clean.
- Jinja2 `Environment.get_template()` parse check on `admin/certificates.html`,
  `admin/certificate_form.html`, `admin/base.html`, and
  `themes/default/templates/index.html` — clean, no syntax errors.
- Standalone Flask-SQLAlchemy round-trip test of the `Certificate` model
  (isolated from the rest of `app.models` to avoid pulling in unrelated
  third-party deps not needed for this check): table creation under
  `__bind_key__ = "tenant"`, insert, query, `skills_list`/`is_expired`
  properties, and `to_dict()` all confirmed working against a live SQLite
  session.
- **Not run** (would require the full app context — DB credentials, Redis,
  etc. — not available in this sandbox): `create_app()` boot test, live
  `flask db upgrade` against a real Postgres tenant DB, and an end-to-end
  browser check of the rendered `#certificates` section. Recommend running
  the existing boot-test command from `PHASE4_AUDIT.md`
  (`create_app()` → expect **10 blueprints, same route count + 6 new
  certificate routes**) before merging.

## 7. Final checklist (against the original spec's own checklist)

| Item | Status |
|---|---|
| Tenant isolation | ✅ verified via code review — repository/route layer only |
| Admin CRUD | ✅ list/create/edit/delete/toggle-featured/toggle-visible/reorder |
| Uploads | ✅ reuses existing pipeline; quota-gated (see §4) |
| Frontend rendering | ✅ `default` theme only (see §3) |
| Dark/light mode | ✅ uses existing CSS custom properties (`--bg-card`, `--accent`, etc.), no hardcoded colors |
| Responsive layout | ✅ 3/2/1-column grid breakpoints added |
| Featured sorting | ✅ `display_order` + `is_featured` badge/border treatment |
| Image validation | ✅ existing `save_image` magic-byte + extension checks, unchanged |
| Existing portfolio still works | ✅ zero lines removed from any pre-existing route/template; all diffs are additive |
| No blueprint registration errors | ⚠️ not boot-tested in this sandbox — see §6 |
| No circular imports | ✅ verified by import graph: `services/` → `repositories/` → `models/`, never the reverse; `admin/routes/certificates.py` → `services/` + `admin/blueprint`, not the other way |
| No broken templates | ✅ Jinja parse-checked |
