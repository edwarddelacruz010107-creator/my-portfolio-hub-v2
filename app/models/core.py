"""
app/models/core.py — core_db models (SQLALCHEMY_DATABASE_URI / default bind)

Covers: authentication, tenant management, billing, platform settings,
        messaging, notifications, and all system-level configuration.

Bind: default (CORE_DATABASE_URL)
"""

import json
import re
import secrets
import string
import hashlib
import os as _os
import base64 as _base64
import logging as _logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pyotp
from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet as _Fernet, InvalidToken as _InvalidToken
from flask_login import UserMixin
from sqlalchemy.ext.hybrid import hybrid_property

from app import db
from app.utils.datetime_utils import ensure_utc_aware, is_expired as datetime_is_expired, utc_expiry, utc_now
from app.system_plan import (
    ADMINISTRATOR_PLAN_SLUG,
    has_administrator_access,
    is_administrator_plan,
)

import threading

# Module-level lock to serialize singleton creation across threads/process
_global_email_config_lock = threading.Lock()



# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

_comm_logger = _logging.getLogger(__name__)


def _utcnow():
    return utc_now()


SUBSCRIPTION_PLAN_ORDER = {'starter': 1, 'pro': 2, 'business': 3, 'enterprise': 4, 'administrator': 99}

_PLAN_ALIASES = {
    'trial': 'trial',
    'free_trial': 'trial',
    'basic': 'starter',
    'starter': 'starter',
    'pro': 'pro',
    'professional': 'pro',
    'business': 'business',
    'enterprise': 'enterprise',
    'administrator': 'administrator',
    'admin': 'administrator',
    'system': 'administrator',
}


PAID_PLAN_NAMES = frozenset({'starter', 'pro', 'business', 'enterprise'})

PLAN_FEATURES = {
    'trial': {
        'max_projects': 10,
        'max_skills': 20,
        'max_media_uploads': 10,
        'max_testimonials': 3,
        'max_certificates': 3,
        'max_services': 3,
        'max_experiences': 3,
        'storage_limit_mb': 10,
        'max_upload_size_mb': 2,
        'daily_email_limit': 50,
        'max_team_members': 1,
        'projects': True,
        'skills': True,
        'uploads': True,
        'testimonials': True,
        'certificates': True,
        'badges': True,
        'services': True,
        'experiences': True,
        'custom_domain': False,
        'analytics': False,
        'white_label': False,
        'team_members': False,
        'api_access': False,
        'theme_customization': False,
        'premium_themes': False,
        'email_services': False,
        'custom_smtp': False,
        'resend': False,
        'mailersend': False,
        'ai_features': False,
        'branding_removal': False,
    },
    'starter': {
        'max_projects': 25,
        'max_skills': 20,
        'max_media_uploads': 10,
        'max_testimonials': 10,
        'max_certificates': 10,
        'max_services': 10,
        'max_experiences': 10,
        'testimonials': True,
        'certificates': True,
        'badges': True,
        'services': True,
        'experiences': True,
        'custom_domain': False,
        'analytics': False,
        'white_label': False,
        'team_members': False,
        'api_access': False,
        'theme_customization': False,
    },
    'pro': {
        'max_projects': None,
        'max_skills': None,
        'max_media_uploads': None,
        'custom_domain': True,
        'analytics': True,
        'white_label': False,
        'team_members': False,
        'api_access': False,
        'theme_customization': True,
        'premium_themes': True,
        'testimonials': True,
        'certificates': True,
        'badges': True,
        'services': True,
        'experiences': True,
        'max_testimonials': None,
        'max_certificates': None,
        'max_services': None,
        'max_experiences': None,
    },
    'business': {
        'max_projects': None,
        'max_skills': None,
        'max_media_uploads': None,
        'custom_domain': True,
        'analytics': True,
        'white_label': True,
        'team_members': True,
        'api_access': True,
        'theme_customization': True,
        'premium_themes': True,
        'testimonials': True,
        'certificates': True,
        'badges': True,
        'services': True,
        'experiences': True,
        'max_testimonials': None,
        'max_certificates': None,
        'max_services': None,
        'max_experiences': None,
    },
    'enterprise': {
        'max_projects': None,
        'max_skills': None,
        'max_media_uploads': None,
        'custom_domain': True,
        'analytics': True,
        'white_label': True,
        'team_members': True,
        'api_access': True,
        'theme_customization': True,
        'premium_themes': True,
        'email_services': True,
        'certificates': True,
        'badges': True,
        'services': True,
        'experiences': True,
        'max_testimonials': None,
        'max_certificates': None,
        'max_services': None,
        'max_experiences': None,
    },
    'administrator': {
        'max_projects': None,
        'max_skills': None,
        'max_media_uploads': None,
        'custom_domain': True,
        'analytics': True,
        'white_label': True,
        'team_members': True,
        'api_access': True,
        'theme_customization': True,
        'theme_marketplace': True,
        'premium_themes': True,
        'developer_themes': True,
        'custom_branding': True,
        'email_services': True,
        'certificates': True,
        'badges': True,
        'services': True,
        'experiences': True,
        'max_testimonials': None,
        'max_certificates': None,
        'max_services': None,
        'max_experiences': None,
        'storage_limit_mb': None,
        'all_features': True,
    },
}


def normalize_plan_name(plan: str) -> str:
    if not plan:
        return 'starter'
    normalized = (plan or '').strip().lower()
    return _PLAN_ALIASES.get(normalized, plan.strip().lower())


def get_plan_features(plan: str) -> dict:
    normalized = normalize_plan_name(plan)
    if is_administrator_plan(normalized):
        return PLAN_FEATURES['administrator'].copy()
    features = PLAN_FEATURES.get(normalized, PLAN_FEATURES['starter']).copy()
    try:
        from app.services.billing.trial_limits import apply_plan_feature_overrides
        features = apply_plan_feature_overrides(normalized, features)
    except Exception as exc:
        _comm_logger.warning('Unable to load editable plan limits for %s; using defaults: %s', normalized, exc)
    return features


# ─────────────────────────────────────────────────────────────────────────────
# Fernet encryption (for secrets stored in DB)
# ─────────────────────────────────────────────────────────────────────────────

