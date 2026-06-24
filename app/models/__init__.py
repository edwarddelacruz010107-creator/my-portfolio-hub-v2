"""
app/models/__init__.py — Dual-DB model registry

core_db   (SQLALCHEMY_DATABASE_URI / default bind):
    Tenant, User, Subscription, WebhookEvent,
    PaymentMethod, PaymentInstruction, PaymentSubmission,
    PlatformSetting, TenantCommunicationSettings,
    PasswordResetOTP, GlobalEmailConfig,
    Inquiry, InquiryReply, SubscriptionNotification, ActivityLog

tenant_data_db (__bind_key__ = "tenant" / TENANT_DATABASE_URL):
    Profile, Skill, Project, Testimonial, Service, TenantFormSettings
"""

# ── Core DB models ─────────────────────────────────────────────────────────
from app.models.core import (
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
    # Crypto helpers (consumed by services)
    encrypt_secret,
    decrypt_secret,
    normalize_plan_name,
    get_plan_features,
    PLAN_FEATURES,
    PAID_PLAN_NAMES,
)

# ── Tenant Data DB models ───────────────────────────────────────────────────
from app.models.tenant_data import (
    Profile,
    Skill,
    Project,
    Testimonial,
    Service,
)

# ── Form settings (core_db — canonical owner: app/models/tenant_form_settings.py) ──
from app.models.tenant_form_settings import (
    TenantFormSettings,
    VALID_PROVIDERS,
    BASIN_PREFIX,
    WEB3FORMS_URL,
)

__all__ = [
    # core_db
    'Tenant', 'User', 'Subscription', 'WebhookEvent',
    'PaymentMethod', 'PaymentInstruction', 'PaymentSubmission',
    'PlatformSetting', 'TenantCommunicationSettings',
    'PasswordResetOTP', 'GlobalEmailConfig',
    'Inquiry', 'InquiryReply', 'SubscriptionNotification', 'ActivityLog',
    # tenant_data_db
    'Profile', 'Skill', 'Project', 'Testimonial', 'Service', 'TenantFormSettings',
    # helpers
    'encrypt_secret', 'decrypt_secret',
    'normalize_plan_name', 'get_plan_features',
    'PLAN_FEATURES', 'PAID_PLAN_NAMES',
    # form settings
    'TenantFormSettings', 'VALID_PROVIDERS', 'BASIN_PREFIX', 'WEB3FORMS_URL',
]
