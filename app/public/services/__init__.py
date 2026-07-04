"""
app/public/services/ — Read-only, cross-tenant query layer for the public
SaaS surface (landing page, /explore, /feed).

ARCHITECTURAL CONSTRAINT (see AUDIT_REPORT.md §2):
    Tenant (core_db) and Profile/Project (tenant_db, __bind_key__='tenant')
    are separate SQLAlchemy binds. There is NO cross-DB SQL JOIN available.
    Every function in this package that needs both "is this tenant active"
    (core_db) and "show me their profile/projects" (tenant_db) does TWO
    queries and stitches the result in Python — never assume a join works.

SECURITY CONSTRAINT:
    These functions are the ONLY sanctioned way to read tenant_db rows
    outside of a tenant-scoped request (i.e. without g.tenant_slug /
    _require_tenant_object() isolation already applied). Every dict
    returned to a template MUST go through an explicit field allowlist
    (see serializers.py) — never pass a raw model instance from here
    into a public-facing template. Raw instances carry email, phone,
    monthly_rate, internal_notes, free_trial_ends, etc.
"""