def _get_fernet() -> _Fernet:
    key = _os.environ.get('FERNET_KEY', '').strip()
    if not key:
        flask_env = _os.environ.get('FLASK_ENV', 'development').lower()
        if flask_env == 'production':
            raise RuntimeError(
                'FERNET_KEY environment variable is not set. '
                'Generate one with: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        import hashlib
        raw = _os.environ.get('SECRET_KEY', 'dev-insecure-key').encode()
        digest = hashlib.sha256(raw).digest()
        key = _base64.urlsafe_b64encode(digest).decode()
        _comm_logger.warning(
            'FERNET_KEY not set — deriving encryption key from SECRET_KEY for '
            'development. Set FERNET_KEY explicitly before deploying to production.'
        )
    return _Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ''
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    # Use identity/equality checks — never truthiness — to avoid triggering
    # SQLAlchemy's __bool__ guard when an InstrumentedAttribute is passed
    # before the ORM has loaded the scalar value from the database.
    if ciphertext is None or ciphertext == '':
        return ''
    # Guard against SQLAlchemy InstrumentedAttribute/Column descriptor being
    # passed instead of a scalar string value (happens when a model property
    # is accessed on an un-refreshed instance in some ORM states).
    if not isinstance(ciphertext, str):
        _comm_logger.error(
            'decrypt_secret received non-str type %s — ORM column not yet loaded. '
            'Returning empty string to avoid AttributeError.',
            type(ciphertext).__name__,
        )
        return ''
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except _InvalidToken:
        _comm_logger.warning('decrypt_secret: InvalidToken — key rotation or corruption?')
        return ''
    except RuntimeError:
        _comm_logger.critical('decrypt_secret: FERNET_KEY not set in production.')
        return ''
    except Exception as exc:
        _comm_logger.error('decrypt_secret unexpected error: %s', exc)
        return ''


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: Tenant
# ═════════════════════════════════════════════════════════════════════════════

class Tenant(db.Model):
    """
    Tenant registry — lives in core_db.

    tenant_id from this table is the foreign-key used in ALL tenant_data_db
    tables. There is NO cross-DB FK; tenant_id is an integer contract enforced
    at the application layer.
    """
    __tablename__ = 'tenants'
    # No __bind_key__ → uses SQLALCHEMY_DATABASE_URI (core_db)

    id           = db.Column(db.Integer, primary_key=True)
    slug         = db.Column(db.String(120), nullable=False, unique=True, index=True)
    company_name = db.Column(db.String(200), nullable=False, default='')
    email        = db.Column(db.String(120), nullable=False, default='')
    contact_email = db.Column(db.String(120), nullable=True, index=True)
    # Contact form routing
    form_provider  = db.Column(db.String(20),  nullable=False, default='internal', index=True)
    basin_endpoint = db.Column(db.Text,         nullable=True)
    status     = db.Column(db.String(50),  nullable=False, default='active')
    plan       = db.Column(db.String(50),  nullable=False, default='starter')
    subscription_state = db.Column(db.String(32), nullable=False, default='trial')
    trial_status = db.Column(db.String(30), nullable=False, default='trial')
    plan_name  = db.Column(db.String(50), nullable=False, default='starter')
    trial_started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    trial_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    grace_period_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    subscription_started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    subscription_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Tenant-confirmed billing location. Country selects a suggested payment currency;
    # plan prices remain authoritative in USD.
    country_code = db.Column(db.String(2), nullable=True)
    preferred_currency = db.Column(db.String(3), nullable=True)
    country_source = db.Column(db.String(30), nullable=False, default='unconfirmed', server_default='unconfirmed')
    country_updated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # All relationships on the CORE side (no cross-DB relationships)
    users                 = db.relationship('User',  back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')
    subscriptions         = db.relationship('Subscription', back_populates='tenant', cascade='all, delete-orphan', lazy='select', order_by='Subscription.created_at.desc()')
    payment_instructions  = db.relationship('PaymentInstruction', back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')
    payment_methods       = db.relationship('PaymentMethod', back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')
    payments              = db.relationship('PaymentSubmission', back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')
    custom_domains        = db.relationship('TenantCustomDomain', back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')

    @property
    def normalized_plan(self) -> str:
        return normalize_plan_name(self.plan)

    def effective_plan(self) -> str:
        """Current plan from active subscription, else Trial/tenant.plan."""
        if has_administrator_access(self):
            return ADMINISTRATOR_PLAN_SLUG
        active = next(
            (s for s in self.subscriptions if s.is_active()),
            None,
        )
        if active:
            return normalize_plan_name(active.plan)
        if (self.subscription_state or '').strip().lower() == 'trial':
            return 'trial'
        return self.normalized_plan

    @property
    def subscription_status(self) -> str:
        if has_administrator_access(self):
            return 'active'
        state = (self.subscription_state or '').strip().lower()
        if state in {'trial', 'active', 'grace', 'readonly', 'expired', 'suspended', 'cancelled'}:
            return state

        active_sub = next((s for s in self.subscriptions if s.status not in ('cancelled', 'expired')), None)
        if active_sub is not None:
            return active_sub.status
        return 'none'

    def has_feature(self, feature: str) -> bool:
        if has_administrator_access(self):
            return True
        if self.subscription_state in {'readonly', 'suspended', 'expired', 'cancelled'}:
            return False
        return bool(get_plan_features(self.effective_plan()).get(feature, False))

    def can_publish(self) -> bool:
        if has_administrator_access(self):
            return True
        return self.subscription_state in {'trial', 'active'}

    def can_upload(self) -> bool:
        if has_administrator_access(self):
            return True
        return self.subscription_state in {'trial', 'active'}

    @subscription_status.setter
    def subscription_status(self, value: str | None) -> None:
        state = (value or 'none').strip().lower()
        self.subscription_state = state
        self.trial_status = state

    def is_active_subscription(self) -> bool:
        if has_administrator_access(self):
            return True
        return any(s.is_active() for s in self.subscriptions)

    def plan_features(self) -> dict:
        return get_plan_features(self.effective_plan())

    def __repr__(self):
        return f'<Tenant {self.slug}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: TenantCustomDomain
# ═════════════════════════════════════════════════════════════════════════════

class TenantCustomDomain(db.Model):
    """Verified custom domain mapping for tenant public portfolios.

    Lives in core_db because domain ownership/routing is platform-level
    metadata. Tenant content remains in tenant_data_db and is looked up by
    tenant_slug after this host mapping resolves.
    """
    __tablename__ = 'tenant_custom_domains'
    __table_args__ = (
        db.UniqueConstraint('normalized_domain', name='uq_tenant_custom_domains_normalized_domain'),
        db.Index('ix_tenant_custom_domains_tenant_status', 'tenant_id', 'status'),
        db.Index('ix_tenant_custom_domains_status_domain', 'status', 'normalized_domain'),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    tenant_slug = db.Column(db.String(120), nullable=False, index=True)
    domain = db.Column(db.String(255), nullable=False)
    normalized_domain = db.Column(db.String(255), nullable=False, unique=True, index=True)
    verification_token = db.Column(db.String(120), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default='pending', index=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    failure_reason = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant = db.relationship('Tenant', back_populates='custom_domains', lazy='joined')

    @property
    def is_verified(self) -> bool:
        return (self.status or '').lower() == 'verified'

    def __repr__(self):
        return f'<TenantCustomDomain {self.normalized_domain} tenant={self.tenant_slug} status={self.status}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: User (tenant admin)
# ═════════════════════════════════════════════════════════════════════════════

class User(UserMixin, db.Model):
    """Tenant admin user — authenticates against core_db."""
    __tablename__ = 'users'
    __table_args__ = (
        db.Index('ix_users_tenant_admin', 'tenant_slug', 'is_admin'),
    )

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64),  unique=True, nullable=False, index=True)
    # Email uniqueness is enforced by app.services.auth.email_policy and the
    # partial unique index from migration 0048: all normal emails are unique;
    # only the protected owner email may be shared by SuperAdmin + default admin.
    email         = db.Column(db.String(120), unique=False, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    tenant_slug   = db.Column(db.String(120), nullable=False, index=True, default='default')
    tenant_id     = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False, index=True)
    is_admin      = db.Column(db.Boolean, default=True)
    is_superadmin = db.Column(db.Boolean, default=False, nullable=False)
    created_at    = db.Column(db.DateTime(timezone=True), default=_utcnow)
    last_login    = db.Column(db.DateTime(timezone=True), nullable=True)
    last_login_ip = db.Column(db.String(45), nullable=True)

    # 2FA
    totp_secret           = db.Column(db.String(64), nullable=True)
    totp_enabled          = db.Column(db.Boolean, default=False, nullable=False)
    totp_backup_codes     = db.Column(db.Text, nullable=True)
    last_totp_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_totp_code_hash   = db.Column(db.String(64), nullable=True)

    # Account security
    failed_login_attempts  = db.Column(db.Integer, default=0, nullable=False)
    last_failed_login_at   = db.Column(db.DateTime(timezone=True), nullable=True)
    require_password_reset = db.Column(db.Boolean, default=False, nullable=False)
    last_password_changed  = db.Column(db.DateTime(timezone=True), nullable=True)
    session_token          = db.Column(db.String(255), unique=True, nullable=True)
    password_reset_token   = db.Column(db.String(100), unique=True, nullable=True, index=True)
    password_reset_expires = db.Column(db.DateTime(timezone=True), nullable=True)

    # OAuth identities. Google and GitHub are optional second login methods.
    # Superadmin accounts are blocked from OAuth route code; public signup via
    # OAuth still creates tenant-admin trial accounts only.
    google_id     = db.Column(db.String(255), unique=True, nullable=True, index=True)
    github_id     = db.Column(db.String(255), unique=True, nullable=True, index=True)
    auth_provider = db.Column(db.String(20), nullable=False, default='local')  # local | google | github | both
    avatar_url    = db.Column(db.String(500), nullable=True)

    email_verified             = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    email_verification_token   = db.Column(db.String(64), nullable=True, unique=True, index=True)
    email_verification_expires = db.Column(db.DateTime(timezone=True), nullable=True)
    last_login_user_agent      = db.Column(db.String(255), nullable=True)

    tenant = db.relationship('Tenant', back_populates='users', lazy='joined')

    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter
    def password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def verify_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def get_totp_uri(self, issuer: str = 'Portfolio CMS') -> str:
        return pyotp.totp.TOTP(self.totp_secret).provisioning_uri(
            name=self.email, issuer_name=issuer,
        )

    def verify_totp(self, code: str) -> bool:
        if not self.totp_secret:
            return False
        totp   = pyotp.TOTP(self.totp_secret)
        window = current_app.config.get('TOTP_VALID_WINDOW', 1)
        if not totp.verify(code, valid_window=window):
            return False
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        now = utc_now()
        if self.last_totp_code_hash == code_hash and self.last_totp_verified_at:
            last = ensure_utc_aware(self.last_totp_verified_at)
            if last and (now - last).total_seconds() < 60:
                return False
        self.last_totp_code_hash   = code_hash
        self.last_totp_verified_at = now
        return True

    def generate_totp_secret(self) -> str:
        self.totp_secret = pyotp.random_base32()
        return self.totp_secret

    def generate_backup_codes(self, count: int = 10) -> list[str]:
        alphabet = string.ascii_uppercase + string.digits
        codes = [
            ''.join(secrets.choice(alphabet) for _ in range(5))
            + '-'
            + ''.join(secrets.choice(alphabet) for _ in range(5))
            for _ in range(count)
        ]
        hashed = [generate_password_hash(c) for c in codes]
        self.totp_backup_codes = json.dumps(hashed)
        return codes

    def use_backup_code(self, code: str) -> bool:
        if not self.totp_backup_codes:
            return False
        try:
            hashed_list: list[str] = json.loads(self.totp_backup_codes)
        except (ValueError, TypeError):
            return False
        code_clean = code.strip().upper()
        for i, h in enumerate(hashed_list):
            if check_password_hash(h, code_clean):
                hashed_list.pop(i)
                self.totp_backup_codes = json.dumps(hashed_list)
                return True
        return False

    @property
    def backup_codes_remaining(self) -> int:
        if not self.totp_backup_codes:
            return 0
        try:
            return len(json.loads(self.totp_backup_codes))
        except (ValueError, TypeError):
            return 0

    def generate_reset_token(self, expires_in_minutes: int = 30) -> str:
        token = secrets.token_urlsafe(32)
        self.password_reset_token   = hashlib.sha256(token.encode()).hexdigest()
        self.password_reset_expires = utc_expiry(minutes=expires_in_minutes)
        return token

    def verify_reset_token(self, token: str) -> bool:
        if not self.password_reset_token or not self.password_reset_expires:
            return False
        expires = ensure_utc_aware(self.password_reset_expires)
        if expires is None:
            return False
        return (
            secrets.compare_digest(self.password_reset_token, hashlib.sha256(token.encode()).hexdigest())
            and utc_now() < expires
        )

    def clear_reset_token(self):
        self.password_reset_token   = None
        self.password_reset_expires = None

    def __repr__(self):
        return f'<User {self.username}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: PendingSignup
# ═════════════════════════════════════════════════════════════════════════════

class PendingSignup(db.Model):
    """Temporary signup state for unverified local signups."""
    __tablename__ = 'pending_signups'

    id               = db.Column(db.Integer, primary_key=True)
    email            = db.Column(db.String(120), nullable=False, index=True)
    username         = db.Column(db.String(64), nullable=False, index=True)
    full_name        = db.Column(db.String(100), nullable=False)
    password_hash    = db.Column(db.String(255), nullable=False)
    otp_hash         = db.Column(db.String(64), nullable=False)
    otp_expires_at   = db.Column(db.DateTime(timezone=True), nullable=False)
    otp_attempts     = db.Column(db.Integer, default=0, nullable=False)
    last_otp_sent_at = db.Column(db.DateTime(timezone=True), nullable=False)
    email_verified   = db.Column(db.Boolean, nullable=False, default=False)
    ip_address       = db.Column(db.String(45), nullable=True)
    user_agent       = db.Column(db.String(300), nullable=True)
    created_at       = db.Column(db.DateTime(timezone=True), default=_utcnow)
    expires_at       = db.Column(db.DateTime(timezone=True), nullable=False)

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def verify_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    def set_otp(self, raw: str, ttl_minutes: int) -> None:
        self.otp_hash = hashlib.sha256(raw.encode()).hexdigest()
        self.otp_expires_at = utc_expiry(minutes=ttl_minutes)
        self.otp_attempts = 0
        self.last_otp_sent_at = utc_now()

    def verify_otp(self, raw: str, max_attempts: int = 5) -> tuple[bool, str]:
        if self.otp_attempts >= max_attempts:
            return False, 'Too many failed attempts. Request a new code.'

        expires_at = ensure_utc_aware(self.otp_expires_at)
        if expires_at is None or utc_now() >= expires_at:
            return False, 'The code has expired. Request a new one.'

        if self.otp_hash != hashlib.sha256(raw.encode()).hexdigest():
            self.otp_attempts += 1
            return False, f'Incorrect code. {max_attempts - self.otp_attempts} attempt(s) remaining.'

        return True, ''

    @property
    def is_expired(self) -> bool:
        exp = ensure_utc_aware(self.expires_at)
        return True if exp is None else utc_now() >= exp

    def __repr__(self):
        return f'<PendingSignup {self.email}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: Subscription
# ═════════════════════════════════════════════════════════════════════

class Subscription(db.Model):
    """PayMongo / manual subscription for a tenant."""
    __tablename__ = 'subscriptions'
    __table_args__ = (
        db.Index('ix_subscriptions_status_expires_at', 'status', 'expires_at'),
        db.Index('ix_subscriptions_tenant_status', 'tenant_id', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False, index=True)
    plan      = db.Column(db.String(50), nullable=False, default='Basic')
    status    = db.Column(db.String(30), nullable=False, default='pending')

    billing_cycle  = db.Column(db.String(20), default='monthly')
    amount_paid    = db.Column(db.Float, nullable=False, default=0.0)
    payment_method = db.Column(db.String(100), default='')

    # Durable coupon reference for this activation attempt. Session-based
    # stashing (discount_checkout.stash_coupon) only survives within a
    # single tenant browser session, which breaks for any activation path
    # that completes out-of-session (PayMongo webhook, superadmin manual
    # approval, superadmin resync). This column is the cross-request source
    # of truth; it is set at plan-selection time and cleared/reset whenever
    # get_or_create_pending_subscription() prepares a fresh checkout attempt.
    coupon_code    = db.Column(db.String(100), nullable=True)

    paymongo_id              = db.Column(db.String(255), nullable=True, index=True)
    paymongo_customer_id     = db.Column(db.String(255), nullable=True)
    paymongo_subscription_id = db.Column(db.String(255), nullable=True, unique=True, index=True)
    paymongo_payment_id      = db.Column(db.String(255), nullable=True, unique=True, index=True)

    started_at      = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at      = db.Column(db.DateTime(timezone=True), nullable=True)
    cancelled_at    = db.Column(db.DateTime(timezone=True), nullable=True)
    last_webhook_at = db.Column(db.DateTime(timezone=True), nullable=True)
    reminder_sent_7d  = db.Column(db.Boolean, default=False, nullable=False)
    reminder_sent_30d = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant = db.relationship('Tenant', back_populates='subscriptions', lazy='joined')

    @classmethod
    def current(cls, tenant_id: int) -> 'Subscription | None':
        sub = (
            cls.query
            .filter(cls.tenant_id == tenant_id, cls.status.notin_(['cancelled', 'trial']))
            .order_by(cls.created_at.desc())
            .first()
        )
        if sub is not None:
            sub.refresh_status(commit=False)
        return sub

    def refresh_status(self, commit: bool = False):
        if self.status in ('expired', 'cancelled'):
            return self
        now = utc_now()
        started = ensure_utc_aware(self.started_at)
        expires = ensure_utc_aware(self.expires_at)

        # A scheduled subscription becomes active as soon as its UTC start
        # timestamp is reached. This transition also happens lazily when the
        # tenant/profile reads the current subscription, so activation does
        # not depend solely on a background scheduler being enabled.
        if self.status == 'scheduled' and started is not None and started <= now:
            self.status = 'active'

        if expires is not None and expires <= now:
            self.status = 'expired'

        if commit:
            db.session.add(self)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        return self

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires = ensure_utc_aware(self.expires_at)
        return False if expires is None else expires <= utc_now()

    def is_active(self) -> bool:
        now = utc_now()
        started = ensure_utc_aware(self.started_at)
        if self.status == 'scheduled':
            if started is None or started > now:
                return False
        elif self.status != 'active':
            return False
        if self.expires_at is None:
            return True
        expires = ensure_utc_aware(self.expires_at)
        return False if expires is None else expires > now

    @property
    def normalized_plan(self) -> str:
        return normalize_plan_name(self.plan)

    @property
    def status_label(self) -> str:
        _MAP = {
            'trial':     'Trial',
            'pending':   'Pending Payment',
            'scheduled': 'Scheduled',
            'active':    'Active',
            'expired':   'Expired',
            'cancelled': 'Cancelled',
        }
        return _MAP.get(self.status, self.status.title())

    @property
    def next_billing_date(self):
        return self.expires_at if self.status in ('active', 'scheduled') else None

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<Subscription {self.tenant_id} {self.plan} {self.status}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: DiscountCampaign / DiscountRedemption  (v6.6 — Discount Manager)
# ═════════════════════════════════════════════════════════════════════════════

class DiscountCampaign(db.Model):
    """
    Superadmin-managed discount / promotion campaign.

    A campaign is either a coupon (code is set, tenant must enter it at
    checkout) or an auto-applied global campaign (is_global=True, code is
    NULL, engine applies it without user input). Money math itself never
    lives here — DiscountCampaign is pure configuration; the actual
    calculation and eligibility checks live in
    app/services/billing/discount_service.py so there is exactly one place
    that touches billing totals.
    """
    __tablename__ = 'discount_campaigns'
    __table_args__ = (
        db.Index('ix_discount_campaigns_active_dates', 'is_active', 'starts_at', 'expires_at'),
        db.CheckConstraint(
            "discount_type IN ('percent', 'fixed')",
            name='ck_discount_campaigns_type',
        ),
        db.CheckConstraint(
            "applies_to IN ('monthly', 'yearly', 'one_time', 'all')",
            name='ck_discount_campaigns_applies_to',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)

    # NULL code + is_global=True => auto-applied campaign, no coupon entry
    code = db.Column(db.String(100), unique=True, index=True, nullable=True)

    discount_type = db.Column(db.String(20), nullable=False, default='percent')  # percent | fixed
    value = db.Column(db.Numeric(10, 2), nullable=False)

    applies_to = db.Column(db.String(20), nullable=False, default='all')  # monthly | yearly | one_time | all
    plan_slug = db.Column(db.String(100), nullable=True)  # NULL = all plans
    is_global = db.Column(db.Boolean, default=False, nullable=False)

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    usage_limit = db.Column(db.Integer, nullable=True)       # NULL = unlimited
    usage_count = db.Column(db.Integer, default=0, nullable=False)
    per_tenant_limit = db.Column(db.Integer, default=1, nullable=True)  # NULL = unlimited per tenant
    first_time_only = db.Column(db.Boolean, default=False, nullable=False)

    starts_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    redemptions = db.relationship(
        'DiscountRedemption', back_populates='campaign', lazy='select',
        cascade='all, delete-orphan',
    )

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires = ensure_utc_aware(self.expires_at)
        return False if expires is None else expires <= utc_now()

    @property
    def has_started(self) -> bool:
        if self.starts_at is None:
            return True
        starts = ensure_utc_aware(self.starts_at)
        return True if starts is None else starts <= utc_now()

    @property
    def usage_remaining(self):
        if self.usage_limit is None:
            return None
        return max(0, self.usage_limit - (self.usage_count or 0))

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<DiscountCampaign {self.code or ("global:" + str(self.id))} {self.discount_type}:{self.value}>'


class DiscountRedemption(db.Model):
    """
    One row per successful discount application. This is the source of
    truth for per-tenant limits, first-time-only checks, and analytics —
    DiscountCampaign.usage_count is a denormalized counter kept in sync by
    the service layer, never written to directly by routes.
    """
    __tablename__ = 'discount_redemptions'
    __table_args__ = (
        db.Index('ix_discount_redemptions_campaign_tenant', 'campaign_id', 'tenant_id'),
        # Defense-in-depth against double redemption when multiple activation
        # paths (webhook, manual approval, resync) race or retry against the
        # same subscription. Partial index: subscription_id is nullable for
        # non-subscription contexts, so only enforce uniqueness when set.
        # NOTE (Postgres-specific): if this project targets SQLite in dev,
        # the postgresql_where clause is ignored there (SQLite has no
        # partial-index syntax support in this form) — the app-level guard
        # in discount_service.redeem_discount() is the portable enforcement;
        # this index is the belt-and-suspenders DB-level backstop for prod.
        db.Index(
            'uq_discount_redemptions_subscription',
            'subscription_id',
            unique=True,
            postgresql_where=db.text('subscription_id IS NOT NULL'),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey('discount_campaigns.id', ondelete='CASCADE'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True)

    amount_before = db.Column(db.Numeric(10, 2), nullable=False)
    amount_discounted = db.Column(db.Numeric(10, 2), nullable=False)
    amount_after = db.Column(db.Numeric(10, 2), nullable=False)

    billing_cycle = db.Column(db.String(20), nullable=True)
    redeemed_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    campaign = db.relationship('DiscountCampaign', back_populates='redemptions', lazy='joined')
    tenant = db.relationship('Tenant', lazy='joined')

    def __repr__(self):
        return f'<DiscountRedemption campaign={self.campaign_id} tenant={self.tenant_id}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: Invoice
# ═════════════════════════════════════════════════════════════════════════════

class Invoice(db.Model):
    """
    Immutable accounting record, one per successful activation (manual
    approval, PayMongo webhook, or superadmin resync — the same three
    call sites that call discount_checkout.apply_on_activation()).

    IMMUTABILITY CONTRACT: once issued (status='issued'), the financial
    columns (amount_subtotal/amount_discount/amount_tax/amount_total,
    invoice_number, currency) must never be UPDATEd by application code.
    Corrections go through void_invoice() (status='void' + void_reason),
    never a mutation of an issued row — this is a real accounting
    requirement, not a style preference, and it's enforced only at the
    service layer (app/services/billing/invoice_service.py) by omitting
    any update path for those columns. There is no DB-level trigger
    enforcing this; don't write raw UPDATE statements against this table.

    invoice_number is sequential per the Postgres sequence created in
    migration 0040 (invoice_number_seq) — NOT a random/hex ID like
    Subscription's legacy license-key generator. Sequential numbering is
    a real bookkeeping/audit requirement; SQLite dev/test environments
    fall back to a non-atomic max()+1 (see invoice_service.py — dev-only,
    never use under concurrent writers).
    """
    __tablename__ = 'invoices'
    __table_args__ = (
        db.Index('ix_invoices_tenant_issued', 'tenant_id', 'issued_at'),
        # Idempotency backstop: a webhook retry or a superadmin resync
        # hitting the same subscription+payment_reference must not mint a
        # second invoice. Partial (payment_reference nullable for edge
        # cases like force-activation with no real payment). Postgres-only
        # for the same reason as uq_discount_redemptions_subscription —
        # the service-layer guard in invoice_service.record_invoice() is
        # the portable enforcement across all engines.
        db.Index(
            'uq_invoices_subscription_payment_ref',
            'subscription_id', 'payment_reference',
            unique=True,
            postgresql_where=db.text('payment_reference IS NOT NULL'),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(30), nullable=False, unique=True, index=True)

    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True, index=True)
    discount_redemption_id = db.Column(db.Integer, db.ForeignKey('discount_redemptions.id', ondelete='SET NULL'), nullable=True)

    plan = db.Column(db.String(50), nullable=False)
    billing_cycle = db.Column(db.String(20), nullable=False, default='monthly')

    # Accounting breakdown. amount_total = amount_subtotal - amount_discount + amount_tax.
    # Computed once at issuance time and frozen — never recomputed on read.
    amount_subtotal = db.Column(db.Numeric(10, 2), nullable=False)
    amount_discount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    tax_rate         = db.Column(db.Numeric(5, 4), nullable=False, default=0)  # e.g. 0.1200 = 12%
    amount_tax       = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    amount_total     = db.Column(db.Numeric(10, 2), nullable=False)

    currency = db.Column(db.String(10), nullable=False, default='USD')
    coupon_code = db.Column(db.String(100), nullable=True)

    payment_method   = db.Column(db.String(100), nullable=False, default='')
    payment_provider = db.Column(db.String(30),  nullable=False, default='')  # 'paymongo' | 'manual'
    payment_reference = db.Column(db.String(255), nullable=True)

    status     = db.Column(db.String(20), nullable=False, default='issued')  # 'issued' | 'void'
    issued_at  = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    voided_at  = db.Column(db.DateTime(timezone=True), nullable=True)
    void_reason = db.Column(db.Text, nullable=True)

    notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)

    tenant = db.relationship('Tenant', lazy='joined')
    subscription = db.relationship('Subscription', lazy='joined')
    discount_redemption = db.relationship('DiscountRedemption', lazy='joined')

    def __repr__(self):
        return f'<Invoice {self.invoice_number} tenant={self.tenant_id} total={self.amount_total}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: WebhookEvent
# ═════════════════════════════════════════════════════════════════════════════

class WebhookEvent(db.Model):
    """Idempotency log for PayMongo webhook events."""
    __tablename__ = 'webhook_events'
    __table_args__ = (
        db.Index('ix_webhook_events_type_received', 'event_type', 'received_at'),
    )

    id              = db.Column(db.Integer, primary_key=True)
    event_id        = db.Column(db.String(255), nullable=False, unique=True, index=True)
    event_type      = db.Column(db.String(100), nullable=False)
    tenant_id       = db.Column(db.Integer, nullable=True, index=True)
    payload_summary = db.Column(db.String(500), default='')
    processed       = db.Column(db.Boolean, nullable=False, default=False)
    received_at     = db.Column(db.DateTime(timezone=True), default=_utcnow)

    def __repr__(self):
        return f'<WebhookEvent {self.event_type} {self.event_id}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: PaymentMethod
# ═════════════════════════════════════════════════════════════════════════════

PAYMENT_METHOD_TYPES = ('ewallet', 'bank', 'paymongo', 'crypto')
PAYMENT_METHOD_ICONS = {
    'ewallet': 'lucide:smartphone',
    'bank':    'lucide:landmark',
    'paymongo':'lucide:zap',
    'crypto':  'lucide:bitcoin',
}


class PaymentMethod(db.Model):
    """Configurable payment method managed by superadmin."""
    __tablename__ = 'payment_methods'
    __table_args__ = (
        db.Index('ix_payment_methods_tenant_active', 'tenant_id', 'is_active'),
        db.Index('ix_payment_methods_display_order', 'display_order'),
    )

    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True, index=True)
    name           = db.Column(db.String(120), nullable=False, default='')
    method_type    = db.Column(db.String(30),  nullable=False, default='ewallet')
    is_active      = db.Column(db.Boolean, nullable=False, default=True)
    is_default     = db.Column(db.Boolean, nullable=False, default=False)
    instructions   = db.Column(db.Text, default='')
    qr_image       = db.Column(db.String(255), default='')
    account_name   = db.Column(db.String(120), default='')
    account_number = db.Column(db.String(120), default='')
    mobile_number  = db.Column(db.String(50),  default='')
    bank_name      = db.Column(db.String(120), default='')
    notes          = db.Column(db.Text, default='')
    display_order  = db.Column(db.Integer, nullable=False, default=0)
    created_at     = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at     = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant      = db.relationship('Tenant', back_populates='payment_methods', lazy='joined')
    submissions = db.relationship('PaymentSubmission', back_populates='payment_method_ref', lazy='dynamic')

    @property
    def is_global(self) -> bool:
        return self.tenant_id is None

    @property
    def scope(self) -> str:
        return 'Global' if self.tenant_id is None else (self.tenant.slug if self.tenant else str(self.tenant_id))

    @property
    def icon(self) -> str:
        return PAYMENT_METHOD_ICONS.get(self.method_type, 'lucide:credit-card')

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<PaymentMethod {self.scope} {self.name}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: PaymentInstruction (deprecated alias, kept for migrations)
# ═════════════════════════════════════════════════════════════════════════════

class PaymentInstruction(db.Model):
    """Deprecated alias table — kept for backward-compatible migrations."""
    __tablename__ = 'payment_instructions'
    __table_args__ = (
        db.Index('ix_payment_instructions_tenant_active', 'tenant_id', 'is_active'),
    )

    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True, index=True)
    method         = db.Column(db.String(50),  nullable=False, default='')
    title          = db.Column(db.String(120), nullable=False, default='')
    description    = db.Column(db.Text, default='')
    account_name   = db.Column(db.String(120), default='')
    account_number = db.Column(db.String(120), default='')
    bank_name      = db.Column(db.String(120), default='')
    qr_image       = db.Column(db.String(255), default='')
    is_active      = db.Column(db.Boolean, nullable=False, default=True)
    created_at     = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at     = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant = db.relationship('Tenant', back_populates='payment_instructions', lazy='joined')

    @property
    def is_global(self) -> bool:
        return self.tenant_id is None

    def __repr__(self):
        return f'<PaymentInstruction {self.method}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: PaymentSubmission
# ═════════════════════════════════════════════════════════════════════════════

class PaymentSubmission(db.Model):
    """Proof-of-payment record submitted by a tenant."""
    __tablename__ = 'payment_submissions'
    __table_args__ = (
        db.Index('ix_payment_submissions_status_submitted_at', 'status', 'submitted_at'),
        db.Index('ix_payment_submissions_tenant_status', 'tenant_id', 'status'),
    )

    id                = db.Column(db.Integer, primary_key=True)
    tenant_id         = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False, index=True)
    subscription_id   = db.Column(db.Integer, db.ForeignKey('subscriptions.id'), nullable=True, index=True)
    payment_method_id = db.Column(db.Integer, db.ForeignKey('payment_methods.id'), nullable=True, index=True)
    plan              = db.Column(db.String(50), nullable=False, default='Basic')
    amount_paid       = db.Column(db.Float, default=0.0)
    amount_usd        = db.Column(db.Float, nullable=True)
    currency_code     = db.Column(db.String(3), nullable=False, default='USD', server_default='USD')
    exchange_rate     = db.Column(db.Numeric(18, 8), nullable=True)
    country_code      = db.Column(db.String(2), nullable=True)
    # FIX [MED-COUPON-01]: system-computed reference price at submission time
    # (list price for `plan`/billing_cycle minus any validated coupon),
    # captured via discount_checkout.quote_for_context() in
    # manual_billing.submit_manual_payment(). Nullable because historical
    # rows predating this column have no reliable value to backfill —
    # review UI must treat expected_amount is None as "no reference
    # available", not "expected zero".
    expected_amount      = db.Column(db.Float, nullable=True)
    coupon_code_applied  = db.Column(db.String(50), nullable=True)
    payment_method    = db.Column(db.String(100), default='')
    payment_reference = db.Column(db.String(255), default='')
    payment_proof     = db.Column(db.String(255), default='')
    note              = db.Column(db.Text, default='')
    status            = db.Column(db.String(30), nullable=False, default='pending')
    submitted_at      = db.Column(db.DateTime(timezone=True), default=_utcnow)
    reviewed_at       = db.Column(db.DateTime(timezone=True), nullable=True)
    reviewed_by       = db.Column(db.String(120), default='')
    review_notes      = db.Column(db.Text, default='')

    tenant             = db.relationship('Tenant',       back_populates='payments',    lazy='joined')
    subscription       = db.relationship('Subscription', lazy='joined', foreign_keys=[subscription_id])
    payment_method_ref = db.relationship('PaymentMethod', back_populates='submissions', lazy='joined', foreign_keys=[payment_method_id])

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, Decimal):
                d[k] = float(v)
        return d

    def __repr__(self):
        return f'<PaymentSubmission {self.tenant_id} {self.status}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: PlatformSetting
# ═════════════════════════════════════════════════════════════════════════════

class PlatformSetting(db.Model):
    """Key-value platform settings managed by superadmin."""
    __tablename__ = 'platform_settings'

    key        = db.Column(db.String(100), primary_key=True)
    # TEXT (not VARCHAR(500)) as of migration 0044 — holds JSON-encoded
    # structured settings (see get_json/set_json) in addition to plain
    # strings. Model must match DB type or Alembic autogenerate will flag
    # drift on the next `alembic revision --autogenerate`.
    value      = db.Column(db.Text, nullable=False, default='')
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @classmethod
    def get_bool(cls, key: str, default: bool | None = None) -> bool | None:
        row = db.session.get(cls, key)
        if row is None or row.value == '':
            return default
        return row.value.strip().lower() in ('1', 'true', 'yes', 'on')

    @classmethod
    def set_bool(cls, key: str, value: bool) -> None:
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value      = 'true' if value else 'false'
        row.updated_at = _utcnow()

    @classmethod
    def get_float(cls, key: str, default: float | None = None) -> float | None:
        row = db.session.get(cls, key)
        if row is None or row.value == '':
            return default
        try:
            return float(row.value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def set_float(cls, key: str, value: float) -> None:
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value      = str(float(value))
        row.updated_at = _utcnow()

    @classmethod
    def get_string(cls, key: str, default: str | None = None) -> str | None:
        row = db.session.get(cls, key)
        if row is None:
            return default
        return row.value or default

    @classmethod
    def set_string(cls, key: str, value: str) -> None:
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value      = value or ''
        row.updated_at = _utcnow()

    # ── JSON helpers (v6.7 — Pricing CMS + any future structured settings) ──
    #
    # `value` is TEXT (widened from VARCHAR(500) in migration
    # 0044_widen_platform_setting_value) so this safely stores small JSON
    # blobs (per-plan pricing overrides, feature lists, etc.) without a new
    # table. A hard serialized-size cap is enforced app-side regardless of
    # the column type, since this is reachable from superadmin form input
    # and unbounded growth here is still a (low-severity, admin-only) DoS
    # surface worth capping defensively.

    _JSON_MAX_BYTES = 8_000

    @classmethod
    def get_json(cls, key: str, default=None):
        row = db.session.get(cls, key)
        if row is None or not row.value:
            return default
        try:
            return json.loads(row.value)
        except (TypeError, ValueError):
            _logging.getLogger(__name__).warning(
                "PlatformSetting.get_json: corrupt JSON for key=%s", key
            )
            return default

    @classmethod
    def set_json(cls, key: str, value) -> None:
        serialized = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
        if len(serialized.encode('utf-8')) > cls._JSON_MAX_BYTES:
            raise ValueError(
                f"PlatformSetting.set_json: value for key={key!r} exceeds "
                f"{cls._JSON_MAX_BYTES} byte cap ({len(serialized.encode('utf-8'))} bytes)."
            )
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key)
            db.session.add(row)
        row.value      = serialized
        row.updated_at = _utcnow()


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: TenantCommunicationSettings
# ═════════════════════════════════════════════════════════════════════════════

class TenantCommunicationSettings(db.Model):
    """Per-tenant email / Web3Forms / SMTP credentials (Fernet-encrypted secrets)."""
    __tablename__ = 'tenant_communication_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_comm_settings'),
        db.Index('ix_tenant_comm_slug', 'tenant_slug'),
    )

    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    tenant_slug = db.Column(db.String(120), nullable=False, index=True)

    _web3forms_key      = db.Column('web3forms_key', db.Text, default='')
    mail_username       = db.Column(db.String(200), default='')
    _mail_password      = db.Column('mail_password', db.Text, default='')
    mail_default_sender = db.Column(db.String(200), default='')
    admin_email         = db.Column(db.String(200), default='')
    smtp_host           = db.Column(db.String(200), default='')
    smtp_port           = db.Column(db.Integer, default=587)
    smtp_tls            = db.Column(db.Boolean, default=True)
    created_at          = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at          = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant = db.relationship('Tenant', backref=db.backref(
        'communication_settings', uselist=False, cascade='all, delete-orphan',
        passive_deletes=True,
    ))

    @property
    def web3forms_key(self) -> str:
        return decrypt_secret(self._web3forms_key)

    @web3forms_key.setter
    def web3forms_key(self, value: str):
        self._web3forms_key = encrypt_secret(value) if value else ''

    @property
    def mail_password(self) -> str:
        return decrypt_secret(self._mail_password)

    @mail_password.setter
    def mail_password(self, value: str):
        self._mail_password = encrypt_secret(value) if value else ''

    @property
    def has_web3forms(self) -> bool:
        return len(str(self._web3forms_key or '')) > 0
        return (
            len(str(self.smtp_host or '')) > 0
            and len(str(self.mail_username or '')) > 0
            and len(str(self.mail_password or '')) > 0
        )

    @property
    def is_configured(self) -> bool:
        return self.has_web3forms or self.has_smtp

    def effective_web3forms_key(self, app_config: dict) -> str:
        return self.web3forms_key or app_config.get('WEB3FORMS_ACCESS_KEY', '')

    def effective_smtp_config(self, app_config: dict) -> dict:
        return {
            'host':     self.smtp_host     or app_config.get('MAIL_SERVER', ''),
            'port':     self.smtp_port     or int(app_config.get('MAIL_PORT', 587)),
            'tls':      self.smtp_tls,
            'username': self.mail_username or app_config.get('MAIL_USERNAME', ''),
            'password': self.mail_password or app_config.get('MAIL_PASSWORD', ''),
            'sender':   self.mail_default_sender or app_config.get('MAIL_DEFAULT_SENDER', ''),
            'admin':    self.admin_email   or app_config.get('ADMIN_EMAIL', ''),
        }

    @classmethod
    def get_or_create(cls, tenant_id: int, tenant_slug: str) -> 'TenantCommunicationSettings':
        obj = cls.query.filter_by(tenant_id=tenant_id).first()
        if not obj:
            obj = cls(tenant_id=tenant_id, tenant_slug=tenant_slug)
            db.session.add(obj)
            db.session.flush()
        return obj

    def __repr__(self):
        return f'<TenantCommSettings tenant={self.tenant_slug!r}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: PasswordResetOTP
# ═════════════════════════════════════════════════════════════════════════════

class PasswordResetOTP(db.Model):
    """Hashed OTP records for all password-reset flows."""
    __tablename__ = 'password_reset_otps'
    __table_args__ = (
        db.Index('ix_otp_user_type_user_id', 'user_type', 'user_id'),
        db.Index('ix_otp_tenant_id', 'tenant_id'),
        db.Index('ix_otp_expires_at', 'expires_at'),
    )

    id         = db.Column(db.Integer, primary_key=True)
    user_type  = db.Column(db.String(20), nullable=False)
    user_id    = db.Column(db.Integer,   nullable=False)
    tenant_id  = db.Column(db.Integer,   nullable=True)
    email      = db.Column(db.String(120), nullable=False)
    otp_hash   = db.Column(db.String(64), nullable=False)
    attempts   = db.Column(db.Integer, default=0, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used       = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)

    @staticmethod
    def hash_otp(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def verify(self, raw: str) -> bool:
        return self.otp_hash == hashlib.sha256(raw.encode()).hexdigest()

    @property
    def is_expired(self) -> bool:
        exp = ensure_utc_aware(self.expires_at)
        return True if exp is None else utc_now() >= exp

    @property
    def is_valid(self) -> bool:
        return not self.used and not self.is_expired and self.attempts < 5

    @classmethod
    def purge_old(cls, user_type: str, user_id: int) -> None:
        cls.query.filter_by(user_type=user_type, user_id=user_id).delete()

    def __repr__(self):
        return f'<PasswordResetOTP {self.user_type}/{self.user_id} used={self.used}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: GlobalEmailConfig
# ═════════════════════════════════════════════════════════════════════════════

class GlobalEmailConfig(db.Model):
    """Singleton DB row for superadmin-managed email / OTP configuration."""
    __tablename__ = 'global_email_config'

    id                    = db.Column(db.Integer, primary_key=True)
    _web3forms_key        = db.Column('web3forms_key', db.Text, default='')
    _resend_api_key       = db.Column('resend_api_key', db.Text, default='', nullable=True)
    _mailersend_api_key   = db.Column('mailersend_api_key', db.Text, default='', nullable=True)
    sender_name           = db.Column(db.String(200), default='Portfolio CMS')
    sender_email          = db.Column(db.String(200), default='')
    otp_expiry_minutes    = db.Column(db.Integer, default=10, nullable=False)
    recovery_enabled      = db.Column(db.Boolean, default=True, nullable=False)
    updated_at            = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    updated_by            = db.Column(db.String(120), nullable=True)

    @property
    def web3forms_key(self) -> str:
        return decrypt_secret(self._web3forms_key)

    @web3forms_key.setter
    def web3forms_key(self, value: str):
        self._web3forms_key = encrypt_secret(value) if value else ''

    @property
    def has_web3forms(self) -> bool:
        return len(str(self._web3forms_key or '')) > 0

    # ── MailerSend (v5.0 primary provider) ──────────────────────────────────

    @property
    def mailersend_api_key(self) -> str:
        return decrypt_secret(self._mailersend_api_key) if (self._mailersend_api_key is not None and self._mailersend_api_key != '') else ''

    @mailersend_api_key.setter
    def mailersend_api_key(self, value: str):
        self._mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_mailersend(self) -> bool:
        return len(str(self._mailersend_api_key or '')) > 0

    # ── Resend shim (deprecated — column retained for zero-downtime migration) ──

    @property
    def resend_api_key(self) -> str:
        """Deprecated. Returns '' — Resend removed in v5.0."""
        return ''

    @resend_api_key.setter
    def resend_api_key(self, value: str):
        """Deprecated no-op setter. Use mailersend_api_key instead."""
        pass

    @property
    def has_resend(self) -> bool:
        """Deprecated. Always False — Resend removed in v5.0."""
        return False

    @classmethod
    def get(cls, *, fresh: bool = False):
        """Return the singleton GlobalEmailConfig row (id=1).

        Parameters
        ----------
        fresh : bool
            When True (or when called from a status/save endpoint that must
            reflect the just-committed state) the SQLAlchemy identity map is
            bypassed entirely by issuing a SELECT … with populate_existing=True
            after removing the current session.  This prevents the stale-ORM
            cache bug where `has_sa_smtp` evaluates False immediately after a
            successful commit because the session still holds the pre-commit
            InstrumentedAttribute values.
        """
        from sqlalchemy.exc import IntegrityError, OperationalError

        # Failsafe: if schema is out of sync (missing columns), attempt an
        # in-process patch before crashing. This covers the case where the
        # startup patcher ran before the app context was fully established,
        # or where a new column was added without restarting the process.
        try:
            if fresh:
                # Completely discard the current session so SQLAlchemy is
                # forced to issue a real SELECT rather than returning the
                # cached identity-map object.
                db.session.remove()
                config = (
                    cls.query
                    .execution_options(populate_existing=True)
                    .filter_by(id=1)
                    .first()
                )
            else:
                config = db.session.get(cls, 1)
        except OperationalError as exc:
            import logging as _logging
            _log = _logging.getLogger(__name__)
            _log.warning(
                '[DB FAILSAFE] GlobalEmailConfig.get() hit OperationalError (%s). '
                'Attempting inline schema patch...', exc
            )
            db.session.rollback()
            try:
                from flask import current_app
                with current_app.app_context():
                    from app import _ensure_global_email_config_columns
                    _ensure_global_email_config_columns()
            except Exception as patch_exc:
                _log.error('[DB FAILSAFE] Inline schema patch failed: %s', patch_exc)
            config = db.session.get(cls, 1)

        if config is None:
            # Serialize creation to avoid SQLite connection/parameter issues
            with _global_email_config_lock:
                # Double-check after acquiring lock
                config = db.session.get(cls, 1)
                if config is None:
                    try:
                        # Use a raw INSERT OR IGNORE to avoid ORM/session parameter
                        # races on SQLite when multiple threads attempt to create
                        # the same singleton concurrently.
                        from sqlalchemy import text
                        bind = db.session.get_bind()
                        stmt = text(
                            "INSERT OR IGNORE INTO global_email_config (id, sender_name, otp_expiry_minutes, recovery_enabled) VALUES (:id, :sender_name, :otp, :recovery)"
                        )
                        # Use a transactional connection to ensure the INSERT is
                        # committed and visible to other sessions/threads.
                        try:
                            with bind.begin() as conn:
                                conn.execute(stmt, {"id": 1, "sender_name": "Portfolio CMS", "otp": 10, "recovery": 1})
                        except Exception:
                            # Some DB backends may not expose begin() on the bind
                            # object (fallback to execute on the bind itself).
                            bind.execute(stmt, {"id": 1, "sender_name": "Portfolio CMS", "otp": 10, "recovery": 1})
                        # Ensure visibility in this session
                        db.session.remove()
                        config = (
                            db.session.query(cls)
                            .execution_options(populate_existing=True)
                            .filter_by(id=1)
                            .first()
                        )
                        if config is None:
                            import logging as _logging
                            _logging.getLogger(__name__).critical(
                                'GlobalEmailConfig race condition: failed to create/retrieve singleton after raw insert'
                            )
                            raise
                    except Exception:
                        # Fallback to ORM path if raw insert fails for any reason
                        try:
                            config = cls(
                                id=1,
                                sender_name="Portfolio CMS",
                                otp_expiry_minutes=10,
                                recovery_enabled=True
                            )
                            db.session.add(config)
                            db.session.commit()
                        except IntegrityError:
                            db.session.rollback()
                            db.session.remove()
                            config = (
                                db.session.query(cls)
                                .execution_options(populate_existing=True)
                                .filter_by(id=1)
                                .first()
                            )
                            if config is None:
                                import logging as _logging
                                _logging.getLogger(__name__).critical(
                                    'GlobalEmailConfig race condition: failed to create/retrieve singleton after fallback'
                                )
                                raise

        return config

    def effective_web3forms_key(self, app_config: dict) -> str:
        return self.web3forms_key or app_config.get('WEB3FORMS_ACCESS_KEY', '')

    # ── Per-portal MailerSend (Task 4 — v5.6) ────────────────────────────────
    # Admin Portal credentials (fallback: ADMIN_MAILERSEND_API_KEY env var)
    _admin_mailersend_api_key = db.Column(
        'admin_mailersend_api_key', db.Text, default='', nullable=True
    )
    admin_sender_name  = db.Column(db.String(200), default='', nullable=True)
    admin_sender_email = db.Column(db.String(200), default='', nullable=True)

    # Superadmin Portal credentials (fallback: SUPERADMIN_MAILERSEND_API_KEY env var)
    _superadmin_mailersend_api_key = db.Column(
        'superadmin_mailersend_api_key', db.Text, default='', nullable=True
    )
    superadmin_sender_name  = db.Column(db.String(200), default='', nullable=True)
    superadmin_sender_email = db.Column(db.String(200), default='', nullable=True)

    @property
    def admin_mailersend_api_key(self) -> str:
        return decrypt_secret(self._admin_mailersend_api_key) if (self._admin_mailersend_api_key is not None and self._admin_mailersend_api_key != '') else ''

    @admin_mailersend_api_key.setter
    def admin_mailersend_api_key(self, value: str):
        self._admin_mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_admin_mailersend(self) -> bool:
        return len(str(self._admin_mailersend_api_key or '')) > 0

    @property
    def superadmin_mailersend_api_key(self) -> str:
        return decrypt_secret(self._superadmin_mailersend_api_key) if (self._superadmin_mailersend_api_key is not None and self._superadmin_mailersend_api_key != '') else ''

    @superadmin_mailersend_api_key.setter
    def superadmin_mailersend_api_key(self, value: str):
        self._superadmin_mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_superadmin_mailersend(self) -> bool:
        """Raw blob check — avoids decrypt failures giving false-negative configured state."""
        try:
            raw_key = self._superadmin_mailersend_api_key
            if raw_key is None:
                raw_key = ''
            raw_key = str(raw_key).strip()
            sender  = str(self.superadmin_sender_email or '').strip()
            # Key alone is sufficient — sender falls back to shared sender_email
            return bool(raw_key)
        except Exception:
            return False

    def get_portal_key(self, portal: str) -> str:
        """
        Resolve the MailerSend API key for the given portal.
        portal: 'superadmin' | 'admin' | 'tenant' (default/shared)
        Priority: DB per-portal key → DB shared key → env per-portal → env shared
        """
        import os
        if portal == 'superadmin':
            return (
                self.superadmin_mailersend_api_key
                or self.mailersend_api_key
                or os.environ.get('SUPERADMIN_MAILERSEND_API_KEY', '')
                or os.environ.get('MAILERSEND_API_KEY', '')
            )
        if portal == 'admin':
            return (
                self.admin_mailersend_api_key
                or self.mailersend_api_key
                or os.environ.get('ADMIN_MAILERSEND_API_KEY', '')
                or os.environ.get('MAILERSEND_API_KEY', '')
            )
        # tenant / default
        return self.mailersend_api_key or os.environ.get('MAILERSEND_API_KEY', '')

    def get_portal_sender_email(self, portal: str) -> str:
        import os
        if portal == 'superadmin':
            return (
                self.superadmin_sender_email
                or self.sender_email
                or os.environ.get('SUPERADMIN_MAIL_FROM', '')
                or os.environ.get('MAILERSEND_FROM_EMAIL', 'noreply@portfoliocms.app')
            )
        if portal == 'admin':
            return (
                self.admin_sender_email
                or self.sender_email
                or os.environ.get('ADMIN_MAIL_FROM', '')
                or os.environ.get('MAILERSEND_FROM_EMAIL', 'noreply@portfoliocms.app')
            )
        return self.sender_email or os.environ.get('MAILERSEND_FROM_EMAIL', 'noreply@portfoliocms.app')

    def get_portal_sender_name(self, portal: str) -> str:
        import os
        if portal == 'superadmin':
            return (
                self.superadmin_sender_name
                or self.sender_name
                or os.environ.get('SUPERADMIN_MAIL_NAME', '')
                or os.environ.get('MAILERSEND_FROM_NAME', 'Portfolio CMS')
            )
        if portal == 'admin':
            return (
                self.admin_sender_name
                or self.sender_name
                or os.environ.get('ADMIN_MAIL_NAME', '')
                or os.environ.get('MAILERSEND_FROM_NAME', 'Portfolio CMS')
            )
        return self.sender_name or os.environ.get('MAILERSEND_FROM_NAME', 'Portfolio CMS')

    def __repr__(self):
        return f'<GlobalEmailConfig mailersend={"✓" if self.has_mailersend else "✗"}>'

    # ── Superadmin SMTP provider (0032) ──────────────────────────────────────
    sa_smtp_host               = db.Column(db.String(300), default='', nullable=True)
    sa_smtp_port               = db.Column(db.Integer, default=587, nullable=True)
    sa_smtp_username           = db.Column(db.String(300), default='', nullable=True)
    _sa_smtp_password          = db.Column('sa_smtp_password_encrypted', db.Text, nullable=True)
    sa_smtp_sender_email       = db.Column(db.String(300), default='', nullable=True)
    sa_smtp_sender_name        = db.Column(db.String(200), default='', nullable=True)
    sa_smtp_encryption         = db.Column(db.String(20), default='tls', nullable=True)
    sa_smtp_active             = db.Column(db.Boolean, default=False, nullable=True)

    # ── Superadmin Resend provider (0032) ────────────────────────────────────
    _sa_resend_api_key         = db.Column('sa_resend_api_key_encrypted', db.Text, default='', nullable=True)
    sa_resend_sender_email     = db.Column(db.String(300), default='', nullable=True)
    sa_resend_sender_name      = db.Column(db.String(200), default='', nullable=True)
    sa_resend_active           = db.Column(db.Boolean, default=False, nullable=True)

    # ── Superadmin MailerSend toggle + priority (0032) ───────────────────────
    sa_mailersend_active       = db.Column(db.Boolean, default=True, nullable=True)
    sa_provider_priority       = db.Column(db.String(200), default='["mailersend","smtp","resend"]', nullable=True)

    @property
    def sa_smtp_password(self) -> str:
        # Force scalar string — _sa_smtp_password can be an ORM InstrumentedAttribute
        # descriptor if the instance hasn't been refreshed yet, which causes
        # decrypt_secret to receive a Column object and raise AttributeError.
        raw = self._sa_smtp_password
        if raw is None or raw == '' or not isinstance(raw, str):
            return ''
        return decrypt_secret(raw)

    @sa_smtp_password.setter
    def sa_smtp_password(self, value: str):
        self._sa_smtp_password = encrypt_secret(value) if value else ''

    @property
    def has_sa_smtp(self) -> bool:
        """
        Determine whether SuperAdmin SMTP is fully configured.

        Checks the raw encrypted blob directly — never goes through
        decrypt_secret — because decryption can fail after key rotation
        or when the ORM instance is accessed before a session refresh,
        which would produce a false-negative 'not configured' state.

        A provider is considered configured when ALL of:
          • host is set
          • username is set
          • encrypted password blob exists (non-empty string)
          • sender_email is set
          • port is a valid integer > 0
        """
        try:
            host    = str(self.sa_smtp_host    or '').strip()
            user    = str(self.sa_smtp_username or '').strip()
            sender  = str(self.sa_smtp_sender_email or '').strip()
            port    = self.sa_smtp_port

            # Read raw encrypted blob — avoid decrypt path entirely
            raw_pw = self._sa_smtp_password
            if raw_pw is None:
                raw_pw = ''
            raw_pw = str(raw_pw).strip()

            return bool(
                host
                and user
                and raw_pw
                and sender
                and port
                and int(port) > 0
            )
        except Exception:
            return False

    @property
    def sa_resend_api_key(self) -> str:
        raw = self._sa_resend_api_key
        if raw is None or raw == '' or not isinstance(raw, str):
            return ''
        return decrypt_secret(raw)

    @sa_resend_api_key.setter
    def sa_resend_api_key(self, value: str):
        self._sa_resend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_sa_resend(self) -> bool:
        """Check raw encrypted blob to avoid decrypt failures giving false-negative."""
        try:
            raw_key = self._sa_resend_api_key
            if raw_key is None:
                raw_key = ''
            raw_key = str(raw_key).strip()
            sender  = str(self.sa_resend_sender_email or '').strip()
            return bool(raw_key and sender)
        except Exception:
            return False

    def get_sa_provider_priority(self) -> list:
        """Return ordered provider list, defaulting to ['mailersend','smtp','resend']."""
        import json, logging
        from app.services.email.constants import VALID_EMAIL_PROVIDERS, DEFAULT_PROVIDER_PRIORITY

        logger = logging.getLogger(__name__)
        try:
            raw = self.sa_provider_priority or '[]'
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError('sa_provider_priority not a list')
        except Exception:
            # Malformed JSON or unexpected type — recover to default
            parsed = []

        # Filter to known providers, preserve order, dedupe
        seen = set()
        filtered = []
        valid_set = set(VALID_EMAIL_PROVIDERS)
        for p in parsed:
            try:
                pname = str(p).strip()
            except Exception:
                continue
            if pname in valid_set and pname not in seen:
                filtered.append(pname)
                seen.add(pname)

        repaired = False
        if not filtered:
            # Empty or no valid providers -> use system default
            filtered = list(DEFAULT_PROVIDER_PRIORITY)
            repaired = True

        # If parsed differs (e.g., malformed entries removed), persist repair
        try:
            if not repaired and json.dumps(parsed) != json.dumps(filtered):
                repaired = True
        except Exception:
            repaired = True

        if repaired:
            try:
                self.sa_provider_priority = json.dumps(filtered)
                # Best-effort persist so callers (including non-request code) see repaired state
                db.session.add(self)
                db.session.commit()
                logger.warning('Repaired corrupted GlobalEmailConfig.sa_provider_priority to defaults')
            except Exception:
                logger.exception('Failed to persist repaired sa_provider_priority')

        return filtered

    def set_sa_provider_priority(self, order: list):
        import json
        from app.services.email.constants import VALID_EMAIL_PROVIDERS

        if not isinstance(order, list):
            raise ValueError('order must be a list')

        valid_set = set(VALID_EMAIL_PROVIDERS)
        seen = set()
        filtered = []
        for p in order:
            pname = str(p).strip()
            if pname in valid_set and pname not in seen:
                filtered.append(pname)
                seen.add(pname)

        if not filtered:
            raise ValueError('priority order must include at least one valid provider')

        self.sa_provider_priority = json.dumps(filtered)


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: Inquiry  (superadmin↔tenant messaging hub)
# ═════════════════════════════════════════════════════════════════════════════

class Inquiry(db.Model):
    """
    Contact-form submissions AND superadmin→tenant messages.
    Lives in core_db so superadmin can query across all tenants.

    DESIGN DECISION: Inquiry messages could reasonably go in either DB.
    They live in core_db because:
      1. Superadmin reads ALL tenants' inquiries from a single query.
      2. Contact form submissions are platform-level audit artifacts.
      3. Avoids cross-DB JOIN for superadmin messaging dashboard.
    """
    __tablename__ = 'inquiries'
    __table_args__ = (
        db.Index('ix_inquiries_tenant_sender_read', 'tenant_slug', 'sender', 'is_read'),
        db.Index('ix_inquiries_updated_at', 'updated_at'),
        db.Index('ix_inquiries_tenant_id', 'tenant_id'),
    )

    id         = db.Column(db.Integer, primary_key=True)
    tenant_id  = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)
    tenant_slug= db.Column(db.String(120), nullable=True)
    name       = db.Column(db.String(120), nullable=False)
    email      = db.Column(db.String(120), nullable=False)
    subject    = db.Column(db.String(200), default='')
    message    = db.Column(db.Text, nullable=False)
    phone      = db.Column(db.String(50), nullable=True)
    company    = db.Column(db.String(200), nullable=True)
    admin_notified = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    auto_reply_sent = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    spam_score = db.Column(db.Float, default=0.0, nullable=False, server_default='0')
    is_spam    = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    ip_address = db.Column(db.String(45), nullable=True)
    sender     = db.Column(db.String(50), nullable=False, default='visitor', server_default='visitor')
    is_read    = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    thread_unread_tenant = db.Column(db.Integer, default=0, nullable=False, server_default='0')
    thread_unread_super  = db.Column(db.Integer, default=0, nullable=False, server_default='0')
    created_at           = db.Column(db.DateTime(timezone=True), default=_utcnow)

    # ── Delivery tracking (v5.2) ──────────────────────────────────────────────
    # Nullable so existing rows remain compatible (no backfill needed)
    user_agent       = db.Column(db.String(500),  nullable=True)       # submitter UA string
    submission_id    = db.Column(db.String(80),   nullable=True)       # idempotency key from contact form
    provider_used    = db.Column(db.String(30),   nullable=True)       # 'basin'|'email_only'|'email'|'internal'
    delivery_status  = db.Column(db.String(20),   nullable=True)       # 'delivered'|'failed'|'pending'|None
    delivery_error   = db.Column(db.String(500),  nullable=True)       # error detail on failure

    tenant = db.relationship('Tenant', backref=db.backref('inquiries', lazy='dynamic'))

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<Inquiry {self.name}>'


class InquiryReply(db.Model):
    """A single reply in an Inquiry thread."""
    __tablename__ = 'inquiry_replies'
    __table_args__ = (
        db.Index('ix_reply_inquiry_id', 'inquiry_id'),
        db.Index('ix_reply_tenant_slug', 'tenant_slug'),
        db.Index('ix_reply_direction_read', 'direction', 'is_read'),
    )

    id          = db.Column(db.Integer, primary_key=True)
    inquiry_id  = db.Column(db.Integer, db.ForeignKey('inquiries.id', ondelete='CASCADE'), nullable=False)
    tenant_slug = db.Column(db.String(120), nullable=False, index=True)
    direction   = db.Column(db.String(20), nullable=False)
    sender_name = db.Column(db.String(120), nullable=False)
    message     = db.Column(db.Text, nullable=False)
    is_read     = db.Column(db.Boolean, default=False, nullable=False)
    created_at  = db.Column(db.DateTime(timezone=True), default=_utcnow)

    inquiry = db.relationship(
        'Inquiry',
        backref=db.backref(
            'replies',
            lazy='dynamic',
            order_by='InquiryReply.created_at.asc()',
            cascade='all, delete-orphan',
            passive_deletes=True,
        ),
    )

    def __repr__(self):
        return f'<InquiryReply inquiry={self.inquiry_id} dir={self.direction}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: SubscriptionNotification
# ═════════════════════════════════════════════════════════════════════════════

class SubscriptionNotification(db.Model):
    """Automated and manual subscription-related notifications."""
    __tablename__ = 'subscription_notifications'
    __table_args__ = (
        db.Index('ix_sub_notif_tenant_read', 'tenant_id', 'is_read'),
        db.Index('ix_sub_notif_type', 'notification_type'),
    )

    id                  = db.Column(db.Integer, primary_key=True)
    tenant_id           = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False)
    subscription_id     = db.Column(db.Integer, db.ForeignKey('subscriptions.id', ondelete='SET NULL'), nullable=True)
    notification_type   = db.Column(db.String(50), nullable=False)
    title               = db.Column(db.String(200), nullable=False)
    message             = db.Column(db.Text, nullable=False)
    is_read             = db.Column(db.Boolean, default=False, nullable=False)
    sent_via_email      = db.Column(db.Boolean, default=False, nullable=False)
    sent_via_dashboard  = db.Column(db.Boolean, default=True, nullable=False)
    created_at          = db.Column(db.DateTime(timezone=True), default=_utcnow)
    read_at             = db.Column(db.DateTime(timezone=True), nullable=True)

    tenant       = db.relationship('Tenant', backref=db.backref('notifications', lazy='dynamic', cascade='all, delete-orphan'))
    subscription = db.relationship('Subscription', backref=db.backref('notifications', lazy='dynamic'))

    @classmethod
    def unread_count(cls, tenant_id: int) -> int:
        return cls.query.filter_by(tenant_id=tenant_id, is_read=False).count()

    @classmethod
    def for_tenant(cls, tenant_id: int, limit: int = 20):
        return (
            cls.query
            .filter_by(tenant_id=tenant_id)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = _utcnow()

    def __repr__(self):
        return f'<SubscriptionNotification [{self.notification_type}] tenant={self.tenant_id}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: ActivityLog  (cross-tenant audit trail)
# ═════════════════════════════════════════════════════════════════════════════

class ActivityLog(db.Model):
    """
    Audit trail for all administrative actions.
    Lives in core_db — superadmin queries span all tenants.
    user_id FKs to users table (also in core_db).
    """
    __tablename__ = 'activity_log'
    __table_args__ = (
        db.Index('ix_activitylog_created_at', 'created_at'),
        db.Index('ix_activitylog_tenant_action', 'tenant_slug', 'action'),
        db.Index('ix_activitylog_user_tenant', 'user_id', 'tenant_slug'),
    )

    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='SET NULL'), nullable=True, index=True)
    tenant_slug = db.Column(db.String(120), nullable=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    username    = db.Column(db.String(120), nullable=True)
    action      = db.Column(db.String(50),  nullable=False)
    entity_type = db.Column(db.String(50),  nullable=True)
    entity_name = db.Column(db.String(200), nullable=True)
    description = db.Column(db.String(500), nullable=True)
    ip_address  = db.Column(db.String(45),  nullable=True)
    created_at  = db.Column(db.DateTime(timezone=True), default=_utcnow)

    ACTION_ICONS = {
        'create':    '✨',
        'update':    '✏️',
        'delete':    '🗑️',
        'login':     '🔐',
        'logout':    '🔓',
        'publish':   '🚀',
        'unpublish': '📦',
        'export':    '💾',
        'security':  '🛡️',
    }

    def __repr__(self):
        return f'<ActivityLog {self.action} {self.entity_type}>'


# ═════════════════════════════════════════════════════════════════════════════
# EMAIL SERVICES v5.9 — Multi-Provider Tenant Email Infrastructure
# ═════════════════════════════════════════════════════════════════════════════

class TenantEmailProvider(db.Model):
    """
    Registry of active/inactive email providers per tenant with priority ordering.

    provider_name values: 'smtp' | 'resend' | 'mailersend'
    status values: 'connected' | 'disconnected' | 'invalid_credentials' |
                   'timeout' | 'rate_limited' | 'unconfigured'
    """
    __tablename__ = 'tenant_email_providers'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'provider_name', name='uq_tenant_email_provider'),
        db.Index('ix_tep_tenant_active', 'tenant_id', 'active'),
    )

    id                = db.Column(db.Integer, primary_key=True)
    tenant_id         = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    provider_name     = db.Column(db.String(50), nullable=False)      # smtp | resend | mailersend
    active            = db.Column(db.Boolean, default=False, nullable=False)
    priority          = db.Column(db.Integer, default=99, nullable=False)  # lower = higher priority
    status            = db.Column(db.String(50), default='unconfigured', nullable=False)
    last_tested_at    = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error        = db.Column(db.Text, nullable=True)
    emails_sent_today = db.Column(db.Integer, default=0, nullable=False)
    last_sent_at      = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at        = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at        = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant = db.relationship('Tenant', backref=db.backref(
        'email_providers', lazy='dynamic', cascade='all, delete-orphan',
        passive_deletes=True,
    ))

    @classmethod
    def get_ordered_active(cls, tenant_id: int) -> list:
        """Return active providers ordered by priority (ascending)."""
        return (cls.query
                .filter_by(tenant_id=tenant_id, active=True)
                .order_by(cls.priority.asc())
                .all())

    @classmethod
    def get_or_create(cls, tenant_id: int, provider_name: str) -> 'TenantEmailProvider':
        obj = cls.query.filter_by(tenant_id=tenant_id, provider_name=provider_name).first()
        if not obj:
            # Assign sensible default priorities
            defaults = {'smtp': 2, 'resend': 1, 'mailersend': 3}
            obj = cls(
                tenant_id=tenant_id,
                provider_name=provider_name,
                priority=defaults.get(provider_name, 99),
            )
            db.session.add(obj)
            db.session.flush()
        return obj

    def __repr__(self):
        return f'<TenantEmailProvider tenant={self.tenant_id} provider={self.provider_name} active={self.active}>'


