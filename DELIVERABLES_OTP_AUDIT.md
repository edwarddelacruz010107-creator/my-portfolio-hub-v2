# Portfolio CMS v5.6 — MailerSend OTP Audit & Fix
## Deliverables

---

## 1. Root Cause Analysis

**The OTP/email code itself is correct.** Routes, templates, the
`password_reset_service.py` three-flow design (superadmin / admin / tenant),
and `mailersend_service.py`'s per-portal key resolution are all sound and
structurally identical across portals.

**The actual root cause is a broken Alembic migration chain**, the same bug
class your earlier v5.3 audit fixed once already — reintroduced in two files:

| File | Problem |
|---|---|
| `migrations/versions/0029_merge_heads.py` | `down_revision = None`, despite a docstring and inline comment both claiming it merges two parent heads. A merge revision with `down_revision = None` is treated by Alembic as a brand-new root, not a merge. |
| `migrations/versions/v5_6_per_portal_email_config.py` (new in v5.6) | Also `down_revision = None` — a second new root, on top of the first. |

**Effect:** 4 independent Alembic heads instead of 1; 3 roots instead of 1.
`flask db upgrade` — the first command in Render's `preDeployCommand` — hard-fails
with *"Multiple head revisions are present."* Every subsequent pre-deploy
step (`ensure-tenant-schema`, `ensure-default-tenant`, `create-superadmin`)
is skipped too, since they're chained with `&&`.

**Why this breaks OTP specifically:** the v5.6 migration is the one that adds
`admin_mailersend_api_key`, `superadmin_mailersend_api_key`, and the
per-portal sender columns to `global_email_config`. Because it never ran in
production, that table is missing those columns. `GlobalEmailConfig.get()`
does a full-row ORM load (`db.session.get(cls, 1)`), so **any** access to the
config — not just the admin/superadmin-specific properties — raises a DB
error on a table that doesn't have all its mapped columns. That poisons the
SQLAlchemy session before `create_otp_record()` / `send_otp_email()` ever
run, so the failure is silent from the user's perspective (generic
flashed message, no email, often a 500).

**Why Tenant "worked" and Admin/Superadmin didn't:** the **local dev SQLite
DB already had the new columns** (created via `db.create_all()`, which
doesn't consult Alembic's revision graph at all). That made every flow
*appear* fine in dev. In actual production, the table is genuinely missing
the columns, so all three flows — and the contact form's `email_only`
provider, and the superadmin "send test email" feature — are equally
exposed; Admin/Superadmin Forgot Password are simply the two surfaces this
was reported against.

**Corroborating evidence:** `patch_v5_6.py`, a standalone manual
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` script in the project root,
exists specifically to patch this same table by hand against
`CORE_DATABASE_URL` — strong evidence someone already hit this exact wall
once and worked around it with a manual script instead of fixing the
underlying chain.

---

## 2. Files Modified

| File | Change |
|---|---|
| `migrations/versions/0029_merge_heads.py` | `down_revision = None` → `down_revision = ('0011_add_paymongo_subscription', '0028_add_email_only_provider')` |
| `migrations/versions/v5_6_per_portal_email_config.py` | `down_revision = None` → `down_revision = '0029_merge_heads'`; updated stale docstring |
| `tests/test_migration_chain_integrity.py` | **New file.** Lightweight regression guard (no live DB required) that fails if the chain ever regresses to multiple heads/roots, or if these two specific revisions lose their correct wiring. |

No other files were touched. No business logic, billing, auth flow,
tenant rendering, or CMS content code was modified.

---

## 3. Exact Code Changes

**`0029_merge_heads.py`:**
```diff
 revision = '0029_merge_heads'
-down_revision = None  # FIXED: was '0011_add_paymongo_subscription' (orphaned branch head; use both parents)
+down_revision = ('0011_add_paymongo_subscription', '0028_add_email_only_provider')
 branch_labels = None
 depends_on = None
```

**`v5_6_per_portal_email_config.py`:**
```diff
 # Flask-Migrate / Alembic revision
 revision = 'v5_6_portal_email'
-down_revision = None   # set to previous revision ID if chaining
+down_revision = '0029_merge_heads'
 branch_labels = None
 depends_on = None
