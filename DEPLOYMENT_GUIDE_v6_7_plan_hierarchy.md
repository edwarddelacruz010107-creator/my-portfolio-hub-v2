# Portfolio Hub v6.7 — Plan Hierarchy Refactor Deployment Guide

## Root Cause Analysis

Three distinct bugs caused Administrator tenants to see locked themes, PRO Required
notices, and upgrade CTAs:

### Bug 1 — Profile.is_administrator did not exist (CRITICAL)

`ThemeEngine.resolve_theme()` and `ThemeEngine.can_use_theme()` both called:

```python
if getattr(tenant_profile, 'is_administrator', False):
    return requested  # bypass plan check
```

But `Profile` (in `app/models/tenant_data.py`) had **no `is_administrator` attribute**.
`getattr(..., False)` silently returned `False`, so the bypass never fired.

**Fix:** Added `@property is_administrator` to `Profile` that delegates to
`plan_hierarchy.is_administrator(self.effective_plan())`.

---

### Bug 2 — ThemeEngine._PLAN_RANK missing 'administrator' key (CRITICAL)

The local plan rank dict in `ThemeEngine`:

```python
_PLAN_RANK = {'free': 0, 'basic': 0, 'trial': 0, 'pro': 1, 'premium': 1, 'enterprise': 2, 'agency': 2}
```

Had **no 'administrator' key**. When `_plan_meets_requirement('Administrator', 'pro')` was
called, `_PLAN_RANK.get('administrator', 0)` returned `0` (Free tier rank), so the check
`have >= need` became `0 >= 1` → `False` → theme blocked.

**Fix:** Removed `_PLAN_RANK` entirely. `_plan_meets_requirement()` now delegates to
`plan_hierarchy.has_plan_access()` where Administrator has rank 999.

---

### Bug 3 — Hardcoded plan name lists in ThemeEngine (MEDIUM)

```python
if str(plan).lower() in ('pro', 'premium', 'enterprise', 'agency'):
    return requested
```

'administrator' was not in this list. Any Administrator tenant whose theme didn't
have an explicit `required_plan` field in its metadata would be blocked at this gate.

**Fix:** Replaced all such lists with `ThemeAccessService.can_access_theme()` which
routes through the canonical hierarchy.

---

## Files Delivered

```
app/services/plans/                         ← NEW package
    __init__.py                             Single import surface
    plan_hierarchy.py                       Canonical rank table + core helpers
    plan_resolver.py                        Translates any object → plan decision
    entitlement_service.py                  Feature gate registry + context builder
    theme_access_service.py                 All theme entitlement logic
    feature_gate_service.py                 Route decorators (require_plan etc.)
    quota_service.py                        All quota checks + bypass

app/theme_engine.py                         PATCHED — delegates to ThemeAccessService
app/models/tenant_data.py                   PATCHED — adds Profile.is_administrator
app/services/permissions/__init__.py        PATCHED — re-exports from plans + compat shims
app/static/admin/css/administrator-plan.css NEW — gold/crown visual design
app/templates/admin/themes/_theme_card.html NEW — theme card macro with admin state
app/templates/admin/billing/plans_v6_administrator_patch.html  NEW — billing section patch
PATCH_admin_themes.py                       PATCH instructions for admin/__init__.py
```

---

## Apply Instructions (Windows PowerShell)

### Step 1 — Copy new files into project

```powershell
$SRC = "path\to\this\output"
$DST = "path\to\my-portfolio-hub-v6_6-email-fixed"

# Create plans package directory
New-Item -ItemType Directory -Force "$DST\app\services\plans"

# Copy new plans package
Copy-Item "$SRC\app\services\plans\*" "$DST\app\services\plans\" -Recurse -Force

# Copy patched files
Copy-Item "$SRC\app\theme_engine.py"  "$DST\app\theme_engine.py"  -Force
Copy-Item "$SRC\app\models\tenant_data.py" "$DST\app\models\tenant_data.py" -Force
Copy-Item "$SRC\app\services\permissions\__init__.py" "$DST\app\services\permissions\__init__.py" -Force

# CSS
New-Item -ItemType Directory -Force "$DST\app\static\admin\css"
Copy-Item "$SRC\app\static\admin\css\administrator-plan.css" "$DST\app\static\admin\css\" -Force

# Templates
New-Item -ItemType Directory -Force "$DST\app\templates\admin\themes"
Copy-Item "$SRC\app\templates\admin\themes\_theme_card.html" "$DST\app\templates\admin\themes\" -Force
Copy-Item "$SRC\app\templates\admin\billing\plans_v6_administrator_patch.html" "$DST\app\templates\admin\billing\" -Force
```

