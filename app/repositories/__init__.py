"""
app/repositories/ — Repository layer (Phase 4)

Centralizes ORM access for the highest-frequency model lookups. See
PHASE4_AUDIT.md at the repo root for full scope, rationale, and the
call-site migration map for the deferred (Phase 4b) work.
"""
from app.repositories.base import BaseRepository
from app.repositories.profile_repository import ProfileRepository, profile_repository
from app.repositories.tenant_repository import TenantRepository, tenant_repository
from app.repositories.user_repository import UserRepository, user_repository
from app.repositories.project_repository import ProjectRepository, project_repository
from app.repositories.testimonial_repository import TestimonialRepository, testimonial_repository
from app.repositories.certificate_repository import CertificateRepository, certificate_repository
from app.repositories.skill_repository import SkillRepository, skill_repository
from app.repositories.service_repository import ServiceRepository, service_repository
from app.repositories.inquiry_repository import InquiryRepository, inquiry_repository
from app.repositories.activity_log_repository import ActivityLogRepository, activity_log_repository
from app.repositories.subscription_repository import SubscriptionRepository, subscription_repository
from app.repositories.payment_method_repository import PaymentMethodRepository, payment_method_repository
from app.repositories.payment_submission_repository import PaymentSubmissionRepository, payment_submission_repository
from app.repositories.subscription_notification_repository import SubscriptionNotificationRepository, subscription_notification_repository
from app.repositories.webhook_event_repository import WebhookEventRepository, webhook_event_repository
from app.repositories.global_email_config_repository import GlobalEmailConfigRepository, global_email_config_repository
from app.repositories.discount_repository import (
    DiscountCampaignRepository, discount_campaign_repository,
    DiscountRedemptionRepository, discount_redemption_repository,
)

__all__ = [
    "BaseRepository",
    "UserRepository",                     "user_repository",
    "TenantRepository",                   "tenant_repository",
    "ProfileRepository",                  "profile_repository",
    "ProjectRepository",                  "project_repository",
    "TestimonialRepository",              "testimonial_repository",
    "CertificateRepository",              "certificate_repository",
    "SkillRepository",                    "skill_repository",
    "ServiceRepository",                  "service_repository",
    "InquiryRepository",                  "inquiry_repository",
    "ActivityLogRepository",              "activity_log_repository",
    "SubscriptionRepository",             "subscription_repository",
    "PaymentMethodRepository",            "payment_method_repository",
    "PaymentSubmissionRepository",        "payment_submission_repository",
    "SubscriptionNotificationRepository", "subscription_notification_repository",
    "WebhookEventRepository",             "webhook_event_repository",
    "GlobalEmailConfigRepository",         "global_email_config_repository",
    "DiscountCampaignRepository",          "discount_campaign_repository",
    "DiscountRedemptionRepository",        "discount_redemption_repository",
]