class TenantSmtpSettings(db.Model):
    """Encrypted SMTP credentials for a tenant email provider."""
    __tablename__ = 'tenant_smtp_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_smtp'),
    )

    id               = db.Column(db.Integer, primary_key=True)
    tenant_id        = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    smtp_host        = db.Column(db.String(300), default='')
    smtp_port        = db.Column(db.Integer, default=587)
    smtp_username    = db.Column(db.String(300), default='')
    _smtp_password   = db.Column('smtp_password_encrypted', db.Text, default='')
    sender_email     = db.Column(db.String(300), default='')
    sender_name      = db.Column(db.String(200), default='')
    encryption_type  = db.Column(db.String(20), default='tls')   # tls | ssl | none
    created_at       = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at       = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def smtp_password(self) -> str:
        return decrypt_secret(self._smtp_password) if (self._smtp_password is not None and self._smtp_password != '') else ''

    @smtp_password.setter
    def smtp_password(self, value: str):
        self._smtp_password = encrypt_secret(value) if value else ''

    @property
    def is_configured(self) -> bool:
        # Check raw encrypted blob for password — never decrypt_secret here,
        # a decryption failure returns '' and makes configured=False even when
        # a password IS stored. sender_email excluded: display-only metadata.
        raw_pw = self._smtp_password
        return (
            isinstance(self.smtp_host, str) and len(self.smtp_host.strip()) > 0
            and isinstance(self.smtp_username, str) and len(self.smtp_username.strip()) > 0
            and isinstance(raw_pw, str) and len(raw_pw.strip()) > 0
        )

    @classmethod
    def get_or_create(cls, tenant_id: int) -> 'TenantSmtpSettings':
        obj = cls.query.filter_by(tenant_id=tenant_id).first()
        if not obj:
            obj = cls(tenant_id=tenant_id)
            db.session.add(obj)
            db.session.flush()
        return obj

    def __repr__(self):
        return f'<TenantSmtpSettings tenant={self.tenant_id} host={self.smtp_host!r}>'


