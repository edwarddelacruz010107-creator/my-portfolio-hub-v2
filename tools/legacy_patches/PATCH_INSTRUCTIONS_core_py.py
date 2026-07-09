"""
PATCH INSTRUCTIONS FOR app/models/core.py — v6.0 Enterprise Refactor

Apply these changes to the existing core.py file.
Each section shows WHAT TO REPLACE and WITH WHAT.

════════════════════════════════════════════════════════════════
CHANGE 1: Replace PLAN_FEATURES / normalize_plan_name / get_plan_features
          with a lightweight shim that delegates to plan_capabilities.py
════════════════════════════════════════════════════════════════

FIND (lines ~43–99):

    SUBSCRIPTION_PLAN_ORDER = {'Basic': 1, 'Pro': 2, 'Enterprise': 3}

    _PLAN_ALIASES = {
        'basic': 'Basic',
        ...
    }

    PAID_PLAN_NAMES = frozenset({'Basic', 'Pro', 'Enterprise'})

    PLAN_FEATURES = {
        'Basic': { ... },
        'Pro':   { ... },
        ...
    }

    def normalize_plan_name(plan: str) -> str:
        ...

    def get_plan_features(plan: str) -> dict:
        ...


REPLACE WITH:

    SUBSCRIPTION_PLAN_ORDER = {'Trial': 0, 'Basic': 1, 'Pro': 2, 'Enterprise': 3}

    PAID_PLAN_NAMES = frozenset({'Basic', 'Pro', 'Enterprise'})

    def normalize_plan_name(plan: str) -> str:
        # Preserved for backward-compat imports; delegates to plan_capabilities
        from app.services.plan_capabilities import get_capabilities
        cap = get_capabilities(plan)
        return cap.plan_name

    def get_plan_features(plan: str) -> dict:
        # Preserved for backward-compat; returns capability dict
        from app.services.plan_capabilities import get_capabilities
        return get_capabilities(plan).as_dict()

    # Backward-compat alias
    PLAN_FEATURES = property(lambda _: {
        name: get_plan_features(name)
        for name in ('Trial', 'Basic', 'Pro', 'Enterprise')
    })


════════════════════════════════════════════════════════════════
CHANGE 2: Add storage / subscription state columns to Tenant model
════════════════════════════════════════════════════════════════

IN class Tenant(db.Model):, AFTER the line:
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

ADD:

    # v6.0 — Storage quota tracking
    storage_used_bytes   = db.Column(db.BigInteger, nullable=False, default=0)
    storage_limit_bytes  = db.Column(db.BigInteger, nullable=True)

    # v6.0 — Subscription lifecycle state machine
    # Values: 'trial' | 'active' | 'grace' | 'readonly' | 'suspended'
    subscription_state   = db.Column(db.String(30), nullable=False, default='active')
    grace_period_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)


════════════════════════════════════════════════════════════════
CHANGE 3: Update Tenant.plan_features() and add capability helpers
════════════════════════════════════════════════════════════════

FIND in class Tenant:
    def plan_features(self) -> dict:
        return get_plan_features(self.effective_plan())

REPLACE WITH:
    def plan_features(self) -> dict:
        from app.services.plan_capabilities import get_tenant_capabilities
        return get_tenant_capabilities(self).as_dict()

    def get_capabilities(self):
        \"\"\"Return PlanCapability instance for this tenant's effective plan.\"\"\"
        from app.services.plan_capabilities import get_tenant_capabilities
        return get_tenant_capabilities(self)

    def storage_pct(self) -> float:
        \"\"\"Storage usage as 0–100 percentage.\"\"\"
        caps = self.get_capabilities()
        return caps.storage_usage_pct(self.storage_used_bytes or 0)

    def storage_near_limit(self) -> bool:
        \"\"\"True when >= 90% of quota is used.\"\"\"
        caps = self.get_capabilities()
        return caps.storage_warning(self.storage_used_bytes or 0)


════════════════════════════════════════════════════════════════
CHANGE 4: Add MediaUpload and PlanUsageLog to models __init__.py
════════════════════════════════════════════════════════════════

In app/models/__init__.py, ADD:
    from app.models.core_additions import MediaUpload, PlanUsageLog


════════════════════════════════════════════════════════════════
CHANGE 5: Register subscription guard in app/__init__.py
════════════════════════════════════════════════════════════════

In app/__init__.py, AFTER blueprint registrations, ADD:

    # v6.0 — Subscription lifecycle enforcement
    from app.middleware.subscription_guard import init_subscription_guard
    init_subscription_guard(app)


════════════════════════════════════════════════════════════════
CHANGE 6: Expose quota summary in admin context processor
════════════════════════════════════════════════════════════════

In app/context_processors.py, ADD to the admin context dict:

    # Storage quota for admin sidebar widget
    'quota_summary': _get_quota_summary(),

And define:

    def _get_quota_summary():
        try:
            from flask_login import current_user
            from app.models.core import Tenant
            from app.services.storage_service import get_quota_summary
            if current_user and current_user.is_authenticated and not current_user.is_superadmin:
                t = Tenant.query.get(current_user.tenant_id)
                if t:
                    return get_quota_summary(t)
        except Exception:
            pass
        return None
"""
