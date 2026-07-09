"""
app/models/tenant_form_settings.py — Per-tenant form provider model (v4.2)

Architecture:
    Each tenant owns their own form provider configuration.
    NO tenant can read another tenant's API keys.
    Supported providers: basin | web3forms | disabled

Security:
    - api_key_encrypted: Fernet-encrypted, never decrypted in templates
    - Masking helper: only last 4 chars visible to superadmin
    - Encryption reuses the same Fernet helper from core.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app import db
from app.models.core import encrypt_secret, decrypt_secret  # canonical Fernet helpers

logger = logging.getLogger(__name__)

VALID_PROVIDERS = ('basin', 'email_only', 'web3forms', 'disabled')
BASIN_PREFIX    = 'https://usebasin.com/f/'
WEB3FORMS_URL   = 'https://api.web3forms.com/submit'


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TenantFormSettings(db.Model):
    """
    Per-tenant contact form provider configuration.

    One row per tenant (unique constraint on tenant_id).
    Encrypted: api_key_encrypted (Fernet). Never expose in templates or JSON.

    Provider routing:
        'basin'     → POST to form_endpoint (server-side proxy)
        'web3forms' → POST to WEB3FORMS_URL with api_key as access_key field
        'disabled'  → fallback to internal CMS inbox (Inquiry model)
    """
    __tablename__ = 'tenant_form_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_form_settings'),
        db.Index('ix_tfs_provider',   'provider'),
        db.Index('ix_tfs_is_enabled', 'is_enabled'),
    )

    id                = db.Column(db.Integer, primary_key=True)
    tenant_id         = db.Column(
        db.Integer,
        db.ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    # 'basin' | 'web3forms' | 'disabled'
    provider          = db.Column(db.String(20), nullable=False, default='disabled')

    # Fernet-encrypted API key — NEVER expose raw value to frontend
    _api_key_encrypted = db.Column('api_key_encrypted', db.Text, nullable=False, default='')

    form_endpoint     = db.Column(db.Text,        nullable=True)   # Basin: full URL; Web3Forms: None
    receiver_email    = db.Column(db.String(200),  nullable=True)   # Where submissions land
    sender_name       = db.Column(db.String(200),  nullable=True)   # Display name in email

    is_enabled        = db.Column(db.Boolean, nullable=False, default=False)

    created_at        = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at        = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # ── Relationship ──────────────────────────────────────────────────────────
    tenant = db.relationship(
        'Tenant',
        backref=db.backref(
            'form_settings',
            uselist=False,
            cascade='all, delete-orphan',
            passive_deletes=True,
        ),
    )

    # ── Encrypted API key accessors ───────────────────────────────────────────

    @property
    def api_key(self) -> str:
        """Decrypt API key. Returns '' on failure — never raises."""
        return decrypt_secret(self._api_key_encrypted)

    @api_key.setter
    def api_key(self, value: str) -> None:
        """
        Encrypt and store API key.
        Raises RuntimeError if encryption is unavailable (misconfigured FERNET_KEY).
        Pass '' to clear without encryption.
        """
        if not value:
            self._api_key_encrypted = ''
            return
        encrypted = encrypt_secret(value)
        if not encrypted:
            raise RuntimeError(
                'encrypt_secret() returned empty — FERNET_KEY may be misconfigured. '
                'Check your environment and ensure FERNET_KEY is set correctly.'
            )
        self._api_key_encrypted = encrypted

    @property
    def api_key_masked(self) -> str:
        """
        Return masked key for superadmin display: '***********abcd'
        Never expose more than last 4 chars.
        """
        raw = self.api_key
        if not raw:
            return ''
        visible = raw[-4:] if len(raw) >= 4 else raw
        return f'{"*" * 11}{visible}'

    # ── Status helpers ────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """True if provider is set and has the required key/endpoint."""
        if self.provider == 'disabled':
            return False
        if self.provider == 'basin':
            return bool(self.form_endpoint and
                        self.form_endpoint.startswith(BASIN_PREFIX))
        if self.provider == 'email_only':
            return bool(self.receiver_email)
        if self.provider == 'web3forms':
            return bool(self.api_key)
        return False

    @property
    def status_label(self) -> str:
        """Human-readable status for UI badges."""
        if not self.is_enabled or self.provider == 'disabled':
            return 'disabled'
        if self.is_configured:
            return 'connected'
        return 'needs_setup'

    @property
    def effective_endpoint(self) -> Optional[str]:
        """Return the actual submission endpoint based on provider."""
        if self.provider == 'basin':
            return self.form_endpoint
        if self.provider == 'web3forms':
            return WEB3FORMS_URL
        return None

    # ── Class methods ─────────────────────────────────────────────────────────

    @classmethod
    def get_or_create(cls, tenant_id: int) -> 'TenantFormSettings':
        """Fetch existing settings or initialize a disabled row (not yet committed)."""
        obj = cls.query.filter_by(tenant_id=tenant_id).first()
        if not obj:
            obj = cls(tenant_id=tenant_id, provider='disabled', is_enabled=False)
            db.session.add(obj)
            db.session.flush()
        return obj

    @classmethod
    def for_tenant(cls, tenant_id: int) -> Optional['TenantFormSettings']:
        """Nullable lookup — returns None if not configured."""
        return cls.query.filter_by(tenant_id=tenant_id).first()

    def __repr__(self) -> str:
        return (
            f'<TenantFormSettings tenant_id={self.tenant_id} '
            f'provider={self.provider!r} '
            f'configured={self.is_configured}>'
        )
