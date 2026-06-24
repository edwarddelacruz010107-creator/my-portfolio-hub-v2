"""
app/models/tenant_data.py — tenant_data_db models

All portfolio content owned by tenants.
Every model carries:
  __bind_key__ = "tenant"   → routes to TENANT_DATABASE_URL
  tenant_id (Integer, index=True, nullable=False)

IMPORTANT: There are NO SQLAlchemy ForeignKey constraints pointing to
tenants.id here — that table lives in a different physical database.
Tenant isolation is enforced at the application layer via:
    .filter_by(tenant_id=session["tenant_id"])

Any violation of this rule is a cross-tenant data leak (IDOR).
"""

import re
from datetime import datetime, timezone, date as date_type

from app import db


def _utcnow():
    return datetime.now(timezone.utc)


# ═════════════════════════════════════════════════════════════════════════════
# Profile
# ═════════════════════════════════════════════════════════════════════════════

class Profile(db.Model):
    """One-per-tenant portfolio profile."""
    __bind_key__ = 'tenant'
    __tablename__ = 'profile'
    __table_args__ = (
        db.Index('ix_profile_tenant_id', 'tenant_id'),
        db.Index('ix_profile_updated_at', 'updated_at'),
        db.Index('ix_profile_is_available', 'is_available'),
    )

    id         = db.Column(db.Integer, primary_key=True)
    # Tenant isolation — no FK (cross-DB); enforced at application layer
    tenant_id  = db.Column(db.Integer, nullable=False)
    tenant_slug = db.Column(db.String(120), nullable=False, index=True, default='default')

    name      = db.Column(db.String(100), nullable=False, default='')
    title     = db.Column(db.String(150), default='Full Stack Developer')
    subtitle  = db.Column(db.String(200), default='Building beautiful digital experiences')
    bio       = db.Column(db.Text, default='')
    bio_short = db.Column(db.String(300), default='')
    location  = db.Column(db.String(100), default='')
    email     = db.Column(db.String(120), default='')
    phone     = db.Column(db.String(30),  default='')

    profile_image = db.Column(db.String(255), default='')
    resume_url    = db.Column(db.String(255), default='')

    years_experience      = db.Column(db.Integer, default=0)
    clients_count         = db.Column(db.Integer, default=0)
    experience_start_year = db.Column(db.Integer, nullable=True)
    hero_tagline          = db.Column(db.String(200), default='')
    availability_status   = db.Column(db.String(100), default='Available for freelance')
    is_available          = db.Column(db.Boolean, default=True)

    social_links = db.Column(db.JSON, nullable=False, default=dict)

    # Billing / plan fields (duplicated from core for display; source of truth is core_db)
    plan            = db.Column(db.String(50), default='Basic')
    monthly_rate    = db.Column(db.Float, default=0.0)
    free_trial_days = db.Column(db.Integer, default=0)
    free_trial_ends = db.Column(db.DateTime(timezone=True), nullable=True)
    internal_notes  = db.Column(db.Text, default='')
    meta_title      = db.Column(db.String(200), default='')
    meta_description= db.Column(db.String(300), default='')
    og_image        = db.Column(db.String(255), default='')

    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # ── Business logic helpers ────────────────────────────────────────────────
    # NOTE: Profile lives in tenant_db (no FK to core_db Tenant/Subscription).
    # Subscription lookups cross into core_db via tenant_id at the app layer.

    def get_years_experience(self) -> int:
        if self.experience_start_year:
            return max(0, datetime.now(timezone.utc).year - self.experience_start_year)
        return self.years_experience or 0

    def _get_subscription(self):
        """Load the current Subscription from core_db (lazy, cached per request)."""
        if hasattr(self, '_current_subscription_cache'):
            return self._current_subscription_cache
        try:
            from app.models.core import Subscription
            sub = Subscription.current(self.tenant_id)
            self._current_subscription_cache = sub
            return sub
        except Exception:
            self._current_subscription_cache = None
            return None

    @property
    def tenant(self):
        """Load the Tenant from core_db by tenant_id (cached per instance)."""
        if hasattr(self, '_tenant_cache'):
            return self._tenant_cache
        try:
            from app.models.core import Tenant
            self._tenant_cache = Tenant.query.get(self.tenant_id)
        except Exception:
            self._tenant_cache = None
        return self._tenant_cache

    @tenant.setter
    def tenant(self, value):
        """Allow setting tenant directly (used in some legacy call sites)."""
        self._tenant_cache = value
        if value is not None:
            self.tenant_id = value.id
            self.tenant_slug = value.slug

    def current_subscription(self):
        return self._get_subscription()

    def effective_plan(self) -> str:
        """Current plan from active subscription, else profile/tenant default."""
        from app.models.core import normalize_plan_name
        sub = self._get_subscription()
        if sub and sub.plan and sub.status in ('active', 'pending'):
            return normalize_plan_name(sub.plan)
        return normalize_plan_name(self.plan or 'Basic')

    def plan_features(self) -> dict:
        from app.models.core import get_plan_features
        return get_plan_features(self.effective_plan())

    def plan_allows(self, feature: str) -> bool:
        return bool(self.plan_features().get(feature))

    def project_limit(self):
        return self.plan_features().get('max_projects')

    def skill_limit(self):
        return self.plan_features().get('max_skills')

    def media_upload_limit(self):
        return self.plan_features().get('max_media_uploads')

    def trial_days_remaining(self) -> int:
        if self.free_trial_ends is None:
            return 0
        trial_ends = self.free_trial_ends
        if trial_ends.tzinfo is None:
            trial_ends = trial_ends.replace(tzinfo=timezone.utc)
        delta = trial_ends - datetime.now(timezone.utc)
        return max(0, delta.days)

    def is_trial_active(self) -> bool:
        if not self.free_trial_ends:
            return False
        trial_ends = self.free_trial_ends
        if trial_ends.tzinfo is None:
            trial_ends = trial_ends.replace(tzinfo=timezone.utc)
        return trial_ends > datetime.now(timezone.utc)

    def is_expired(self) -> bool:
        sub = self._get_subscription()
        if sub and sub.is_active():
            return False
        if self.is_trial_active():
            return False
        try:
            from app.services.billing import is_in_grace_period
            if is_in_grace_period(self):
                return False
        except Exception:
            pass
        if not self.free_trial_ends and not sub:
            return False
        if self.free_trial_ends:
            trial_ends = self.free_trial_ends
            if trial_ends.tzinfo is None:
                trial_ends = trial_ends.replace(tzinfo=timezone.utc)
            if trial_ends <= datetime.now(timezone.utc):
                if not sub or sub.status in ('expired', 'cancelled'):
                    return True
        if sub and sub.status == 'expired':
            return True
        return False

    def enforce_expiry(self, commit: bool = True) -> bool:
        if not self.is_expired():
            return False
        try:
            from app.models.core import Tenant
            tenant = Tenant.query.get(self.tenant_id)
            if tenant and tenant.status != 'suspended':
                tenant.status = 'suspended'
                if commit:
                    from app import db
                    db.session.add(tenant)
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
        except Exception:
            pass
        return True

    @property
    def subscription_status(self) -> str:
        try:
            from app.services.billing import subscription_access_status
            return subscription_access_status(self)
        except Exception:
            return self.license_status()

    def license_status(self) -> str:
        status = self.subscription_status
        if status == 'none':
            return 'unlicensed'
        return status

    def is_subscription_active(self) -> bool:
        return self.subscription_status in ('trial', 'active', 'grace')

    def sync_license_from_subscription(self) -> None:
        from app.models.core import normalize_plan_name
        sub = self._get_subscription()
        if sub and sub.status == 'active':
            self.plan = normalize_plan_name(sub.plan)
        if hasattr(self, '_current_subscription_cache'):
            del self._current_subscription_cache

    # ── Backward-compat properties ────────────────────────────────────────────

    @property
    def license_plan(self) -> str:
        return self.effective_plan()

    @property
    def license_active(self) -> bool:
        return self.is_subscription_active()

    @property
    def license_activated_at(self):
        sub = self._get_subscription()
        return sub.started_at if sub and sub.started_at else None

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<Profile tenant={self.tenant_slug}>'


