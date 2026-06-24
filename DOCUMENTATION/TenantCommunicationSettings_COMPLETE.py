"""
COMPLETE MODEL CLASS: TenantCommunicationSettings (app/models/core.py)

This is the full updated TenantCommunicationSettings class with all MailerSend fields.
Copy this entire class to replace the existing one in app/models/core.py.

Key changes:
  1. Added three new columns for MailerSend
  2. Added encryption properties for API key
  3. Added has_mailersend property
  4. Added effective_mailersend_config() method
  5. Updated is_configured to check MailerSend first
"""

class TenantCommunicationSettings(db.Model):
    """
    Per-tenant email/Web3Forms/SMTP/MailerSend credentials (Fernet-encrypted secrets).
    
    This model stores configuration for each tenant's contact form provider and
    email sender settings. Supports multiple providers with priority ordering:
      1. Per-tenant MailerSend (highest priority)
      2. Global MailerSend
      3. Web3Forms
    
    SMTP fields are retained for backward compatibility but deprecated.
    """
    __tablename__ = 'tenant_communication_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_comm_settings'),
        db.Index('ix_tenant_comm_slug', 'tenant_slug'),
    )

    # ────────────────────────────────────────────────────────────────────────
    # IDENTITY
    # ────────────────────────────────────────────────────────────────────────
    
    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(
        db.Integer, 
        db.ForeignKey('tenants.id', ondelete='CASCADE'), 
        nullable=False, 
        index=True
    )
    tenant_slug = db.Column(db.String(120), nullable=False, index=True)

    # ────────────────────────────────────────────────────────────────────────
    # WEB3FORMS (Contact form submission routing)
    # ────────────────────────────────────────────────────────────────────────
    
    _web3forms_key = db.Column('web3forms_key', db.Text, default='')

    # ────────────────────────────────────────────────────────────────────────
    # SMTP (LEGACY — Deprecated in v5.0, retained for backward compatibility)
    # ────────────────────────────────────────────────────────────────────────
    
    mail_username       = db.Column(db.String(200), default='')
    _mail_password      = db.Column('mail_password', db.Text, default='')
    mail_default_sender = db.Column(db.String(200), default='')
    admin_email         = db.Column(db.String(200), default='')
    smtp_host           = db.Column(db.String(200), default='')
    smtp_port           = db.Column(db.Integer, default=587)
    smtp_tls            = db.Column(db.Boolean, default=True)

    # ────────────────────────────────────────────────────────────────────────
    # MAILERSEND (v5.0+ — Primary email provider, per-tenant support)
    # ────────────────────────────────────────────────────────────────────────
    
    _mailersend_api_key    = db.Column('mailersend_api_key', db.Text, default='', nullable=True)
    mailersend_from_email  = db.Column(db.String(200), default='', nullable=True)
    mailersend_from_name   = db.Column(db.String(200), default='', nullable=True)

    # ────────────────────────────────────────────────────────────────────────
    # METADATA
    # ────────────────────────────────────────────────────────────────────────
    
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # ────────────────────────────────────────────────────────────────────────
    # RELATIONSHIPS
    # ────────────────────────────────────────────────────────────────────────
    
    tenant = db.relationship(
        'Tenant', 
        backref=db.backref(
            'communication_settings', 
            uselist=False, 
            cascade='all, delete-orphan',
            passive_deletes=True,
        )
    )

    # ════════════════════════════════════════════════════════════════════════
    # PROPERTIES — Web3Forms
    # ════════════════════════════════════════════════════════════════════════

    @property
    def web3forms_key(self) -> str:
        """Decrypt and return Web3Forms API key."""
        return decrypt_secret(self._web3forms_key)

    @web3forms_key.setter
    def web3forms_key(self, value: str):
        """Encrypt and store Web3Forms API key."""
        self._web3forms_key = encrypt_secret(value) if value else ''

    @property
    def has_web3forms(self) -> bool:
        """Check if Web3Forms is configured."""
        return bool(self._web3forms_key)

    # ════════════════════════════════════════════════════════════════════════
    # PROPERTIES — SMTP (Legacy)
    # ════════════════════════════════════════════════════════════════════════

    @property
    def mail_password(self) -> str:
        """Decrypt and return SMTP password."""
        return decrypt_secret(self._mail_password)

    @mail_password.setter
    def mail_password(self, value: str):
        """Encrypt and store SMTP password."""
        self._mail_password = encrypt_secret(value) if value else ''

    @property
    def has_smtp(self) -> bool:
        """Check if SMTP is fully configured (deprecated)."""
        return bool(self.smtp_host and self.mail_username and self._mail_password)

    # ════════════════════════════════════════════════════════════════════════
    # PROPERTIES — MailerSend (Primary v5.0+ provider)
    # ════════════════════════════════════════════════════════════════════════

    @property
    def mailersend_api_key(self) -> str:
        """Decrypt and return MailerSend API key."""
        return decrypt_secret(self._mailersend_api_key) if self._mailersend_api_key else ''

    @mailersend_api_key.setter
    def mailersend_api_key(self, value: str):
        """Encrypt and store MailerSend API key.
        
        Args:
            value: Plain-text API key or empty string to clear.
        """
        self._mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_mailersend(self) -> bool:
        """Check if MailerSend is fully configured for this tenant.
        
        Returns True only if all three required fields are set:
          - API key (encrypted, non-empty)
          - From email address
          - From display name
        """
        return bool(
            self._mailersend_api_key 
            and self.mailersend_from_email 
            and self.mailersend_from_name
        )

    # ════════════════════════════════════════════════════════════════════════
    # CONFIGURATION METHODS
    # ════════════════════════════════════════════════════════════════════════

    @property
    def is_configured(self) -> bool:
        """Check if any email provider is configured.
        
        Priority order: MailerSend > Web3Forms > SMTP (legacy)
        """
        return self.has_mailersend or self.has_web3forms or self.has_smtp

    def effective_web3forms_key(self, app_config: dict) -> str:
        """Return Web3Forms key, preferring tenant config over app config.
        
        Args:
            app_config: Flask app.config dict with fallback values.
            
        Returns:
            Web3Forms API key (decrypted if present, or from app config).
        """
        return self.web3forms_key or app_config.get('WEB3FORMS_ACCESS_KEY', '')

    def effective_smtp_config(self, app_config: dict) -> dict:
        """Return effective SMTP configuration (DEPRECATED).
        
        Args:
            app_config: Flask app.config dict with fallback values.
            
        Returns:
            Dict with 'host', 'port', 'tls', 'username', 'password', 'sender', 'admin'.
            
        Note:
            This method is retained for backward compatibility but should not be used
            if MailerSend is configured. Email dispatch should check has_mailersend first.
        """
        return {
            'host':     self.smtp_host or app_config.get('MAIL_SERVER', ''),
            'port':     self.smtp_port or int(app_config.get('MAIL_PORT', 587)),
            'tls':      self.smtp_tls,
            'username': self.mail_username or app_config.get('MAIL_USERNAME', ''),
            'password': self.mail_password or app_config.get('MAIL_PASSWORD', ''),
            'sender':   self.mail_default_sender or app_config.get('MAIL_DEFAULT_SENDER', ''),
            'admin':    self.admin_email or app_config.get('ADMIN_EMAIL', ''),
        }

    def effective_mailersend_config(self) -> dict:
        """Return MailerSend configuration for email dispatch.
        
        Returns:
            Dict with 'api_key', 'from_email', 'from_name'.
            All values decrypted and ready to use.
            
        Raises:
            ValueError: If has_mailersend is False (configuration incomplete).
        """
        if not self.has_mailersend:
            raise ValueError(
                f'MailerSend not fully configured for tenant {self.tenant_id}. '
                'Check has_mailersend before calling this method.'
            )
        return {
            'api_key': self.mailersend_api_key,
            'from_email': self.mailersend_from_email,
            'from_name': self.mailersend_from_name,
        }

    # ════════════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ════════════════════════════════════════════════════════════════════════

    @classmethod
    def get_or_create(cls, tenant_id: int, tenant_slug: str) -> 'TenantCommunicationSettings':
        """Get or create communication settings for a tenant.
        
        Args:
            tenant_id: ID of the tenant.
            tenant_slug: Slug/identifier of the tenant.
            
        Returns:
            TenantCommunicationSettings instance (creates with defaults if not exists).
        """
        obj = cls.query.filter_by(tenant_id=tenant_id).first()
        if not obj:
            obj = cls(tenant_id=tenant_id, tenant_slug=tenant_slug)
            db.session.add(obj)
            db.session.flush()
        return obj

    # ════════════════════════════════════════════════════════════════════════
    # REPRESENTATION
    # ════════════════════════════════════════════════════════════════════════

    def __repr__(self):
        providers = []
        if self.has_mailersend:
            providers.append('mailersend')
        if self.has_web3forms:
            providers.append('web3forms')
        if self.has_smtp:
            providers.append('smtp')
        provider_str = '+'.join(providers) if providers else 'none'
        return f'<TenantCommSettings tenant={self.tenant_slug!r} providers={provider_str}>'
