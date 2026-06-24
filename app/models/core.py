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

import pyotp
from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet as _Fernet, InvalidToken as _InvalidToken
from flask_login import UserMixin
from sqlalchemy.ext.hybrid import hybrid_property

from app import db


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

_comm_logger = _logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


SUBSCRIPTION_PLAN_ORDER = {'Basic': 1, 'Pro': 2, 'Enterprise': 3}

_PLAN_ALIASES = {
    'basic': 'Basic',
    'pro': 'Pro',
    'professional': 'Pro',
    'enterprise': 'Enterprise',
    'trial': 'Trial',
    'administrator': 'Administrator',
}

PAID_PLAN_NAMES = frozenset({'Basic', 'Pro', 'Enterprise'})

PLAN_FEATURES = {
    'Basic': {
        'max_projects': 5,
        'max_skills': 20,
        'max_media_uploads': 10,
        'custom_domain': False,
        'analytics': False,
        'white_label': False,
        'team_members': False,
        'api_access': False,
        'theme_customization': False,
    },
    'Pro': {
        'max_projects': None,
        'max_skills': None,
        'max_media_uploads': None,
        'custom_domain': True,
        'analytics': True,
        'white_label': False,
        'team_members': False,
        'api_access': False,
        'theme_customization': True,
    },
    'Enterprise': {
        'max_projects': None,
        'max_skills': None,
        'max_media_uploads': None,
        'custom_domain': True,
        'analytics': True,
        'white_label': True,
        'team_members': True,
        'api_access': True,
        'theme_customization': True,
    },
}


def normalize_plan_name(plan: str) -> str:
    if not plan:
        return 'Basic'
    normalized = (plan or '').strip().lower()
    return _PLAN_ALIASES.get(normalized, plan.strip().title())


def get_plan_features(plan: str) -> dict:
    return PLAN_FEATURES.get(normalize_plan_name(plan), PLAN_FEATURES['Basic'])


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
    if not ciphertext:
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
    plan       = db.Column(db.String(50),  nullable=False, default='Basic')
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # All relationships on the CORE side (no cross-DB relationships)
    users                 = db.relationship('User',  back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')
    subscriptions         = db.relationship('Subscription', back_populates='tenant', cascade='all, delete-orphan', lazy='select', order_by='Subscription.created_at.desc()')
    payment_instructions  = db.relationship('PaymentInstruction', back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')
    payment_methods       = db.relationship('PaymentMethod', back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')
    payments              = db.relationship('PaymentSubmission', back_populates='tenant', cascade='all, delete-orphan', lazy='dynamic')

    @property
    def normalized_plan(self) -> str:
        return normalize_plan_name(self.plan)

    def effective_plan(self) -> str:
        """Current plan from active subscription, else tenant.plan."""
        active = next(
            (s for s in self.subscriptions if s.is_active()),
            None,
        )
        return normalize_plan_name(active.plan) if active else self.normalized_plan

    @property
    def subscription_status(self) -> str:
        active_sub = next((s for s in self.subscriptions if s.status not in ('cancelled', 'expired')), None)
        return active_sub.status if active_sub else 'none'

    def is_active_subscription(self) -> bool:
        return any(s.is_active() for s in self.subscriptions)

    def plan_features(self) -> dict:
        return get_plan_features(self.effective_plan())

    def __repr__(self):
        return f'<Tenant {self.slug}>'


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
    email         = db.Column(db.String(120), unique=True, nullable=False, index=True)
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
        now = datetime.now(timezone.utc)
        if self.last_totp_code_hash == code_hash and self.last_totp_verified_at:
            last = self.last_totp_verified_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < 60:
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
        self.password_reset_expires = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
        return token

    def verify_reset_token(self, token: str) -> bool:
        if not self.password_reset_token or not self.password_reset_expires:
            return False
        expires = self.password_reset_expires
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return (
            secrets.compare_digest(self.password_reset_token, hashlib.sha256(token.encode()).hexdigest())
            and datetime.now(timezone.utc) < expires
        )

    def clear_reset_token(self):
        self.password_reset_token   = None
        self.password_reset_expires = None

    def __repr__(self):
        return f'<User {self.username}>'


# ═════════════════════════════════════════════════════════════════════════════
# CORE MODEL: Subscription
# ═════════════════════════════════════════════════════════════════════════════

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
            .filter(cls.tenant_id == tenant_id, cls.status.notin_(['cancelled']))
            .order_by(cls.created_at.desc())
            .first()
        )
        if sub is not None:
            sub.refresh_status(commit=False)
        return sub

    def refresh_status(self, commit: bool = False):
        if self.status in ('expired', 'cancelled'):
            return self
        if self.expires_at is None:
            return self
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
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
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= datetime.now(timezone.utc)

    def is_active(self) -> bool:
        if self.status != 'active':
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)

    @property
    def normalized_plan(self) -> str:
        return normalize_plan_name(self.plan)

    @property
    def status_label(self) -> str:
        _MAP = {
            'pending':   'Pending Payment',
            'active':    'Active',
            'expired':   'Expired',
            'cancelled': 'Cancelled',
        }
        return _MAP.get(self.status, self.status.title())

    @property
    def next_billing_date(self):
        return self.expires_at if self.status == 'active' else None

    def to_dict(self) -> dict:
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def __repr__(self):
        return f'<Subscription {self.tenant_id} {self.plan} {self.status}>'


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
    value      = db.Column(db.String(500), nullable=False, default='')
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
        return bool(self._web3forms_key)

    @property
    def has_smtp(self) -> bool:
        return bool(self.smtp_host and self.mail_username and self._mail_password)

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
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp

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
        return bool(self._web3forms_key)

    # ── MailerSend (v5.0 primary provider) ──────────────────────────────────

    @property
    def mailersend_api_key(self) -> str:
        return decrypt_secret(self._mailersend_api_key) if self._mailersend_api_key else ''

    @mailersend_api_key.setter
    def mailersend_api_key(self, value: str):
        self._mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_mailersend(self) -> bool:
        return bool(self._mailersend_api_key)

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
    def get(cls):
        from sqlalchemy.exc import IntegrityError

        config = db.session.get(cls, 1)

        if config is None:
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
                config = db.session.get(cls, 1)

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
        return decrypt_secret(self._admin_mailersend_api_key) if self._admin_mailersend_api_key else ''

    @admin_mailersend_api_key.setter
    def admin_mailersend_api_key(self, value: str):
        self._admin_mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_admin_mailersend(self) -> bool:
        return bool(self._admin_mailersend_api_key)

    @property
    def superadmin_mailersend_api_key(self) -> str:
        return decrypt_secret(self._superadmin_mailersend_api_key) if self._superadmin_mailersend_api_key else ''

    @superadmin_mailersend_api_key.setter
    def superadmin_mailersend_api_key(self, value: str):
        self._superadmin_mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_superadmin_mailersend(self) -> bool:
        return bool(self._superadmin_mailersend_api_key)

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
