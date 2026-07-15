"""Real operational, incident, and audit evidence for the founder dashboard."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func

from app import db
from app.heartbeat import get_heartbeat_state, get_readiness_snapshot
from app.models.ai_center import AIAuditEvent, AIRequestJob
from app.models.core import ActivityLog, WebhookEvent
from app.models.ledger import FinancialAuditEvent
from app.models.notification import Notification, NotificationDelivery
from app.services.founder.domain import OPERATIONS_READ_MODEL_VERSION


def _scope(model, tenant_ids: tuple[int, ...] | None):
    return [] if tenant_ids is None else [model.tenant_id.in_(tenant_ids)]


def build_operations_read_model(
    *,
    tenant_ids: tuple[int, ...] | None,
    start_at: datetime,
    end_at: datetime,
) -> dict:
    readiness = get_readiness_snapshot()
    heartbeat = get_heartbeat_state()
    delivery = (
        db.session.query(
            func.count(NotificationDelivery.id),
            func.sum(case((NotificationDelivery.status == "pending", 1), else_=0)),
            func.sum(case((NotificationDelivery.status == "failed", 1), else_=0)),
            func.sum(case((NotificationDelivery.status == "dead", 1), else_=0)),
            func.max(NotificationDelivery.updated_at),
        )
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .filter(
            NotificationDelivery.created_at >= start_at,
            NotificationDelivery.created_at < end_at,
            *_scope(Notification, tenant_ids),
        )
        .first()
    )
    webhook_filters = [
        WebhookEvent.received_at >= start_at,
        WebhookEvent.received_at < end_at,
        WebhookEvent.processed.is_(False),
        *_scope(WebhookEvent, tenant_ids),
    ]
    unprocessed_webhooks = int(WebhookEvent.query.filter(*webhook_filters).count())

    incidents = []
    for job in (
        AIRequestJob.query.filter(
            AIRequestJob.status == "failed",
            AIRequestJob.completed_at >= start_at,
            AIRequestJob.completed_at < end_at,
            *_scope(AIRequestJob, tenant_ids),
        )
        .order_by(AIRequestJob.completed_at.desc())
        .limit(5)
        .all()
    ):
        incidents.append({
            "source": "AI",
            "kind": job.last_error_class or "AI request failure",
            "status": "failed",
            "occurred_at": job.completed_at,
            "route": "superadmin.ai_center",
            "route_params": {"tab": "logs"},
        })
    for item in (
        NotificationDelivery.query.join(
            Notification, Notification.id == NotificationDelivery.notification_id
        ).filter(
            NotificationDelivery.status.in_(["failed", "dead"]),
            NotificationDelivery.updated_at >= start_at,
            NotificationDelivery.updated_at < end_at,
            *_scope(Notification, tenant_ids),
        )
        .order_by(NotificationDelivery.updated_at.desc())
        .limit(5)
        .all()
    ):
        incidents.append({
            "source": "Notification",
            "kind": item.error_class or "Delivery failure",
            "status": item.status,
            "occurred_at": item.updated_at,
            "route": "superadmin.notifications",
            "route_params": {},
        })
    for item in (
        WebhookEvent.query.filter(*webhook_filters)
        .order_by(WebhookEvent.received_at.desc())
        .limit(5)
        .all()
    ):
        incidents.append({
            "source": "Webhook",
            "kind": item.event_type,
            "status": "unprocessed",
            "occurred_at": item.received_at,
            "route": "superadmin.subscription_monitor",
            "route_params": {},
        })
    incidents.sort(key=lambda item: item["occurred_at"] or start_at, reverse=True)

    audits = []
    activity_rows = (
        ActivityLog.query.filter(
            ActivityLog.created_at >= start_at,
            ActivityLog.created_at < end_at,
            *_scope(ActivityLog, tenant_ids),
        )
        .order_by(ActivityLog.created_at.desc())
        .limit(8)
        .all()
    )
    for item in activity_rows:
        audits.append({
            "source": "Platform",
            "event": item.action,
            "entity": item.entity_type or "platform",
            "actor": item.username or "system",
            "occurred_at": item.created_at,
            "route": "superadmin.logs",
        })
    for item in (
        FinancialAuditEvent.query.filter(
            FinancialAuditEvent.created_at >= start_at,
            FinancialAuditEvent.created_at < end_at,
            *_scope(FinancialAuditEvent, tenant_ids),
        )
        .order_by(FinancialAuditEvent.created_at.desc())
        .limit(5)
        .all()
    ):
        audits.append({
            "source": "Finance",
            "event": item.action,
            "entity": "payment transaction",
            "actor": item.actor,
            "occurred_at": item.created_at,
            "route": "superadmin.billing_overview",
        })
    for item in (
        AIAuditEvent.query.filter(
            AIAuditEvent.created_at >= start_at,
            AIAuditEvent.created_at < end_at,
            *_scope(AIAuditEvent, tenant_ids),
        )
        .order_by(AIAuditEvent.created_at.desc())
        .limit(5)
        .all()
    ):
        audits.append({
            "source": "AI",
            "event": item.event_type,
            "entity": item.entity_type,
            "actor": f"user:{item.actor_user_id}" if item.actor_user_id else "system",
            "occurred_at": item.created_at,
            "route": "superadmin.ai_center",
        })
    audits.sort(key=lambda item: item["occurred_at"] or start_at, reverse=True)

    checks = readiness["checks"]
    return {
        "definition_version": OPERATIONS_READ_MODEL_VERSION,
        "health": {
            "database_core": {"available": True, "status": checks.get("core_database", "unavailable")},
            "database_tenant": {"available": True, "status": checks.get("tenant_database", "unavailable")},
            "cache": {"available": checks.get("cache") != "not_configured", "status": checks.get("cache", "unavailable")},
            "heartbeat": {
                "available": heartbeat.get("last_heartbeat_at") is not None,
                "status": "ok" if heartbeat.get("last_heartbeat_at") else "unavailable",
                "observed_at": heartbeat.get("last_heartbeat_at"),
            },
            "self_ping": {
                "available": heartbeat.get("selfping_ok") is not None,
                "status": "ok" if heartbeat.get("selfping_ok") else ("degraded" if heartbeat.get("selfping_ok") is False else "unavailable"),
                "observed_at": heartbeat.get("last_selfping_at"),
            },
            "cpu": {"available": False, "status": "unavailable", "reason": "No monitoring source configured"},
            "memory": {"available": False, "status": "unavailable", "reason": "No monitoring source configured"},
            "disk": {"available": False, "status": "unavailable", "reason": "No monitoring source configured"},
        },
        "email_outbox": {
            "attempts": int(delivery[0] or 0),
            "pending": int(delivery[1] or 0),
            "failed": int(delivery[2] or 0),
            "dead": int(delivery[3] or 0),
            "latest_at": delivery[4],
        },
        "unprocessed_webhooks": unprocessed_webhooks,
        "incidents": incidents[:12],
        "audits": audits[:15],
        "freshness": {"readiness_checked_at": readiness["checked_at"]},
    }
