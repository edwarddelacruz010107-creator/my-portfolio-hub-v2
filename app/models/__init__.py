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
    TenantCustomDomain,
    User,
    PendingSignup,
    Subscription,
    WebhookEvent,
    PaymentMethod,
    PaymentInstruction,
    PaymentSubmission,
    PlatformSetting,
    TenantCommunicationSettings,
    PasswordResetOTP,
    GlobalEmailConfig,
    # v5.9 — Tenant Email Services
    TenantEmailProvider,
    TenantSmtpSettings,
    TenantResendSettings,
    TenantMailerSendSettings,
    Inquiry,
    InquiryReply,
    SubscriptionNotification,
    ActivityLog,
    # v6.4 — Theme Catalog (SuperAdmin Theme CRUD)
    ThemeCatalogEntry,
    VALID_REQUIRED_PLANS,
    # v6.6 — Discount & Promotion Manager
    DiscountCampaign,
    DiscountRedemption,
    # v7.7 — Invoice accounting record
    Invoice,
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
    ProjectReaction,
    Testimonial,
    Service,
    Certificate,
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
    'Tenant', 'TenantCustomDomain', 'User', 'PendingSignup', 'Subscription', 'WebhookEvent',
    'PaymentMethod', 'PaymentInstruction', 'PaymentSubmission',
    'PlatformSetting', 'TenantCommunicationSettings',
    'PasswordResetOTP', 'GlobalEmailConfig',
    'Inquiry', 'InquiryReply', 'SubscriptionNotification', 'ActivityLog',
    'TenantEmailProvider', 'TenantSmtpSettings', 'TenantResendSettings', 'TenantMailerSendSettings',
    'ThemeCatalogEntry', 'VALID_REQUIRED_PLANS',
    'DiscountCampaign', 'DiscountRedemption', 'Invoice',
    # tenant_data_db
    'Profile', 'Skill', 'Project', 'ProjectReaction', 'Testimonial', 'Service', 'Certificate', 'TenantFormSettings',
    # helpers
    'encrypt_secret', 'decrypt_secret',
    'normalize_plan_name', 'get_plan_features',
    'PLAN_FEATURES', 'PAID_PLAN_NAMES',
    # form settings
    'TenantFormSettings', 'VALID_PROVIDERS', 'BASIN_PREFIX', 'WEB3FORMS_URL',
]