```

(Docstring/usage comment in the same file was also updated to stop telling
the next person to run this manually — it's now correctly part of the
normal `flask db upgrade` path.)

---

## 4. MailerSend Configuration Issues Discovered

- **None at the code level.** Key resolution priority (DB per-portal →
  DB shared → env per-portal → env shared) is implemented correctly in
  both `GlobalEmailConfig.get_portal_key()` and
  `mailersend_service._get_mailersend_key()`.
- **Operational gap (not a bug):** there is currently no superadmin UI for
  setting the per-portal (`admin_*` / `superadmin_*`) keys/senders — only
  the shared key/sender is editable via Settings → Email. This isn't a
  blocker (every portal falls back to the shared key), but worth knowing:
  today, all three portals will use the *same* MailerSend key/sender unless
  someone sets the per-portal DB columns directly or via the
  `ADMIN_MAILERSEND_API_KEY` / `SUPERADMIN_MAILERSEND_API_KEY` env vars.
  Flagged for your awareness; not fixed, since it's a new feature, not a
  regression.
- `MAILERSEND_REPLY_TO` (mentioned in the task spec) is not referenced
  anywhere in the codebase — nothing to fix, it's just unused.
- No SMTP fallback exists by design (`_smtp_fallback()` is an intentional
  no-op since Flask-Mail's removal in v5.0) — this is documented, expected
  behavior, not a bug.

---

## 5. OTP Workflow Issues Discovered

- **None**, beyond the migration chain. `PasswordResetOTP` storage,
  hashing, expiration (`is_expired`), attempt limiting (`MAX_ATTEMPTS`),
  and purge-on-retry logic in `otp_service.py` are all correct and were
  unaffected (that table was created in an earlier, already-applied
  migration — `0018_auth_otp_web3forms.py` — so it's not blocked by the
  broken chain).
- Logging in the OTP/reset/email path does not leak OTP values, passwords,
  or API keys (verified by inspection — no changes needed).

---

## 6. Test Results

### A. Migration chain topology (new automated test)
```
tests/test_migration_chain_integrity.py
  test_migration_chain_has_exactly_one_head .......... PASSED
  test_migration_chain_has_exactly_one_base .......... PASSED
  test_full_revision_path_is_walkable ................ PASSED
  test_merge_revision_has_tuple_down_revision ........ PASSED
  test_v5_6_portal_email_chains_onto_merge_head ...... PASSED
5 passed in 0.36s
```
Confirmed this test **fails 4/5 cases** when reverted to the original
broken `down_revision = None` state, proving it actually catches the
regression class rather than trivially passing.

### B. Independent verification via real Alembic internals
```
ScriptDirectory.get_heads() → ['v5_6_portal_email']   (was 4 heads)
ScriptDirectory.get_bases() → ['7d0f3492b2b3']         (was 3 bases)
walk_revisions(base='base', head='heads') → all 33 revisions, single
  connected path, correct (mergepoint) at 0029_merge_heads and
  (branchpoint) at 0007→0008.
```

### C. DDL correctness of the v5.6 migration itself
Directly executed `upgrade()` against a throwaway in-memory table shaped
like the real `global_email_config`: all 6 new columns
(`admin_mailersend_api_key`, `admin_sender_name`, `admin_sender_email`,
`superadmin_mailersend_api_key`, `superadmin_sender_name`,
`superadmin_sender_email`) were added correctly. `downgrade()` cleanly
removed all 6. Both match `GlobalEmailConfig`'s ORM column declarations
in `app/models/core.py` exactly.

### D. Tenant Admin / Admin Portal / Superadmin Portal flows
Not independently runnable end-to-end in this environment (no live
Postgres/Render access, and booting the full Flask app requires the
MailerSend SDK + Redis + other production-only dependencies). Verified
instead by full manual code trace: all three call sites
(`initiate_tenant_reset`, `initiate_admin_reset`, `initiate_superadmin_reset`)
follow the identical `_recovery_enabled()` → `create_otp_record()` →
`send_otp_email(portal=...)` path, and all three depend on the same
`global_email_config` table that this fix restores. Once `flask db upgrade`
succeeds in production (which the test above confirms it now will), all
three flows are unblocked identically — there's nothing portal-specific
left to verify in the OTP logic itself, since none of it changed.

**Recommended before/alongside deploy:** run `flask db upgrade` against a
staging Postgres copy (or the `patch_v5_6.py` script can now be safely
retired, since the real migration covers it) and confirm
`SELECT column_name FROM information_schema.columns WHERE table_name =
'global_email_config'` shows all 16 expected columns.

---

## 7. Security Concerns Identified

None new. Existing security posture (anti-enumeration generic messages,
OTP hashing, attempt limiting, session-token rotation on password change,
tenant isolation on the tenant flow) was all already correct and is
untouched by this fix.

One pre-existing, **unrelated**, out-of-scope item noticed during testing
(not fixed, per the "no unrelated refactoring" instruction): `0001_initial_schema.py`
contains SQLite-dialect-only issues (a duplicate `create_index` colliding
with an implicit unique index, and a constraint `ALTER` that needs
batch-mode on SQLite). These don't affect production (Postgres), only
local/test SQLite runs of the full chain from scratch. Flagging in case
you want it addressed in a future, separately-scoped pass.
