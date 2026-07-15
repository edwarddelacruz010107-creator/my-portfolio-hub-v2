"""Append-only portfolio-intelligence snapshots (Phase 6)."""
from __future__ import annotations

import uuid

from sqlalchemy import event

from app.extensions import db
from app.utils.datetime_utils import utc_now


class PortfolioIntelligenceSnapshot(db.Model):
    """One real calculation for a tenant, rubric version, and content hash."""

    __tablename__ = "portfolio_intelligence_snapshots"
    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id", "portfolio_hash", "rubric_version",
            name="uq_portfolio_intelligence_tenant_hash_version",
        ),
        db.Index("ix_portfolio_intelligence_tenant_calculated", "tenant_id", "calculated_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    portfolio_hash = db.Column(db.String(64), nullable=False)
    rubric_version = db.Column(db.String(80), nullable=False)
    total_score = db.Column(db.Numeric(5, 2), nullable=True)
    evaluated_weight = db.Column(db.SmallInteger, nullable=False)
    dimension_scores = db.Column(db.JSON, nullable=False, default=list)
    evidence = db.Column(db.JSON, nullable=False, default=dict)
    recommendations = db.Column(db.JSON, nullable=False, default=list)
    calculated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_snapshot_mutation(_mapper, _connection, target):
    raise RuntimeError(f"{target.__class__.__name__} is append-only")


event.listen(PortfolioIntelligenceSnapshot, "before_update", _reject_snapshot_mutation)
event.listen(PortfolioIntelligenceSnapshot, "before_delete", _reject_snapshot_mutation)