class TenantResendSettings(db.Model):
    """Encrypted Resend API credentials for a tenant."""
    __tablename__ = 'tenant_resend_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_resend'),
    )

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    _api_key      = db.Column('api_key_encrypted', db.Text, default='')
    domain        = db.Column(db.String(300), default='')
    sender_email  = db.Column(db.String(300), default='')
    sender_name   = db.Column(db.String(200), default='')
    created_at    = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at    = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def api_key(self) -> str:
        return decrypt_secret(self._api_key) if (self._api_key is not None and self._api_key != '') else ''

    @api_key.setter
    def api_key(self, value: str):
        self._api_key = encrypt_secret(value) if value else ''

    @property
    def is_configured(self) -> bool:
        raw_key = self._api_key
        return isinstance(raw_key, str) and len(raw_key.strip()) > 0

    @classmethod
    def get_or_create(cls, tenant_id: int) -> 'TenantResendSettings':
        obj = cls.query.filter_by(tenant_id=tenant_id).first()
        if not obj:
            obj = cls(tenant_id=tenant_id)
            db.session.add(obj)
            db.session.flush()
        return obj

    def __repr__(self):
        return f'<TenantResendSettings tenant={self.tenant_id} domain={self.domain!r}>'


class TenantMailerSendSettings(db.Model):
    """Encrypted MailerSend API credentials for a tenant."""
    __tablename__ = 'tenant_mailersend_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_mailersend'),
    )

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    _api_token    = db.Column('api_token_encrypted', db.Text, default='')
    domain        = db.Column(db.String(300), default='')
    sender_email  = db.Column(db.String(300), default='')
    sender_name   = db.Column(db.String(200), default='')
    created_at    = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at    = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def api_token(self) -> str:
        return decrypt_secret(self._api_token) if (self._api_token is not None and self._api_token != '') else ''

    @api_token.setter
    def api_token(self, value: str):
        self._api_token = encrypt_secret(value) if value else ''

    @property
    def is_configured(self) -> bool:
        raw_token = self._api_token
        return isinstance(raw_token, str) and len(raw_token.strip()) > 0

    @classmethod
    def get_or_create(cls, tenant_id: int) -> 'TenantMailerSendSettings':
        obj = cls.query.filter_by(tenant_id=tenant_id).first()
        if not obj:
            obj = cls(tenant_id=tenant_id)
            db.session.add(obj)
            db.session.flush()
        return obj

    def __repr__(self):
        return f'<TenantMailerSendSettings tenant={self.tenant_id} domain={self.domain!r}>'


