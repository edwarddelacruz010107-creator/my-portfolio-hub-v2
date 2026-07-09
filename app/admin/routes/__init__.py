"""
app/admin/routes/__init__.py — imports every route module so each module's
@admin.route(...) decorators register against the shared blueprint object
on import.
"""
from app.admin.routes import core_auth
from app.admin.routes import billing
from app.admin.routes import messaging
from app.admin.routes import profile_appearance
from app.admin.routes import skills
from app.admin.routes import projects_uploads
from app.admin.routes import testimonials
from app.admin.routes import certificates
from app.admin.routes import services
from app.admin.routes import experiences
from app.admin.routes import settings_2fa
from app.admin.routes import notifications_email
from app.admin.routes import custom_domains

__all__ = [
    "core_auth", "billing", "messaging", "profile_appearance", "skills",
    "projects_uploads", "testimonials", "certificates", "services", "experiences", "settings_2fa",
    "notifications_email", "custom_domains",
]