### Step 2 — Apply admin/__init__.py themes_index patch

Open `app/admin/__init__.py` and make these changes:

**A. Add import near the top (after existing imports):**
```python
from app.services.plans import ThemeAccessService, EntitlementService
```

**B. Replace `themes_index()` body** with the version in `PATCH_admin_themes.py`.

**C. Replace `apply_theme()` body** with the version in `PATCH_admin_themes.py`.

### Step 3 — Add CSS to admin base template

In `app/templates/admin/base.html`, add:
```html
<link rel="stylesheet" href="{{ url_for('static', filename='admin/css/administrator-plan.css') }}">
```

Add to the `<body>` tag:
```html
<body class="{% if is_administrator_tenant %}admin-context{% endif %} ...">
```

### Step 4 — Apply billing template patch

In `app/templates/admin/billing/plans_v6.html`, replace the existing
`{% if is_administrator_tenant %}` block (lines ~22-44) with the content
from `plans_v6_administrator_patch.html`.

### Step 5 — No migration needed

No database schema changes. No Alembic migration required.

---

## Verification

After applying, verify with an Administrator-plan tenant:

1. **Themes page** (`/admin/appearance/themes`):
   - All theme cards show "ADMIN ACCESS" badge (gold)
   - No lock icons visible
   - No "Upgrade to PRO" buttons
   - All "Apply Theme" buttons are enabled

2. **Billing page** (`/admin/billing/plans`):
   - Shows gold Administrator banner
   - No plan comparison table
   - No payment methods section
   - No checkout buttons

3. **Feature gates**:
   - Resend/MailerSend configuration pages accessible
   - Upload of any file size accepted
   - No page/project limit warnings

4. **Security**:
   - Regular tenant cannot reach Administrator plan via form POST
   - `validate_plan_change()` blocks downgrade of Administrator
   - `can_assign_administrator()` blocks non-superadmin assignment

---

## Architecture Summary

```
Request
  │
  ▼
Route Handler
  │  uses
  ├─► require_plan('Pro')               → feature_gate_service.py
  │       │ calls
  │       └─► EntitlementService.check()  → entitlement_service.py
  │               │ calls
  │               └─► has_plan_access()   → plan_hierarchy.py ← SINGLE SOURCE
  │
  ├─► ThemeEngine.can_use_theme()       → theme_engine.py (patched)
  │       │ delegates to
  │       └─► ThemeAccessService        → theme_access_service.py
  │               │ calls
  │               └─► has_plan_access() → plan_hierarchy.py ← SINGLE SOURCE
  │
  └─► QuotaService.can_upload()         → quota_service.py
          │ calls
          └─► is_administrator()        → plan_hierarchy.py ← SINGLE SOURCE
```

All plan comparisons flow through `plan_hierarchy.has_plan_access()`.
Administrator (rank 999) always passes any `required_plan` check.
No plan strings are ever compared with `==` outside of `plan_hierarchy.py`.

---

## Security Notes

- Administrator plan assignment is guarded by `validate_plan_change()` in
  `tenant_access.py` — only superadmins or the system startup hook may assign it.
- The `PLAN_ADMINISTRATOR` constant (`'Administrator'`) is never exposed in
  public API responses or billing plan lists (`all_plans_summary()` excludes it).
- Billing webhook handlers check `is_administrator_plan()` and return early —
  no subscription record can override an Administrator tenant's plan.
- Frontend receives entitlement flags from the backend (e.g. `can_access: true`)
  and never recomputes permissions independently.
