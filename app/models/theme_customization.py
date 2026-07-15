"""Theme customization drafts and append-only published versions."""
from __future__ import annotations

import uuid

from sqlalchemy import event

from app.extensions import db
from app.utils.datetime_utils import utc_now


class ThemeCustomizationDraft(db.Model):
    __tablename__ = "theme_customization_drafts"
    __table_args__ = (
        db.UniqueConstraint("tenant_id", "theme_id", name="uq_theme_customization_draft_tenant_theme"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    theme_id = db.Column(db.String(64), nullable=False)
    base_version_id = db.Column(
        db.String(36), db.ForeignKey("theme_customization_versions.id", ondelete="SET NULL"), nullable=True
    )
    tokens = db.Column(db.JSON, nullable=False, default=dict)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class ThemeCustomizationVersion(db.Model):
    __tablename__ = "theme_customization_versions"
    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id", "theme_id", "version_number",
            name="uq_theme_customization_version_number",
        ),
        db.Index(
            "ix_theme_customization_version_tenant_theme_created",
            "tenant_id", "theme_id", "created_at",
        ),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    theme_id = db.Column(db.String(64), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    tokens = db.Column(db.JSON, nullable=False, default=dict)
    source = db.Column(db.String(20), nullable=False)
    restored_from_id = db.Column(
        db.String(36), db.ForeignKey("theme_customization_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_version_mutation(_mapper, _connection, target):
    raise RuntimeError(f"{target.__class__.__name__} is append-only")


event.listen(ThemeCustomizationVersion, "before_update", _reject_version_mutation)
event.listen(ThemeCustomizationVersion, "before_delete", _reject_version_mutation)
