"""
app/models/core_additions.py — New ORM models for v6.0 Enterprise refactor.

DROP THIS FILE INTO app/models/ and import from app/models/__init__.py:

    from app.models.core_additions import MediaUpload, PlanUsageLog

Also add the following columns to the Tenant class in core.py:

    storage_used_bytes  = db.Column(db.BigInteger, nullable=False, default=0)
    storage_limit_bytes = db.Column(db.BigInteger, nullable=True)
    subscription_state  = db.Column(db.String(30), nullable=False, default='active')
    grace_period_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)

And update the PLAN_FEATURES dict replacement:
    Remove PLAN_FEATURES / get_plan_features from core.py entirely.
    Use app.services.plan_capabilities.get_capabilities() instead.

NOTE: Do NOT modify existing table columns — only additive changes.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone

from app import db


def _utcnow():
    return datetime.now(timezone.utc)


class MediaUpload(db.Model):
    """
    Tracks every file uploaded by a tenant.

    Actual bytes live on disk at <UPLOAD_BASE>/<tenant_slug>/<category>/<uuid>.<ext>.
    This table holds only metadata for quota tracking, audit, and UI listings.

    Soft-delete via is_deleted (preserves quota recalculation history).
    """
    __tablename__ = 'media_uploads'
    __table_args__ = (
        db.Index('ix_media_uploads_tenant_deleted', 'tenant_id', 'is_deleted'),
        db.Index('ix_media_uploads_uploaded_at', 'uploaded_at'),
    )

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(
        db.Integer,
        db.ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    file_path     = db.Column(db.Text, nullable=False)
    thumb_path    = db.Column(db.Text, nullable=True)
    file_size     = db.Column(db.BigInteger, nullable=False, default=0)   # post-optimisation
    original_size = db.Column(db.BigInteger, nullable=False, default=0)   # pre-optimisation
    mime_type     = db.Column(db.String(100), nullable=False, default='')
    category      = db.Column(db.String(50),  nullable=False, default='general')
    original_name = db.Column(db.String(255), nullable=True)
    uploaded_at   = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    is_deleted    = db.Column(db.Boolean, nullable=False, default=False)

    tenant = db.relationship('Tenant', backref=db.backref('media_uploads', lazy='dynamic'))

    @classmethod
    def active_for_tenant(cls, tenant_id: int):
        """Return query for non-deleted uploads belonging to tenant."""
        return cls.query.filter_by(tenant_id=tenant_id, is_deleted=False)

    @classmethod
    def total_size_for_tenant(cls, tenant_id: int) -> int:
        """Sum of file_size for active (non-deleted) uploads. Used for reconciliation."""
        from sqlalchemy import func as sa_func
        result = (
            db.session.query(sa_func.coalesce(sa_func.sum(cls.file_size), 0))
            .filter_by(tenant_id=tenant_id, is_deleted=False)
            .scalar()
        )
        return int(result or 0)

    def __repr__(self) -> str:
        return f'<MediaUpload tenant={self.tenant_id} size={self.file_size} cat={self.category}>'


class PlanUsageLog(db.Model):
    """
    Lightweight append-only analytics log for plan-related events.

    event_type values:
        upload_saved        — file written successfully
        upload_denied       — capability gate blocked an upload
        quota_warning       — tenant crossed 90% storage
        page_limit_hit      — page cap reached
        project_limit_hit   — project cap reached
        email_sent          — email dispatched via tenant provider
        plan_downgrade      — plan downgraded (audit)
        suspension          — tenant suspended

    value:  numeric dimension (bytes, count, etc.)
    meta:   arbitrary JSON string for extra context
    """
    __tablename__ = 'plan_usage_log'
    __table_args__ = (
        db.Index('ix_plan_usage_log_tenant_event', 'tenant_id', 'event_type'),
        db.Index('ix_plan_usage_log_recorded_at',  'recorded_at'),
    )

    id          = db.Column(db.Integer, primary_key=True)
    tenant_id   = db.Column(
        db.Integer,
        db.ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    event_type  = db.Column(db.String(60), nullable=False)
    value       = db.Column(db.BigInteger, nullable=True)
    meta        = db.Column(db.Text, nullable=True)    # JSON string
    recorded_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    @classmethod
    def record(
        cls,
        tenant_id: int,
        event_type: str,
        value: int | None = None,
        **kwargs,
    ) -> None:
        """
        Append a usage event.  Silently swallows errors — never block user flow
        for analytics writes.
        """
        try:
            meta_str = json.dumps(kwargs) if kwargs else None
            entry = cls(
                tenant_id=tenant_id,
                event_type=event_type,
                value=value,
                meta=meta_str,
            )
            db.session.add(entry)
            # Do NOT commit here — let the caller's transaction boundary handle it.
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                '[PlanUsageLog] Failed to record event=%s tenant=%s: %s',
                event_type, tenant_id, exc,
            )

    def __repr__(self) -> str:
        return f'<PlanUsageLog tenant={self.tenant_id} event={self.event_type} val={self.value}>'
