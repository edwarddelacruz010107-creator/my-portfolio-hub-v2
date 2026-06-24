"""
app/models/portfolio.py — Compatibility shim (v5.1)

ARCHITECTURE NOTE
─────────────────
This file is intentionally a re-export shim.

All ORM model classes were split into canonical homes:
  • app/models/core.py        → core_db models (SQLALCHEMY_DATABASE_URI)
  • app/models/tenant_data.py → tenant_data_db models (__bind_key__ = "tenant")

This shim exists so every existing import of the form:

    from app.models.portfolio import Tenant
    from app.models.portfolio import Profile
    from app.models.portfolio import normalize_plan_name

continues to work WITHOUT modification.  No model class is declared here;
doing so would register a duplicate SQLAlchemy Table on the shared MetaData
instance and raise:

    sqlalchemy.exc.InvalidRequestError:
        Table 'tenants' is already defined for this MetaData instance.

DO NOT add any `class Foo(db.Model)` declarations here.
DO NOT add any `__tablename__` strings here.
Canonical owners:
  • Tenant, Subscription, WebhookEvent, PaymentMethod, PaymentInstruction,
    PaymentSubmission, PlatformSetting, TenantCommunicationSettings,
    PasswordResetOTP, GlobalEmailConfig, Inquiry, InquiryReply,
    SubscriptionNotification, ActivityLog
    → app/models/core.py

  • Profile, Skill, Project, Testimonial, Service, TenantFormSettings
    → app/models/tenant_data.py
"""

# ── Core DB models ────────────────────────────────────────────────────────────
from app.models.core import (
    # constants / helpers
    _utcnow,
    SUBSCRIPTION_PLAN_ORDER,
    PAID_PLAN_NAMES,
    PLAN_FEATURES,
    PAYMENT_METHOD_TYPES,
    PAYMENT_METHOD_ICONS,
    normalize_plan_name,
    get_plan_features,
    encrypt_secret,
    decrypt_secret,

    # ORM models
    Tenant,
    User,
    Subscription,
    WebhookEvent,
    PaymentMethod,
    PaymentInstruction,
    PaymentSubmission,
    PlatformSetting,
    TenantCommunicationSettings,
    PasswordResetOTP,
    GlobalEmailConfig,
    Inquiry,
    InquiryReply,
    SubscriptionNotification,
    ActivityLog,
)

# ── Tenant-data DB models (bind_key = "tenant") ───────────────────────────────
from app.models.tenant_data import (
    Profile,
    Skill,
    Project,
    Testimonial,
    Service,
)

# ── Form settings (core_db canonical source) ──────────────────────────────────
from app.models.tenant_form_settings import (
    TenantFormSettings,
    VALID_PROVIDERS,
    BASIN_PREFIX,
    WEB3FORMS_URL,
)


# ── SubscriptionStatus compatibility namespace ────────────────────────────────
# Several service files import `SubscriptionStatus` from this module.
# The ORM `Subscription` uses plain string status values; this namespace
# provides the same string constants so existing call-sites work unchanged:
#
#   sub.status = SubscriptionStatus.ACTIVE   →  sub.status = 'active'
#
class SubscriptionStatus:
    """String-constant namespace for subscription status values."""
    PENDING   = 'pending'
    ACTIVE    = 'active'
    FAILED    = 'failed'
    CANCELLED = 'cancelled'
    EXPIRED   = 'expired'


# ── Public API ────────────────────────────────────────────────────────────────
__all__ = [
    # helpers / constants
    '_utcnow',
    'SUBSCRIPTION_PLAN_ORDER',
    'PAID_PLAN_NAMES',
    'PLAN_FEATURES',
    'PAYMENT_METHOD_TYPES',
    'PAYMENT_METHOD_ICONS',
    'normalize_plan_name',
    'get_plan_features',
    'encrypt_secret',
    'decrypt_secret',

    # core_db ORM models
    'Tenant',
    'User',
    'Subscription',
    'SubscriptionStatus',
    'WebhookEvent',
    'PaymentMethod',
    'PaymentInstruction',
    'PaymentSubmission',
    'PlatformSetting',
    'TenantCommunicationSettings',
    'PasswordResetOTP',
    'GlobalEmailConfig',
    'Inquiry',
    'InquiryReply',
    'SubscriptionNotification',
    'ActivityLog',

    # tenant_data_db ORM models
    'Profile',
    'Skill',
    'Project',
    'Testimonial',
    'Service',
    'TenantFormSettings',
]