# ═════════════════════════════════════════════════════════════════════════════
# Skill
# ═════════════════════════════════════════════════════════════════════════════

class Skill(db.Model):
    __bind_key__ = 'tenant'
    __tablename__ = 'skills'
    __table_args__ = (
        db.Index('ix_skills_tenant_id', 'tenant_id'),
        db.Index('ix_skills_tenant_visible', 'tenant_id', 'is_visible'),
    )

    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, nullable=False)
    tenant_slug = db.Column(db.String(120), nullable=False, index=True, default='default')
    name        = db.Column(db.String(100), nullable=False)
    proficiency = db.Column(db.Integer, default=80)
    category    = db.Column(db.String(50), default='Frontend')
    icon        = db.Column(db.String(100), default='')
    color       = db.Column(db.String(20),  default='')
    order       = db.Column(db.Integer,     default=0)
    is_visible  = db.Column(db.Boolean,     default=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=_utcnow)

    CATEGORIES = [
        'Frontend', 'Backend', 'Database',
        'DevOps', 'Design', 'Tools', 'Mobile', 'Other',
    ]

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<Skill {self.name}>'


# ═════════════════════════════════════════════════════════════════════════════
# Project
# ═════════════════════════════════════════════════════════════════════════════

class Project(db.Model):
    __bind_key__ = 'tenant'
    __tablename__ = 'projects'
    __table_args__ = (
        db.Index('ix_projects_tenant_id', 'tenant_id'),
        db.Index('ix_projects_status_featured', 'status', 'is_featured'),
        db.Index('ix_projects_tenant_status', 'tenant_id', 'status'),
        db.Index('ix_projects_tenant_category_status', 'tenant_id', 'category', 'status'),
    )

    id                = db.Column(db.Integer, primary_key=True)
    tenant_id         = db.Column(db.Integer, nullable=False)
    tenant_slug       = db.Column(db.String(120), nullable=False, index=True, default='default')
    title             = db.Column(db.String(200), nullable=False)
    slug              = db.Column(db.String(200), index=True)  # unique per tenant enforced by app
    description       = db.Column(db.Text, default='')
    description_short = db.Column(db.String(300), default='')
    image             = db.Column(db.String(255), default='')
    live_url          = db.Column(db.String(500), default='')
    github_url        = db.Column(db.String(500), default='')
    framework         = db.Column(db.String(120), default='')
    language          = db.Column(db.String(120), default='')
    tags              = db.Column(db.JSON, nullable=False, default=list)
    category          = db.Column(db.String(100), default='Web App')
    status            = db.Column(db.String(50),  default='published')
    is_featured       = db.Column(db.Boolean,     default=False)
    order             = db.Column(db.Integer,     default=0)
    view_count        = db.Column(db.Integer,     default=0)
    date_completed    = db.Column(db.Date, nullable=True)
    created_at        = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at        = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    CATEGORIES = [
        'Web App', 'Mobile App', 'API', 'UI/UX',
        'Data Science', 'DevOps', 'Open Source', 'Other',
    ]

    @classmethod
    def published_for_tenant(cls, tenant):
        q = cls.query.filter_by(status='published')

        if isinstance(tenant, int):
            return q.filter_by(tenant_id=tenant).order_by(
                cls.is_featured.desc(),
                cls.order.asc(),
                cls.created_at.desc(),
            )

        if isinstance(tenant, str):
            t = tenant.strip().lower()
            try:
                tenant_id = int(t)
                return q.filter_by(tenant_id=tenant_id).order_by(
                    cls.is_featured.desc(),
                    cls.order.asc(),
                    cls.created_at.desc(),
                )
            except (TypeError, ValueError):
                pass

            from app.models.core import Tenant

            tenant_record = Tenant.query.filter_by(slug=t, status='active').first()
            if not tenant_record:
                return cls.query.filter(False)

            return q.filter_by(tenant_id=tenant_record.id).order_by(
                cls.is_featured.desc(),
                cls.order.asc(),
                cls.created_at.desc(),
            )

        return cls.query.filter(False)

    @property
    def is_published(self) -> bool:
        return self.status == 'published'

    def generate_slug(self) -> str:
        slug = self.title.lower()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        return slug.strip('-')

    def increment_views(self):
        self.view_count = (self.view_count or 0) + 1

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, date_type):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<Project {self.title}>'


