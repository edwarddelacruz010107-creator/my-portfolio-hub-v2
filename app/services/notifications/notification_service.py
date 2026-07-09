"""
app/services/notification_service.py — Notification Service Layer
Portfolio CMS v4.0

Provides clean interface for:
  - Creating notifications (auto + manual)
  - Fetching unread counts for bell badge
  - Marking notifications as read
  - Listing tenant notifications
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc)


def create_notification(
    tenant_id: int,
    notification_type: str,
    title: str,
    message: str,
    subscription_id: int = None,
    sent_via_email: bool = False,
    commit: bool = False,
) -> 'SubscriptionNotification | None':
    """
    Create and optionally commit a SubscriptionNotification.

    notification_type:
        reminder_7d | reminder_30d | expired | renewed | activated | manual
    """
    try:
        from app import db
        from app.models.portfolio import SubscriptionNotification
        notif = SubscriptionNotification(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            notification_type=notification_type,
            title=title,
            message=message,
            is_read=False,
            sent_via_dashboard=True,
            sent_via_email=sent_via_email,
            created_at=_utcnow(),
        )
        db.session.add(notif)
        if commit:
            db.session.commit()
        return notif
    except Exception as exc:
        logger.error('create_notification failed: %s', exc)
        return None


def get_unread_count(tenant_id: int) -> int:
    try:
        from app.models.portfolio import SubscriptionNotification
        return SubscriptionNotification.unread_count(tenant_id)
    except Exception:
        return 0


def get_notifications(tenant_id: int, limit: int = 20, unread_only: bool = False):
    try:
        from app.models.portfolio import SubscriptionNotification
        q = SubscriptionNotification.query.filter_by(tenant_id=tenant_id)
        if unread_only:
            q = q.filter_by(is_read=False)
        return q.order_by(SubscriptionNotification.created_at.desc()).limit(limit).all()
    except Exception as exc:
        logger.error('get_notifications failed: %s', exc)
        return []


def mark_notification_read(notification_id: int, tenant_id: int) -> bool:
    """Mark a single notification as read (enforces tenant ownership)."""
    try:
        from app import db
        from app.models.portfolio import SubscriptionNotification
        notif = SubscriptionNotification.query.filter_by(
            id=notification_id, tenant_id=tenant_id
        ).first()
        if notif:
            notif.mark_read()
            db.session.commit()
            return True
        return False
    except Exception as exc:
        logger.error('mark_notification_read failed: %s', exc)
        return False


def mark_all_read(tenant_id: int) -> int:
    """Mark all unread notifications as read for a tenant. Returns count updated."""
    try:
        from app import db
        from app.models.portfolio import SubscriptionNotification
        now = _utcnow()
        count = (
            SubscriptionNotification.query
            .filter_by(tenant_id=tenant_id, is_read=False)
            .update({'is_read': True, 'read_at': now})
        )
        db.session.commit()
        return count
    except Exception as exc:
        logger.error('mark_all_read failed: %s', exc)
        return 0


def get_expiry_warning(tenant_id: int) -> dict | None:
    """
    Returns warning banner context for tenant dashboard.
    Returns None if no warning needed.

    Returns dict with keys:
        days_left: int
        plan: str
        threshold: int   (7 or 30)
    """
    try:
        from app.models.portfolio import Subscription
        from app.services.renewal_scheduler import _days_until_expiry, _is_monthly, _is_yearly_or_longer

        sub = Subscription.current(tenant_id)
        if not sub or sub.status != 'active':
            return None

        days_left = _days_until_expiry(sub)
        if days_left is None:
            return None

        if _is_monthly(sub) and 0 < days_left <= 7:
            return {'days_left': days_left, 'plan': sub.plan or 'Subscription', 'threshold': 7}
        if _is_yearly_or_longer(sub) and 0 < days_left <= 30:
            return {'days_left': days_left, 'plan': sub.plan or 'Subscription', 'threshold': 30}
        return None
    except Exception as exc:
        logger.error('get_expiry_warning failed: %s', exc)
        return None