# ─────────────────────────────────────────────────────────────────────────────
# Theme Catalog (SuperAdmin Theme CRUD — v6.4)
# ─────────────────────────────────────────────────────────────────────────────
# DB-backed overlay on top of the filesystem-discovered themes (theme.json
# files under /themes/<slug>/). This table does NOT replace the filesystem
# as the source of truth for "does this theme physically exist and have
# renderable templates" — that check stays in ThemeRegistry.exists().
#
# Instead it gives SuperAdmin a place to manage the *gating/listing* concerns
# that previously could only be edited by hand-editing theme.json on disk:
# active/inactive, premium flag, required plan, sort order, and a
# superadmin-editable display name/description/category.
#
# A theme with no ThemeCatalogEntry row still works exactly as before —
# ThemeEngine falls back to the theme.json metadata. The row is only an
# override when present.

# Plans that can be set as 'required_plan' on a ThemeCatalogEntry.
# Must stay in sync with ThemeEngine._PLAN_RANK in theme_engine.py.
VALID_REQUIRED_PLANS = ('free', 'basic', 'pro', 'premium', 'enterprise', 'agency')


class ThemeCatalogEntry(db.Model):
    """SuperAdmin-managed catalog row overlaying a filesystem theme (v6.5)."""
    __tablename__ = 'theme_catalog_entries'

    id            = db.Column(db.Integer, primary_key=True)
    slug          = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name          = db.Column(db.String(150), nullable=True)
    description   = db.Column(db.Text, nullable=True)
    category      = db.Column(db.String(60), nullable=True)
    is_active     = db.Column(db.Boolean, nullable=False, default=True)
    is_premium    = db.Column(db.Boolean, nullable=True)  # None = defer to theme.json
    required_plan = db.Column(db.String(20), nullable=True)  # 'free' | 'pro' | 'enterprise'
    sort_order    = db.Column(db.Integer, nullable=False, default=0)
    created_at    = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at    = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # ── v6.5 Extended fields (migration 0035) ────────────────────────────────
    thumbnail_url  = db.Column(db.String(512), nullable=True)
    banner_url     = db.Column(db.String(512), nullable=True)
    preview_images = db.Column(db.Text, nullable=True)   # JSON array of URLs
    theme_author   = db.Column(db.String(120), nullable=True)
    theme_version  = db.Column(db.String(30), nullable=True)
    theme_tags     = db.Column(db.Text, nullable=True)    # JSON array of strings
    feature_matrix = db.Column(db.Text, nullable=True)   # JSON object
    is_featured    = db.Column(db.Boolean, nullable=False, default=False)
    install_count  = db.Column(db.Integer, nullable=False, default=0)

    # ── Helpers ──────────────────────────────────────────────────────────────

    _DEFAULT_FEATURE_MATRIX = {
        'hero': True, 'about': True, 'projects': True, 'skills': True,
        'services': True, 'testimonials': True, 'timeline': True,
        'resume': True, 'contact': True, 'gallery': True,
        'blog': False, 'shop': False,
    }

    def get_preview_images(self) -> list:
        import json
        try:
            imgs = json.loads(self.preview_images or '[]')
            return [i for i in imgs if isinstance(i, str) and i.strip()]
        except Exception:
            return []

    def set_preview_images(self, urls: list):
        import json
        self.preview_images = json.dumps([u for u in urls if u and u.strip()])

    def get_feature_matrix(self) -> dict:
        import json
        base = dict(self._DEFAULT_FEATURE_MATRIX)
        try:
            stored = json.loads(self.feature_matrix or '{}')
            if isinstance(stored, dict):
                base.update({k: bool(v) for k, v in stored.items()})
        except Exception:
            pass
        return base

    def set_feature_matrix(self, matrix: dict):
        import json
        self.feature_matrix = json.dumps({k: bool(v) for k, v in matrix.items()})

    def get_tags(self) -> list:
        import json
        try:
            tags = json.loads(self.theme_tags or '[]')
            return [t for t in tags if isinstance(t, str) and t.strip()]
        except Exception:
            return []

    def set_tags(self, tags: list):
        import json
        self.theme_tags = json.dumps([t.strip() for t in tags if t and t.strip()])

    def increment_installs(self):
        self.install_count = (self.install_count or 0) + 1

    @classmethod
    def get_by_slug(cls, slug):
        if not slug:
            return None
        return cls.query.filter_by(slug=slug).first()

    @classmethod
    def get_or_create(cls, slug, defaults=None):
        entry = cls.get_by_slug(slug)
        if entry:
            return entry
        entry = cls(slug=slug, **(defaults or {}))
        db.session.add(entry)
        db.session.flush()
        return entry

    def __repr__(self):
        return f'<ThemeCatalogEntry slug={self.slug!r} active={self.is_active}>'