# ═════════════════════════════════════════════════════════════════════════════
# Testimonial
# ═════════════════════════════════════════════════════════════════════════════

class Testimonial(db.Model):
    __bind_key__ = 'tenant'
    __tablename__ = 'testimonials'
    __table_args__ = (
        db.Index('ix_testimonials_tenant_id', 'tenant_id'),
        db.Index('ix_testimonials_tenant_visible', 'tenant_id', 'is_visible'),
    )

    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, nullable=False)
    tenant_slug    = db.Column(db.String(120), nullable=False, index=True, default='default')
    author_name    = db.Column(db.String(100), nullable=False)
    author_title   = db.Column(db.String(150), default='')
    author_company = db.Column(db.String(100), default='')
    author_avatar  = db.Column(db.String(255), default='')
    content        = db.Column(db.Text, nullable=False)
    rating         = db.Column(db.Integer, default=5)
    is_featured    = db.Column(db.Boolean, default=False)
    is_visible     = db.Column(db.Boolean, default=True)
    order          = db.Column(db.Integer, default=0)
    created_at     = db.Column(db.DateTime(timezone=True), default=_utcnow)

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<Testimonial from {self.author_name}>'


# ═════════════════════════════════════════════════════════════════════════════
# Service
# ═════════════════════════════════════════════════════════════════════════════

