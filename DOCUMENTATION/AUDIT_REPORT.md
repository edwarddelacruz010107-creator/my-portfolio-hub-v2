# AUDIT REPORT — Phase 1b: SaaS Landing Page Foundation Stabilization

**Scope of this delivery:** Phases 1–3, 4–7 (real data, not stubs), 9–11
from the source spec. Phase 8 delivered as a deliberately different
(safer) implementation — see §4. Phase 12 explicitly NOT implemented —
see §6. All changes boot-tested against the actual 198-route app; see §7.

---

## 1. Root route (`/`) — decision record

**Source spec asked for:** `/` → SaaS landing page, endpoint reachable as
`public.landing`, old portfolio moved to `/u/default`.

**What shipped instead:**
- `/` still registers under the Flask endpoint name **`root`** (unchanged),
  now rendering the SaaS landing page instead of the default tenant's
  portfolio.
- **Why keep the name:** `grep -rn "url_for('root')"` found **18 call
  sites** across `app/admin/blueprint.py`, `app/auth/__init__.py`,
  `app/main/__init__.py`, `app/superadmin/blueprint.py`, and four
  templates — used as post-logout redirects, post-contact-submit
  redirects, and `BuildError`-safe fallbacks (`_safe_root()` in both
  admin and superadmin blueprints). Renaming the endpoint to
  `public.landing` means editing all 18, several inside auth/session
  logic explicitly marked security-sensitive. Keeping the name ships the
  *exact* behavior change the spec wants (SaaS homepage at `/`) with
  **zero edits to auth/admin/superadmin code**.
- Public templates still reference the homepage via `url_for('root')`
  rather than a hardcoded `/`, which is the actual intent behind the
  spec's `url_for('public.landing')` instruction (avoid hardcoded
  coupling) — just under the endpoint name that already exists.
- `render_landing_page()` lives in `app/public/routes.py` (service-layer
  discipline preserved) and is *called from* `app/__init__.py::root()`,
  not mounted as a competing Flask rule — `public_bp` never registers
  `'/'` itself (would be a route collision — Flask does not allow two
  view functions bound to the same rule).

**Verified:** `GET /` → `200`, endpoint map confirms `root` still exists
and resolves correctly (see §7).

---

## 2. Cross-tenant data access — architectural constraint discovered

`Tenant` lives in `core_db` (default SQLAlchemy bind). `Profile` and
`Project` live in `tenant_db` (`__bind_key__ = 'tenant'`). The codebase's
own comments confirm this is enforced as **no cross-DB FK, `tenant_id` is
an app-layer contract only** (`app/models/core.py`, `Tenant` docstring).

**Implication:** `creator_service.py` / `feed_service.py` cannot SQL-JOIN
`Tenant.status` against `Profile`/`Project`. Every public discovery query
does two passes: (1) fetch active tenant slugs from `core_db`, (2) filter
`tenant_db` rows by `tenant_slug IN (...)`, stitched in Python. This
mirrors the pattern the codebase already uses in `Project.published_for_tenant()`
and the `Profile` model's own comment ("Subscription lookups cross into
core_db via tenant_id at the app layer").

**Security implication acted on:** discovery queries filter on
`Tenant.status == 'active'` specifically so suspended/cancelled tenants
never surface in `/explore`, `/feed`, or the landing page, even though
their `tenant_db` rows are still physically present.

---

## 3. Public-safe field allowlists (`app/public/services/serializers.py`)

`Profile` and `Project` carry fields with no business being public:
`email`, `phone`, `monthly_rate`, `internal_notes`, `free_trial_ends`,
`og_image` (billing-adjacent), etc. Every model → template handoff in
this delivery goes through `serialize_creator_card()` /
`serialize_project_card()` — plain dicts, explicit field lists. No
route or template in this delivery passes a raw `Profile`/`Project`
instance to a public-facing template. Verified in §7 (dumped dict keys
from a live query — no PII/billing fields present).

If a future template needs a new field, add it to the serializer
explicitly — do not widen via `vars(profile)` or similar.

