"""
PATCH: app/__init__.py — Changes required for dual-DB

Apply the following changes to your existing app/__init__.py.
This is a targeted diff, not a full file replacement.
"""

# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 1: Import from new model locations (replace old imports)
# ─────────────────────────────────────────────────────────────────────────────

# OLD (remove these):
# from app.models.portfolio import (Tenant, Profile, Skill, Project, ...)
# from app.models.user import User
# from app.models.tenant_form_settings import TenantFormSettings

# NEW (replace with):
from app.models import (
    # core_db
    Tenant, User, Subscription, WebhookEvent,
    PaymentMethod, PaymentInstruction, PaymentSubmission,
    PlatformSetting, TenantCommunicationSettings,
    PasswordResetOTP, GlobalEmailConfig,
    Inquiry, InquiryReply, SubscriptionNotification, ActivityLog,
    # tenant_data_db
    Profile, Skill, Project, Testimonial, Service, TenantFormSettings,
)


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 2: db.create_all() — must specify bind_key for tenant tables
# ─────────────────────────────────────────────────────────────────────────────

# In your create_app() factory, replace any bare db.create_all() with:

def _create_all_tables(app):
    """Create tables in both databases."""
    with app.app_context():
        # Core DB (default bind)
        db.create_all(bind_key=None)
        # Tenant Data DB
        db.create_all(bind_key='tenant')


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 3: Flask-Migrate multi-DB setup
# ─────────────────────────────────────────────────────────────────────────────

# Option A: Use separate Alembic directories (recommended — see migrations/)
#   Run: cd migrations/core && alembic upgrade head
#   Run: cd migrations/tenant && alembic upgrade head

# Option B: Single Flask-Migrate instance with include_schemas
# If you use flask-migrate (Migrate(app, db)), add this to your factory:
#
#   from flask_migrate import Migrate
#   migrate = Migrate(app, db, include_name=lambda name, type_, parent_names: (
#       # Include all tables — Flask-SQLAlchemy handles bind routing
#       True
#   ))


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 4: Tenant isolation middleware / decorator
# ─────────────────────────────────────────────────────────────────────────────

# All queries against tenant_data_db models MUST filter by tenant_id.
# Replace any query that used tenant_slug isolation with tenant_id isolation:

# OLD pattern:
#   Profile.query.filter_by(tenant_slug=g.tenant_slug).first()
#   Project.query.filter_by(tenant_slug=g.tenant_slug).all()

# NEW pattern (use tenant_id from session — set at login):
#   Profile.query.filter_by(tenant_id=session['tenant_id']).first()
#   Project.query.filter_by(tenant_id=session['tenant_id']).all()

# The session key 'tenant_id' must be set in your login route:
#   session['tenant_id'] = user.tenant_id
#   session['tenant_slug'] = user.tenant_slug  # keep for URL routing


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 5: Cross-DB tenant resolution helper
# ─────────────────────────────────────────────────────────────────────────────

# In blueprints that need to look up a Tenant from its slug (e.g. public
# portfolio routes), query core_db. Profile is in tenant_data_db.
# You cannot JOIN across DBs with SQLAlchemy — do two separate queries:

def get_profile_for_slug(tenant_slug: str):
    """
    Resolve tenant_slug → Profile via two DB queries.
    Use this wherever you previously did:
        Profile.query.filter_by(tenant_slug=slug).first()
    """
    from app.models import Tenant, Profile
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    if not tenant:
        return None, None
    profile = Profile.query.filter_by(tenant_id=tenant.id).first()
    return tenant, profile


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 6: ActivityLog — add tenant_id column
# ─────────────────────────────────────────────────────────────────────────────

# ActivityLog now carries a tenant_id FK to tenants (both in core_db).
# Your existing log_activity() helper needs updating:

def log_activity(
    action: str,
    entity_type: str = None,
    entity_name: str = None,
    description: str = None,
    user=None,
    tenant_id: int = None,
    tenant_slug: str = None,
):
    """Drop-in replacement for the old log_activity helper."""
    from flask import request, session
    from app.models import ActivityLog
    from app import db

    entry = ActivityLog(
        tenant_id   = tenant_id or session.get('tenant_id'),
        tenant_slug = tenant_slug or session.get('tenant_slug', ''),
        user_id     = user.id if user else None,
        username    = user.username if user else None,
        action      = action,
        entity_type = entity_type,
        entity_name = entity_name,
        description = description,
        ip_address  = request.remote_addr if request else None,
    )
    db.session.add(entry)
    # Caller is responsible for db.session.commit()
    return entry
