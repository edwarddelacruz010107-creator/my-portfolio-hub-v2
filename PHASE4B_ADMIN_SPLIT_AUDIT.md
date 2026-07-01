# PHASE 4b AUDIT — Admin Blueprint Split (Batches 1–10)

## Scope

Same treatment as the superadmin split, applied to the other large
monolithic blueprint: `app/admin/__init__.py` (2,611 lines, 63 routes,
77 functions).

## New structure

```
app/admin/
  blueprint.py                  Blueprint object, admin_required,
                                 before_request gate (block_public_admin),
                                 and the 7 tenant-resolution helpers used
                                 across 2+ route modules
  routes/
    __init__.py                  imports all 10 modules (registration)
    core_auth.py          (1)   license gate, dashboard, login/reset-password
                                 aliases, forgot-password flow
    billing.py             (2)   billing index/plans/payment/history
    messaging.py            (3)   admin <-> superadmin messaging
    profile_appearance.py  (4)   profile editing + theme appearance
    skills.py               (5)   skills CRUD
    projects_uploads.py    (6)   projects CRUD + media uploads
    testimonials.py         (7)   testimonials CRUD
    services.py              (8)   services CRUD
    settings_2fa.py          (9)   settings/activity/export + TOTP 2FA
    notifications_email.py (10)   notifications + email services config
  __init__.py                   53-line backward-compat shim
```

## Why `block_public_admin` and the tenant helpers stayed in `blueprint.py`

`block_public_admin` is registered via `@admin.before_request` — it has to
be bound once, on the blueprint object, not duplicated per route module
(duplicating it would run the gate N times per request). `_tenant_media_upload_count`
turned out to be called from `profile_appearance.py`, `projects_uploads.py`,
*and* `testimonials.py` — 3 different route modules — so unlike the
superadmin split (where most helpers were single-batch), this file's
tenant-resolution helpers (`_active_tenant_slug`, `_load_tenant_profile`,
`_tenant_slug_filter`, `_require_tenant_object`, `_active_tenant_plan_features`,
`_active_tenant_plan_name`) are used broadly enough (7–29 call sites each)
that all of them live in `blueprint.py`, not just the 2 borderline ones.

## Verification performed

- **Route-table diff**: all 174 app-wide routes (not just admin's 63),
  dumped as `(rule, endpoint, methods)` from the original monolith and the
  split version, sorted, diffed — **zero differences**.
- **Boot test**: `create_app('development')` against disposable SQLite —
  10 blueprints, 174 routes, both before and after.
- **Backward-compat import check**: `from app.admin import admin,
  admin_required, _active_tenant_slug, _load_tenant_profile,
  _require_tenant_object, _tenant_slug_filter, block_public_admin` — all
  resolve (this exact set is what `tests/test_default_tenant_hardening.py`
  and `tests/root_legacy/test_default_admin_isolation.py` import directly).
- **Test collection**: `pytest tests/ --collect-only` — 314 tests, same
  single pre-existing `psycopg2`-missing error as the superadmin pass
  (sandbox SQLite testing artifact, not a regression — reproduced
  identically against the original file).
- `app/admin/upload_handlers.py` (sibling file, defines its own
  `uploads_bp` Blueprint) was confirmed self-contained — it does not
  import anything from `app/admin/__init__.py`, and `app/__init__.py`
  does not currently register it. Untouched, unaffected.

## Not done in this pass (flagged, not fixed)

- **`_license_expiration_info`** now exists in 3 places:
  `app/admin/blueprint.py` (moved verbatim from the original), plus the
  two locations already flagged in the superadmin audit
  (`app/superadmin/routes/subscriptions.py` and the original
  `app/admin/__init__.py:254` it was copied from). This split didn't
  create the duplication — it pre-dates this engagement — but it's now
  more visible since both blueprints have a clean home for it. A real fix
  would extract this to a shared `app/services/licensing.py` and have
  both blueprints import it; flagging for your sign-off since it's a
  cross-blueprint change, not a route-module-local one.
- `LICENSE_PLANS` in `app/admin/blueprint.py` remains dead/deprecated
  (no call sites), moved verbatim per the non-destructive mandate.