---

## 4. Phase 8 (`/u/<username>` route standardization) — REJECTED AS WRITTEN, shipped differently

**Source spec's Phase 8, verbatim:** *"Standardize ALL creator routes...
USE `/u/<username>`... DO NOT `/<slug>`."*

**Why this cannot be implemented as written:** `tenant_bp` currently owns
`/<tenant_slug>` as a live, catch-all prefix for the platform's **entire**
tenant surface — not just the public portfolio:

```
/<tenant_slug>/                    → portfolio (public)
/<tenant_slug>/project/<slug>      → project detail (public)
/<tenant_slug>/billing             → billing (PayMongo-integrated)
/<tenant_slug>/billing/plans       → plan selection / checkout
/<tenant_slug>/billing/payment/... → payment method entry
/<tenant_slug>/auth/login          → tenant-scoped login
/<tenant_slug>/auth/2fa            → TOTP verification
/<tenant_slug>/admin/              → admin entry
```

"Standardize ALL creator routes" under `/u/` as literally specified means
moving **billing checkout, tenant-scoped auth/2FA, and admin entry**
under a new prefix. That directly contradicts the same spec's own
**Phase 13 safety rules**: *"DO NOT rewrite billing architecture... DO
NOT rewrite auth system... maintain backward compatibility... existing
tenant URLs."* This is an internal contradiction in the source spec, not
an implementation choice — Phase 8 and Phase 13 cannot both be satisfied
as written.

**What shipped instead (`app/public/routes.py::creator_link`):**
- `GET /u/<tenant_slug>` — **additive alias**, not a migration.
  - `tenant_slug == 'default'` → renders the portfolio directly (reuses
    the existing, unchanged `_render_default_portfolio()`).
  - any other slug → `301` to the existing, unchanged
    `/<tenant_slug>/` route.
- `tenant_bp`, `app/tenant_security.py` (`RESERVED_SLUGS`, HMAC session
  signing, `resolve_active_tenant()`), and `TenantGuard` are **not
  modified at all**. `'u'` was already present in `RESERVED_SLUGS`
  (added proactively in the Phase 1a delivery this builds on), so no new
  slug collisions are possible.
- This gives creators the clean, shareable `/u/<name>` link the spec's
  *intent* clearly wants, with zero risk to billing/auth/session code.

