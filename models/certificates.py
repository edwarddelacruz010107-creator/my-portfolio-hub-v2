"""
models/certificates.py
Certificate and Badge models for Portfolio Hub v6.6
Tenant-scoped, CMS-driven, sort-ordered.
"""

from datetime import date
from extensions import db


class Certificate(db.Model):
    __tablename__ = "certificates"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Core fields
    title = db.Column(db.String(200), nullable=False)
    issuer = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Dates
    issue_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)  # null = no expiry

    # Credential identity
    credential_id = db.Column(db.String(200), nullable=True)
    credential_url = db.Column(db.String(512), nullable=True)

    # Media
    image_filename = db.Column(db.String(255), nullable=True)  # stored in tenant upload dir

    # Display
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    is_visible = db.Column(db.Boolean, nullable=False, default=True)

    # Timestamps
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    # Relationships
    tenant = db.relationship("Tenant", back_populates="certificates")

    def __repr__(self):
        return f"<Certificate {self.id}: {self.title} — {self.issuer}>"

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "issuer": self.issuer,
            "description": self.description,
            "issue_date": self.issue_date.isoformat() if self.issue_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "credential_id": self.credential_id,
            "credential_url": self.credential_url,
            "image_filename": self.image_filename,
            "sort_order": self.sort_order,
            "is_featured": self.is_featured,
            "is_visible": self.is_visible,
        }

    @property
    def is_expired(self):
        if self.expiry_date is None:
            return False
        return self.expiry_date < date.today()

    @property
    def image_url(self):
        if self.image_filename:
            return f"/uploads/{self.tenant_id}/certificates/{self.image_filename}"
        return "/static/img/placeholders/certificate.svg"


class Badge(db.Model):
    __tablename__ = "badges"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Core fields
    name = db.Column(db.String(200), nullable=False)
    provider = db.Column(db.String(200), nullable=False)  # e.g. "Credly", "AWS", "GitHub"

    # Media
    image_filename = db.Column(db.String(255), nullable=True)
    image_url_external = db.Column(db.String(512), nullable=True)  # for external badge embeds (Credly, etc.)

    # Verification
    verification_url = db.Column(db.String(512), nullable=True)

    # Metadata
    issued_date = db.Column(db.Date, nullable=True)
    skill_tag = db.Column(db.String(100), nullable=True)  # e.g. "Cloud", "Security", "DevOps"

    # Display
    display_order = db.Column(db.Integer, nullable=False, default=0)
    is_visible = db.Column(db.Boolean, nullable=False, default=True)

    # Timestamps
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    # Relationships
    tenant = db.relationship("Tenant", back_populates="badges")

    def __repr__(self):
        return f"<Badge {self.id}: {self.name} — {self.provider}>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "image_filename": self.image_filename,
            "image_url_external": self.image_url_external,
            "verification_url": self.verification_url,
            "issued_date": self.issued_date.isoformat() if self.issued_date else None,
            "skill_tag": self.skill_tag,
            "display_order": self.display_order,
            "is_visible": self.is_visible,
        }

    @property
    def resolved_image_url(self):
        """Prefer external URL (Credly embed), fallback to uploaded file, fallback to placeholder."""
        if self.image_url_external:
            return self.image_url_external
        if self.image_filename:
            return f"/uploads/{self.tenant_id}/badges/{self.image_filename}"
        return "/static/img/placeholders/badge.svg"
