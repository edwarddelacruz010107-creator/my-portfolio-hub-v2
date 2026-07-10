"""
UPDATED: app/models/core.py — TenantCommunicationSettings excerpt

This is the updated TenantCommunicationSettings class with MailerSend fields.
Add these fields and properties to the existing TenantCommunicationSettings model.

Location: app/models/core.py (class TenantCommunicationSettings)
"""

class TenantCommunicationSettings(db.Model):
    """Per-tenant email / Web3Forms / SMTP / MailerSend credentials (Fernet-encrypted secrets)."""
    __tablename__ = 'tenant_communication_settings'
    __table_args__ = (
        db.UniqueConstraint('tenant_id', name='uq_tenant_comm_settings'),
        db.Index('ix_tenant_comm_slug', 'tenant_slug'),
    )

    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(db.Integer, db.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    tenant_slug = db.Column(db.String(120), nullable=False, index=True)

    # ── Web3Forms ──────────────────────────────────────────────────────────
    _web3forms_key      = db.Column('web3forms_key', db.Text, default='')
    
    # ── SMTP (legacy, retained for backward compatibility) ─────────────────
    mail_username       = db.Column(db.String(200), default='')
    _mail_password      = db.Column('mail_password', db.Text, default='')
    mail_default_sender = db.Column(db.String(200), default='')
    admin_email         = db.Column(db.String(200), default='')
    smtp_host           = db.Column(db.String(200), default='')
    smtp_port           = db.Column(db.Integer, default=587)
    smtp_tls            = db.Column(db.Boolean, default=True)
    
    # ── MailerSend (v5.0+: per-tenant provider) ────────────────────────────
    _mailersend_api_key = db.Column('mailersend_api_key', db.Text, default='', nullable=True)
    mailersend_from_email = db.Column(db.String(200), default='', nullable=True)
    mailersend_from_name = db.Column(db.String(200), default='', nullable=True)
    
    created_at          = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at          = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    tenant = db.relationship('Tenant', backref=db.backref(
        'communication_settings', uselist=False, cascade='all, delete-orphan',
        passive_deletes=True,
    ))

    # ── Web3Forms encryption ───────────────────────────────────────────────
    @property
    def web3forms_key(self) -> str:
        return decrypt_secret(self._web3forms_key)

    @web3forms_key.setter
    def web3forms_key(self, value: str):
        self._web3forms_key = encrypt_secret(value) if value else ''

    @property
    def has_web3forms(self) -> bool:
        return bool(self._web3forms_key)

    # ── SMTP encryption ────────────────────────────────────────────────────
    @property
    def mail_password(self) -> str:
        return decrypt_secret(self._mail_password)

    @mail_password.setter
    def mail_password(self, value: str):
        self._mail_password = encrypt_secret(value) if value else ''

    @property
    def has_smtp(self) -> bool:
        return bool(self.smtp_host and self.mail_username and self._mail_password)

    # ── MailerSend encryption (NEW) ────────────────────────────────────────
    @property
    def mailersend_api_key(self) -> str:
        """Decrypt and return MailerSend API key."""
        return decrypt_secret(self._mailersend_api_key) if self._mailersend_api_key else ''

    @mailersend_api_key.setter
    def mailersend_api_key(self, value: str):
        """Encrypt and store MailerSend API key."""
        self._mailersend_api_key = encrypt_secret(value) if value else ''

    @property
    def has_mailersend(self) -> bool:
        """Check if MailerSend is configured for this tenant."""
        return bool(
            self._mailersend_api_key 
            and self.mailersend_from_email 
            and self.mailersend_from_name
        )

    @property
    def is_configured(self) -> bool:
        """Check if any email provider is configured."""
        return self.has_web3forms or self.has_smtp or self.has_mailersend

    # ── Configuration methods ──────────────────────────────────────────────
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

    def effective_mailersend_config(self) -> dict:
        """Return MailerSend configuration if available."""
        return {
            'api_key': self.mailersend_api_key,
            'from_email': self.mailersend_from_email or '',
            'from_name': self.mailersend_from_name or '',
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────
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