class Service(db.Model):
    __bind_key__ = 'tenant'
    __tablename__ = 'services'
    __table_args__ = (
        db.Index('ix_services_tenant_id', 'tenant_id'),
        db.Index('ix_services_tenant_order', 'tenant_id', 'display_order'),
        db.Index('ix_services_tenant_visible', 'tenant_id', 'is_visible'),
    )

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, nullable=False)
    tenant_slug   = db.Column(db.String(120), nullable=False, index=True, default='default')
    title         = db.Column(db.String(100), nullable=False)
    description   = db.Column(db.Text, default='')
    icon          = db.Column(db.String(100), default='lucide:briefcase')
    features      = db.Column(db.Text, default='')
    display_order = db.Column(db.Integer, default=0)
    is_visible    = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at    = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def features_list(self) -> list[str]:
        return [f.strip() for f in (self.features or '').splitlines() if f.strip()]

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        d['features_list'] = self.features_list
        return d

    def __repr__(self):
        return f'<Service {self.title}>'


# ═════════════════════════════════════════════════════════════════════════════
# TenantFormSettings — re-exported from canonical owner
# ═════════════════════════════════════════════════════════════════════════════
# The canonical model lives in app/models/tenant_form_settings.py (core_db,
# ForeignKey on tenants.id). Declaring it here with __bind_key__ = 'tenant'
# was wrong — tenant_form_settings lives in core_db, not tenant_db.
# This re-export keeps all existing import paths working unchanged.
# DO NOT re-add class TenantFormSettings(db.Model) here.

from app.models.tenant_form_settings import (  # noqa: E402
    TenantFormSettings,
    VALID_PROVIDERS,
    BASIN_PREFIX,
    WEB3FORMS_URL,
)
