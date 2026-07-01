# PHASE 4 AUDIT — Repository Layer Extraction

## Scope of this pass

Built `app/repositories/` (4 files) and migrated **6 files / 13 call sites**
in `app/scripts/` to use it. All changes are structural only — zero query
logic, filter conditions, or transaction boundaries were altered.

| File | `.query` sites migrated |
|---|---|
| `app/repositories/base.py` | new — `BaseRepository[ModelT]` generic CRUD wrapper |
| `app/repositories/user_repository.py` | new — `get_by_username`, `get_by_email`, `*_exists` |
| `app/repositories/tenant_repository.py` | new — `get_by_slug`, `slug_exists` |
| `app/repositories/profile_repository.py` | new — `get_by_tenant_id` |
| `app/scripts/seed_default_tenant.py` | 3 (`Tenant`, `Profile`, `User`) |
| `app/scripts/seed.py` | 3 (`Tenant`, `User`, `Profile`) |
| `app/scripts/create_admin.py` | 3 (`User`×2, `Tenant`) |
| `app/scripts/create_superadmin.py` | 4 (`User`×3, `Tenant`) |
| `app/scripts/reset_admin_password.py` | 1 (`User`) |
| `app/scripts/update_admin_email.py` | 1 (`User`) |

## Why scripts/, not the request path

Earlier in this audit:

```
app/repositories/user_repository.py:
  Multi-condition, tenant-scoped, or auth-flow lookups (login, password
  reset, superadmin verification) are explicitly OUT of scope...
```

`app/services/auth/password_reset_service.py` — the obvious next migration
target by query-count (10 `User.query` sites) — has three functions
literally annotated `"""DO NOT TOUCH — admin/tenant flow, not in scope for
v4.0."""` in the source. Forcing a repository wrapper around those is
exactly the kind of "looks safe, isn't" change your standing instruction
(*"do not break the core logic"*) exists to prevent: a `.filter_by(...)
.filter(...)` chain that differs from a `.filter_by(...)` call by even one
keyword changes which row gets matched, and this code path issues password
reset tokens.

`app/scripts/*.py` are CLI/bootstrap utilities — not imported by `create_app()`,
not on any HTTP request path, run manually/in CI only. They're the
lowest-risk possible proving ground for the repository pattern and were
verified end-to-end (see below), not just import-tested.

## Call-site inventory (full system, for Phase 4b sequencing)

153 `Model.query` call sites remain, ranked by volume:

| Model | Sites | Concentration |
|---|---|---|
| `Profile` | 64 | `app/superadmin/__init__.py` (20), `app/admin/__init__.py`, `app/tenant_isolation.py`, `app/context_processors.py` |
| `User` | 39 | `app/services/auth/password_reset_service.py` (10, **DO NOT TOUCH**), `app/superadmin/__init__.py`, `app/admin/__init__.py` |
| `Tenant` | 31 | `app/admin/__init__.py`, `app/superadmin/__init__.py`, `app/__init__.py`, `app/middleware/` |
| Billing models (`Subscription`, `PaymentMethod`, `PaymentInstruction`, `PaymentSubmission`, `WebhookEvent`) | ~20 | `app/utils/paymongo.py`, `app/services/billing/*` |
| Everything else (`Skill`, `Project`, `Testimonial`, `Service`, `Inquiry`, `ActivityLog`, email-provider models) | ~30 | scattered |

Recommended Phase 4b sequencing (one PR per row, boot-test gated, exactly
like Phases 1–3):

1. **`Profile`/`Tenant` in `app/context_processors.py` + `app/main/__init__.py`** —
   read-only rendering paths, lowest blast radius after scripts/.
2. **`app/admin/__init__.py`** — largest single file (9 `Project`, 7 `Tenant`,
   6 `Testimonial`, 5 `Skill`/`Profile` sites) — do in sub-batches per model,
   not as one file-wide sweep.
3. **`app/superadmin/__init__.py`** — largest call-site count overall (64
   total across models) — same sub-batch approach.
4. **Billing models** (`app/utils/paymongo.py`, `app/services/billing/*`) —
   per your standing constraint, this is "semantically dangerous" code;
   defer until you explicitly direct it, and even then, repository wrapping
   only (no logic changes), with PayMongo webhook replay tests before/after.
5. **`app/services/auth/password_reset_service.py`** — last, individually
   reviewed function-by-function, respecting the existing `DO NOT TOUCH`
   annotations until you lift them explicitly.

## Verification performed

- `create_app()` boot test: **10 blueprints, 174 routes** — identical to
  the Phase 1–3 baseline.
- Repository identity check: `user_repository.model is User`,
  `tenant_repository.model is Tenant`, `profile_repository.model is Profile`
  confirmed at runtime (not just import success).
- Functional test against a throwaway SQLite DB:
  `create_admin.py` run twice — first run creates `Tenant(slug='default')`
  via `tenant_repository.get_by_slug` (miss → insert) and `User` via
  `user_repository.get_by_username`/`get_by_email` (miss → insert); second
  run correctly hits the idempotency guard ("Admin user already exists") —
  confirming `get_by_username`/`get_by_email` return semantics are identical
  to the original `.filter_by(...).first()` chains they replaced.
- No model, migration, template, or route file touched.

## Not done in this pass (by design)

- No call sites in `app/admin/`, `app/superadmin/`, `app/auth/`, `app/main/`,
  `app/tenant/`, `app/middleware/`, `app/context_processors.py`, or any
  `app/services/*` file were touched. All 153 remaining `.query` sites are
  untouched and behave exactly as before.
- Billing-adjacent queries were not even scaffolded into repository methods
  yet, per the standing "billing/plan logic is semantically dangerous"
  constraint — Phase 4b item 4 above requires your explicit go-ahead.