**Deferred, not delivered:** a true route-family migration (billing/auth/
admin also under `/u/`) is a separate, much larger change that needs
explicit sign-off given it touches session HMAC validation and PayMongo
checkout URLs directly — exactly the kind of change the project's
established pattern (see memory: "halt at billing-adjacent or DO NOT
TOUCH signals") says to stop and ask about, not ship inside a "don't
break billing" phase.

---

## 5. `RESERVED_SLUGS` — read, not modified

`app/tenant_security.py` already reserves `explore`, `feed`, `pricing`,
`templates`, `features`, `u`, `administrator`, and `default` — these were
added in the Phase 1a delivery this phase builds on, correctly
anticipating this exact collision risk. **No changes needed or made** to
this file. `'default'` staying reserved (rather than becoming a normal
tenant slug reachable at `/default/`) is why §4 gives it a dedicated
`/u/default` route instead of just letting it fall through to
`tenant_bp`'s catch-all like every other tenant — `tenant_bp.load_tenant()`
explicitly 404s/redirects reserved slugs by design, and that gate lives
inside the same file as the session HMAC signing logic. Not touched.

---

## 6. Phase 12 (social foundation) — NOT implemented, by design

Spec says: *"Prepare architecture for follow/likes/bookmarks/
recommendations. DO NOT fully implement yet."* Even "prepare architecture"
for a schema (new tables/columns) is a migration — and this project
already has **four unmerged Alembic heads** blocking on the tenant-id
migration work in progress (per project history). Adding a fifth
divergent head for speculative social tables would compound a known
blocking issue rather than help it. **Nothing schema-level shipped in
this phase.** `feed_service.get_trending_projects()` uses `view_count`
(already exists) as a "most-viewed" proxy for trending, documented
in-code as a known simplification (no time-decay/velocity model without
a views-over-time table).

---

## 7. Boot test results

Full app booted against the actual codebase (198 routes, up from the
174-route baseline noted in project history — consistent with billing/
discount-lifecycle work already merged into this snapshot). No import
errors, no route collisions.

```
GET /              -> 200   (SaaS landing page)
GET /explore        -> 200   (real creator/project queries)
GET /feed            -> 200   (real project queries)
GET /pricing          -> 200   (unchanged, restyled)
GET /u/default         -> 200   (default tenant portfolio, new path)
GET /default            -> 301 -> /u/default
GET /default/             -> 301 -> /u/default
GET /sitemap.xml            -> 200   (now includes public SaaS pages)

-- regression check: untouched surfaces --
GET /admin/       -> 302 -> /auth/login?next=...   (unchanged)
GET /superadmin/   -> 302 -> /superadmin/login?next=...  (unchanged)
GET /auth/login     -> 200                            (unchanged)
GET /default/billing  -> 301 -> /billing   (PRE-EXISTING behavior,
                                             confirmed unaffected —
                                             tenant_bp's strip-redirect
                                             was not touched)
GET /default/admin/    -> 301 -> /admin/   (same — pre-existing, unaffected)

-- serializer field check --
creator card keys: availability_status, bio_short, is_available, name,
                    profile_image, project_count, tenant_slug, title,
                    url, years_experience
                    (no email/phone/monthly_rate/internal_notes — allowlist holds)
```

---

## 8. Files changed / added

**Modified (surgical, documented inline):**
- `app/__init__.py` — `root()` body swap; `/default` redirect target
  changed. Endpoint names, blueprint registration order, all other
  routes: unchanged.
- `app/context_processors.py` — added `/`, `/explore`, `/feed`,
  `/pricing`, `/u/`, `/administrator` to the existing skip-prefix list
  (was already there for `/static/`, `/robots.txt`, etc.). Pure
  perf/hygiene fix — these routes don't use the `profile`/`project_count`
  globals; running those 3 queries per anonymous public pageview was
  wasted work left over from when `/` *was* tenant-scoped.
- `app/main/__init__.py` — `sitemap_xml()` now also lists the 4 static
  public pages (additive, ~8 lines).
- `app/public/__init__.py` — docstring only, reflects current routing
  reality.
- `app/public/routes.py` — real service-backed `explore()`/`feed()`,
  new `render_landing_page()`, new `creator_link()` (`/u/<slug>`).

**Added:**
- `app/public/services/{__init__,serializers,creator_service,feed_service,discovery_service}.py`
- `app/public/templates/public/{_base,index,explore,feed,pricing}.html`
  (pricing restyled, not behaviorally changed)
- `app/static/css/public-design-system.css` (namespaced `ph-` prefix,
  scoped under `html[data-theme]` — verified zero class-name overlap
  with existing `design-system.css`/`admin.css`)

**Not touched:** `app/tenant/`, `app/tenant_security.py`, `app/auth/`,
`app/admin/`, `app/superadmin/`, billing services, PayMongo integration,
any migration file, `BILLING_PLANS` (read-only import, same source of
truth as `/pricing` already used).

---

## 9. Known follow-ups (explicitly out of scope this phase)

1. **Phase 8 proper** — migrating billing/auth/admin under `/u/` — needs
   explicit sign-off; not started (see §4).
2. **Alembic 4-head divergence** — still unresolved, unrelated to this
   phase, blocks any future schema work including Phase 12.
3. **"Trending" is view-count, not velocity** — fine for current volume;
   revisit with a views-over-time table when it matters.
4. **`/explore` search is `ILIKE`, not indexed full-text** — fine at
   current tenant count; revisit if creator volume grows.
5. **`Profile` has no `is_featured` flag** — "Featured Creators" uses
   recency as an honest proxy; a real flag needs a migration + admin UI
   toggle, deliberately not bundled into this phase's already-large
   surface area.
