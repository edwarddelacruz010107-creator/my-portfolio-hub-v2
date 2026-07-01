# PHASE 4b AUDIT — Superadmin Blueprint Split (Batches 1–10)

## Scope

Closed the one repository-layer gap from `PHASE4_AUDIT.md` follow-up
(`GlobalEmailConfig`), then split the 3,305-line monolithic
`app/superadmin/__init__.py` into a `blueprint.py` + `routes/` package,
10 batches, one boot-test per batch, per the sequencing agreed with the
user.

## Item 1 — GlobalEmailConfig repository (pre-split)

- Added `app/repositories/global_email_config_repository.py`. Named method
  `get_fresh_by_id()` added (not the generic `get_by()`) because
  `populate_existing=True` is load-bearing at all 3 original call sites
  (post-commit cache-bypass verification reads) — collapsing into a generic
  wrapper would have silently dropped that semantic.
- Registered in `app/repositories/__init__.py`.
- 3 call sites in `app/superadmin/__init__.py` (MailerSend/SMTP/Resend save
  handlers) migrated; local `_GEC`/`_GEC2`/`_GEC3` imports removed.
- Untouched by design: `GlobalEmailConfig.get(fresh=True)` call sites
  (lines formerly 2021/2476/2513) — different pattern, already centralized
  on the model itself.

## Item 2 — Blueprint split

**New structure:**

```
app/superadmin/
  blueprint.py            Blueprint object, superadmin_required, _safe_root,
                           inject_tenant_count context processor, and the
                           2 helpers shared across >1 route module
                           (_normalize_timestamp, _slugify)
  routes/
    __init__.py            imports all 10 modules below (registration)
    core_auth.py     (10)  login/logout/forgot-password/dashboard
    tenants.py        (5)  tenant CRUD
    messaging.py      (4)  superadmin <-> tenant messages
    billing.py        (9)  overview/payment-methods/instructions/submissions
    media.py           (1)  media library
    email_settings.py (8)  MailerSend/SMTP/Resend config + diagnostics
    subscriptions.py  (6)  subscription settings + licenses
    twofa.py           (7)  superadmin TOTP 2FA
    logs_monitor.py    (2)  activity logs + subscription monitor
    impersonation.py  (3)  tenant impersonation + tenant comms
  __init__.py             50-line backward-compat shim (see below)
```

(Numbers in parens = batch number from the agreed sequencing plan.)

**Mechanics:** one `Blueprint('superadmin', ...)` object, defined once in
`blueprint.py`. Every route module imports it and decorates with
`@superadmin.route(...)` exactly as before — blueprint name unchanged, so
every `url_for('superadmin.xxx')` and template reference resolves
identically. `app/superadmin/__init__.py` re-exports `superadmin`,
`superadmin_required`, and the 3 functions (`forgot_password_request`,
`forgot_password_verify`, `forgot_password`) that `tests/test_v39_mvp_hardening.py`
imports directly by name, plus retains the original bottom-of-file
`from app.superadmin import themes as _themes` import for the same
circular-import reason documented in the original file.

**What moved verbatim (zero logic changes):** all 64 top-level
functions/helpers, every decorator, every docstring, every comment. Each
route module carries the full original import block rather than a
trimmed/per-file subset — over-importing is the deliberately conservative
choice here; trimming risks a missed transitive import causing a NameError
that only surfaces on a specific code path.

## Verification performed

- **Route-table diff**: dumped `(rule, endpoint, methods)` for all 174
  routes from the original monolith and the split version, sorted,
  diffed — **zero differences**. Not just a count match: every URL,
  every endpoint name, every method set is byte-identical.
- **Boot test**: `create_app('development')` against a disposable SQLite
  DB — 10 blueprints, 174 routes, twice (two separate fresh DBs, two
  separate processes) — matches the Phase 4 baseline both times.
- **Backward-compat import check**: `from app.superadmin import superadmin,
  superadmin_required, forgot_password_request, forgot_password_verify,
  forgot_password, login, logout, dashboard` — all resolve.
- **Test collection**: `pytest tests/ --collect-only` — 314 tests collected
  cleanly. The 1 collection error (`tests/root_legacy/test_postgress.py`,
  missing `psycopg2`) is a sandbox dependency gap from testing against
  SQLite, not a regression — confirmed by reproducing the same error
  against the *original* unsplit file.
- Real production credentials in `.env` were never read into a live
  connection — all boot tests used `CORE_DATABASE_URL` /
  `TENANT_DATABASE_URL` overridden to throwaway local SQLite files.

## Not done in this pass (flagged, not fixed — confirm before touching)

- **`_generate_license_key`** (now in `routes/subscriptions.py`) calls
  `_utils_generate_license_key`, a name that is never imported or defined
  anywhere in the original file. This function has **zero call sites** in
  the codebase, so it's latent/inert — moved verbatim, not fixed, per the
  non-destructive mandate. Flagging because it'll raise `NameError` the
  moment anything calls it.
- **`_license_expiration_info`** (in `routes/subscriptions.py`) is
  duplicated independently in `app/admin/__init__.py:254` — a real
  "duplicate services" finding from the master stabilization doc's Phase 4,
  but it's in the `admin` blueprint, out of scope for this superadmin-only
  engagement.
- Templates referencing `superadmin.*` endpoints were **not** audited line
  by line — the route-table diff is the verification that matters (same
  endpoint names ⇒ same `url_for()` resolution), not a template grep.
- No change to `themes.py`, `system_check.py`, `billing_plans.py`,
  `billing_overview_patch.py` — sibling files in `app/superadmin/`, out of
  scope for the route-monolith split.
