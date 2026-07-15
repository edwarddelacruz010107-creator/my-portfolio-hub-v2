"""
app/superadmin/routes/__init__.py — imports every route module so each
module's @superadmin.route(...) decorators register against the shared
blueprint object on import. Order doesn't affect route registration
(each module is independent), but core_auth is imported first for
readability since it owns the blueprint's primary entry points.
"""
from app.superadmin.routes import core_auth
from app.superadmin.routes import tenants
from app.superadmin.routes import messaging
from app.superadmin.routes import billing
from app.superadmin.routes import media
from app.superadmin.routes import email_settings
from app.superadmin.routes import landing_settings
from app.superadmin.routes import pricing_settings
from app.superadmin.routes import subscriptions
from app.superadmin.routes import twofa
from app.superadmin.routes import logs_monitor
from app.superadmin.routes import impersonation
from app.superadmin.routes import discounts
from app.superadmin.routes import design_system
from app.superadmin.routes import notifications
from app.superadmin.routes import ai_center

__all__ = [
    "core_auth", "tenants", "messaging", "billing", "media",
    "email_settings", "landing_settings", "pricing_settings", "subscriptions", "twofa", "logs_monitor", "impersonation",
    "discounts", "design_system", "notifications", "ai_center",
]
