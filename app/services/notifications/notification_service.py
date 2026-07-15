"""Canonical recipient-scoped notification and delivery service."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
import uuid
from typing import Any, Callable, Iterable

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.notification import Notification, NotificationDelivery, NotificationReceipt


logger = logging.getLogger(__name__)
UTC = timezone.utc
MAX_DELIVERY_ATTEMPTS = 5
PARAMETER_KEY = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


@dataclass(frozen=True)
class TemplateSpec:
    title: str
    body: str
    required: frozenset[str]
    allowed: frozenset[str]


def _template(title: str, body: str, *parameters: str) -> TemplateSpec:
    names = frozenset(parameters)
    return TemplateSpec(title, body, names, names)


TEMPLATES: dict[str, TemplateSpec] = {
    "legacy.literal": _template("{title}", "{message}", "title", "message"),
    "project.like": _template(
        "Your project received a like",
        "{actor_name} liked “{project_title}”.",
        "actor_name", "project_title",
    ),
    "project.view_milestone": _template(
        "Project view milestone",
        "“{project_title}” reached {view_count} views.",
        "project_title", "view_count",
    ),
    "billing.payment_submitted": _template(
        "Payment proof submitted",
        "{tenant_name} submitted a {currency_code} payment for review.",
        "tenant_name", "currency_code",
    ),
    "billing.payment_approved": _template(
        "Payment approved",
        "Your {plan_name} payment was approved. Your subscription is active.",
        "plan_name",
    ),
    "billing.payment_rejected": _template(
        "Payment needs attention",
        "Your payment proof was rejected. Review note: {review_reason}",
        "review_reason",
    ),
    "billing.payment_failed": _template(
        "Payment failed",
        "A payment for your {plan_name} subscription failed. Review billing to continue service.",
        "plan_name",
    ),
    "billing.activated": _template(
        "Subscription activated",
        "Your {plan_name} subscription is now active.",
        "plan_name",
    ),
    "billing.renewed": _template(
        "Subscription renewed",
        "Your {plan_name} subscription has been renewed.",
        "plan_name",
    ),
    "billing.expired": _template(
        "Subscription expired",
        "Your {plan_name} subscription expired. Renew to restore paid access.",
        "plan_name",
    ),
    "billing.cancelled": _template(
        "Subscription cancelled",
        "Your {plan_name} subscription was cancelled.",
        "plan_name",
    ),
    "billing.reminder_7d": _template(
        "Subscription expires in 7 days",
        "Your {plan_name} subscription expires on {expires_on}.",
        "plan_name", "expires_on",
    ),
    "billing.reminder_30d": _template(
        "Subscription expires in 30 days",
        "Your {plan_name} subscription expires on {expires_on}.",
        "plan_name", "expires_on",
    ),
    "inquiry.new": _template(
        "New portfolio inquiry",
        "A new inquiry was received for {tenant_name}.",
        "tenant_name",
    ),
    "system.health": _template(
        "System health needs attention",
        "{component_name} reported {health_status}.",
        "component_name", "health_status",
    ),
    "portfolio.completeness": _template(
        "Portfolio milestone reached",
        "Your portfolio is now {completion_percent}% complete.",
        "completion_percent",
    ),
    "ai.budget": _template(
        "AI budget alert",
        "{tenant_name} reached {usage_percent}% of its AI budget.",
        "tenant_name", "usage_percent",
    ),
    "ai.error": _template(
        "AI request failed",
        "{tenant_name} encountered a {error_class} AI error.",
        "tenant_name", "error_class",
    ),
    "message.tenant_to_platform": _template(
        "New tenant message",
        "{tenant_name} sent “{subject}”.",
        "tenant_name", "subject",
    ),
    "message.platform_to_tenant": _template(
        "New message from platform support",
        "Platform support sent “{subject}”.",
        "subject",
    ),
    "message.reply_to_platform": _template(
        "New tenant reply",
        "{tenant_name} replied to “{subject}”.",
        "tenant_name", "subject",
    ),
    "message.reply_to_tenant": _template(
        "New reply from platform support",
        "Platform support replied to “{subject}”.",
        "subject",
    ),
}

ACTION_ROUTE_ALLOWLIST = frozenset({
    "admin.notifications", "admin.projects", "admin.billing_overview", "admin.messages", "admin.edit_profile",
    "admin.portfolio_intelligence",
    "superadmin.notifications", "superadmin.billing_submissions",
    "superadmin.messages_inbox", "superadmin.logs", "superadmin.subscription_monitor",
    "superadmin.message_thread", "admin.view_message",
})


@dataclass(frozen=True)
class Recipient:
    recipient_type: str
    tenant_id: int | None = None
    recipient_id: str | None = None
    recipient_role: str | None = None

    @classmethod
    def tenant(cls, tenant_id: int) -> "Recipient":
        if not isinstance(tenant_id, int) or tenant_id <= 0:
            raise ValueError("tenant_id must be a positive integer")
        return cls("tenant", tenant_id=tenant_id)

    @classmethod
    def user(cls, user_id: int, *, tenant_id: int | None = None) -> "Recipient":
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        return cls("user", tenant_id=tenant_id, recipient_id=str(user_id))

    @classmethod
    def role(cls, role: str, *, tenant_id: int | None = None) -> "Recipient":
        normalized = str(role or "").strip().lower()
        if normalized not in {"superadmin", "tenant_admin"}:
            raise ValueError("unsupported recipient role")
        return cls("role", tenant_id=tenant_id, recipient_role=normalized)

    @property
    def scope_key(self) -> str:
        return ":".join(str(value or "-") for value in (
            self.recipient_type, self.tenant_id, self.recipient_id, self.recipient_role,
        ))


@dataclass(frozen=True)
class RecipientContext:
    user_id: int
    tenant_id: int | None
    roles: tuple[str, ...]

    @classmethod
    def tenant_admin(cls, *, user_id: int, tenant_id: int) -> "RecipientContext":
        return cls(user_id=user_id, tenant_id=tenant_id, roles=("tenant_admin",))

    @classmethod
    def superadmin(cls, *, user_id: int) -> "RecipientContext":
        return cls(user_id=user_id, tenant_id=None, roles=("superadmin",))


@dataclass(frozen=True)
class NotificationView:
    id: str
    event_type: str
    title: str
    message: str
    priority: str
    created_at: datetime
    is_read: bool
    sent_via_email: bool
    action_url: str | None

    @property
    def notification_type(self) -> str:
        return self.event_type.split(".")[-1]


@dataclass(frozen=True)
class NotificationPage:
    items: tuple[NotificationView, ...]
    next_cursor: str | None
    unread_count: int


@dataclass(frozen=True)
class OperationalNotificationView:
    """Read-only cross-tenant projection for an authorized operations page."""
    id: str
    tenant: object | None
    notification_type: str
    title: str
    sent_via_email: bool
    created_at: datetime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _safe_parameters(template_key: str, parameters: dict[str, object] | None) -> dict[str, str]:
    spec = TEMPLATES.get(template_key)
    if spec is None:
        raise ValueError("unsupported notification template")
    source = parameters or {}
    unknown = set(source) - set(spec.allowed)
    missing = set(spec.required) - set(source)
    if unknown or missing:
        raise ValueError(f"invalid notification parameters: unknown={sorted(unknown)} missing={sorted(missing)}")
    safe: dict[str, str] = {}
    for key, value in source.items():
        if not PARAMETER_KEY.fullmatch(key) or isinstance(value, (dict, list, tuple, set)):
            raise ValueError("notification parameters must be scalar allowlisted values")
        safe[key] = str(value if value is not None else "Unavailable")[:240]
    return safe


def _safe_action(route: str | None, parameters: dict[str, object] | None) -> tuple[str | None, dict[str, object]]:
    if route is None:
        return None, {}
    if route not in ACTION_ROUTE_ALLOWLIST:
        raise ValueError("unsupported notification action route")
    safe: dict[str, object] = {}
    for key, value in (parameters or {}).items():
        if not PARAMETER_KEY.fullmatch(str(key)) or isinstance(value, (dict, list, tuple, set)):
            raise ValueError("invalid notification action parameter")
        rendered = str(value)
        safe[str(key)] = rendered[:100]
    return route, safe


def _stored_dedupe_key(recipient: Recipient, dedupe_key: str) -> str:
    raw = f"{recipient.scope_key}:{str(dedupe_key or '').strip()}"
    if raw.endswith(":"):
        raise ValueError("dedupe_key is required")
    if len(raw) <= 255:
        return raw
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def hourly_dedupe_key(event_name: str, entity_id: object, *, occurred_at: datetime | None = None) -> str:
    """Documented one-hour aggregation bucket for high-volume events."""
    when = _aware(occurred_at) or _utcnow()
    return f"{event_name}:{entity_id}:{when.strftime('%Y%m%d%H')}"


def publish_portfolio_completion_milestone(*, tenant_id: int) -> bool:
    """Publish the highest real intelligence milestone, once per tenant."""
    from app.models.portfolio import Profile
    from app.services.intelligence.intelligence_service import get_portfolio_intelligence

    profile = Profile.query.filter_by(tenant_id=int(tenant_id)).first()
    if profile is None:
        return False
    result = get_portfolio_intelligence(int(tenant_id), persist=True)
    percent = max(0, min(100, int(result.get("total_score") or 0)))
    threshold = max((value for value in (25, 50, 75, 100) if percent >= value), default=0)
    if not threshold:
        return False
    _, created = publish_notification(
        recipient=Recipient.tenant(int(tenant_id)),
        event_type="portfolio.completeness",
        template_key="portfolio.completeness",
        parameters={"completion_percent": threshold},
        dedupe_key=f"portfolio.completeness:{threshold}",
        entity_type="profile",
        entity_id=profile.id,
        action_route="admin.portfolio_intelligence",
        commit=True,
    )
    return created


def publish_project_view_milestone(project) -> bool:
    """Publish only verified, configured project view thresholds."""
    view_count = int(project.view_count or 0)
    if view_count not in {10, 100, 1000, 10000} or not project.tenant_id:
        return False
    _, created = publish_notification(
        recipient=Recipient.tenant(int(project.tenant_id)),
        event_type="project.view_milestone",
        template_key="project.view_milestone",
        parameters={"project_title": project.title, "view_count": view_count},
        dedupe_key=f"project.view_milestone:{project.id}:{view_count}",
        entity_type="project",
        entity_id=project.id,
        action_route="admin.projects",
        commit=True,
    )
    return created


def publish_notification(
    *,
    recipient: Recipient,
    event_type: str,
    template_key: str,
    parameters: dict[str, object],
    dedupe_key: str,
    entity_type: str | None = None,
    entity_id: object | None = None,
    actor_type: str | None = None,
    actor_id: object | None = None,
    action_route: str | None = None,
    action_parameters: dict[str, object] | None = None,
    priority: str = "normal",
    channels: Iterable[str] = ("in_app",),
    expires_at: datetime | None = None,
    session=None,
    commit: bool = False,
) -> tuple[Notification, bool]:
    session = session or db.session
    event = str(event_type or "").strip().lower()
    if not event or len(event) > 100:
        raise ValueError("event_type is required")
    if priority not in {"low", "normal", "high", "urgent"}:
        raise ValueError("unsupported notification priority")
    safe_parameters = _safe_parameters(template_key, parameters)
    action_route, safe_action_parameters = _safe_action(action_route, action_parameters)
    stored_key = _stored_dedupe_key(recipient, dedupe_key)
    existing = session.query(Notification).filter_by(dedupe_key=stored_key).first()
    if existing is not None:
        return existing, False

    now = _utcnow()
    notification = Notification(
        id=str(uuid.uuid4()), recipient_type=recipient.recipient_type,
        recipient_id=recipient.recipient_id, recipient_role=recipient.recipient_role,
        tenant_id=recipient.tenant_id, actor_type=(actor_type or None),
        actor_id=str(actor_id)[:64] if actor_id is not None else None,
        event_type=event, entity_type=(str(entity_type)[:60] if entity_type else None),
        entity_id=(str(entity_id)[:100] if entity_id is not None else None),
        title_template_key=template_key, body_template_key=template_key,
        safe_parameters=safe_parameters, action_route=action_route,
        action_parameters=safe_action_parameters, priority=priority,
        dedupe_key=stored_key, created_at=now, expires_at=_aware(expires_at),
    )
    normalized_channels = tuple(dict.fromkeys(str(channel).lower() for channel in channels))
    if not normalized_channels or any(channel not in {"in_app", "email"} for channel in normalized_channels):
        raise ValueError("notification channels must be in_app or email")
    try:
        with session.begin_nested():
            session.add(notification)
            session.flush()
            for channel in normalized_channels:
                session.add(NotificationDelivery(
                    notification_id=notification.id,
                    channel=channel,
                    status="sent" if channel == "in_app" else "pending",
                    attempt_count=1 if channel == "in_app" else 0,
                    next_attempt_at=None if channel == "in_app" else now,
                    sent_at=now if channel == "in_app" else None,
                ))
            session.flush()
    except IntegrityError:
        existing = session.query(Notification).filter_by(dedupe_key=stored_key).first()
        if existing is None:
            raise
        return existing, False
    if commit:
        session.commit()
    return notification, True


def _eligibility(context: RecipientContext):
    role_filters = [
        and_(
            Notification.recipient_type == "role",
            Notification.recipient_role.in_(context.roles),
            or_(Notification.tenant_id.is_(None), Notification.tenant_id == context.tenant_id),
        )
    ] if context.roles else []
    filters = [
        and_(Notification.recipient_type == "user", Notification.recipient_id == str(context.user_id)),
    ]
    if context.tenant_id is not None:
        filters.append(and_(Notification.recipient_type == "tenant", Notification.tenant_id == context.tenant_id))
    filters.extend(role_filters)
    return or_(*filters)


def _feed_query(context: RecipientContext):
    receipt_join = and_(
        NotificationReceipt.notification_id == Notification.id,
        NotificationReceipt.user_id == context.user_id,
    )
    now = _utcnow()
    return (
        db.session.query(Notification, NotificationReceipt)
        .outerjoin(NotificationReceipt, receipt_join)
        .filter(
            _eligibility(context),
            Notification.archived_at.is_(None),
            NotificationReceipt.archived_at.is_(None),
            or_(Notification.expires_at.is_(None), Notification.expires_at > now),
        )
    )


def _read_expression():
    return func.coalesce(NotificationReceipt.read_at, Notification.read_at)


def get_unread_count_for_context(context: RecipientContext) -> int:
    return int(_feed_query(context).filter(_read_expression().is_(None)).count())


def _encode_cursor(created_at: datetime, notification_id: str) -> str:
    payload = json.dumps([_aware(created_at).isoformat(), notification_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created, notification_id = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        value = datetime.fromisoformat(str(created).replace("Z", "+00:00")).astimezone(UTC)
        return value, str(uuid.UUID(str(notification_id)))
    except Exception as exc:
        raise ValueError("invalid notification cursor") from exc


def _render(notification: Notification) -> tuple[str, str]:
    spec = TEMPLATES.get(notification.title_template_key)
    if spec is None:
        return "Notification unavailable", "This notification uses an unsupported template version."
    parameters = {key: str(value) for key, value in (notification.safe_parameters or {}).items()}
    try:
        return spec.title.format_map(parameters), spec.body.format_map(parameters)
    except (KeyError, ValueError):
        return "Notification unavailable", "This notification is missing required display data."


def _action_url(notification: Notification, url_builder: Callable[..., str] | None) -> str | None:
    if not notification.action_route or notification.action_route not in ACTION_ROUTE_ALLOWLIST or url_builder is None:
        return None
    try:
        return url_builder(notification.action_route, **(notification.action_parameters or {}))
    except Exception:
        logger.warning("Notification action route could not be built: %s", notification.action_route)
        return None


def _to_view(notification: Notification, receipt: NotificationReceipt | None, url_builder=None) -> NotificationView:
    title, message = _render(notification)
    sent_via_email = any(
        delivery.channel == "email" and delivery.status == "sent"
        for delivery in notification.deliveries
    )
    return NotificationView(
        id=notification.id, event_type=notification.event_type,
        title=title, message=message, priority=notification.priority,
        created_at=notification.created_at,
        is_read=bool((receipt and receipt.read_at) or notification.read_at),
        sent_via_email=sent_via_email,
        action_url=_action_url(notification, url_builder),
    )


def list_notifications(
    context: RecipientContext,
    *,
    limit: int = 25,
    cursor: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    url_builder: Callable[..., str] | None = None,
) -> NotificationPage:
    limit = min(max(int(limit), 1), 100)
    if status not in {None, "", "unread", "read"}:
        raise ValueError("invalid notification status filter")
    query = _feed_query(context)
    if event_type:
        query = query.filter(Notification.event_type == str(event_type)[:100])
    if status == "unread":
        query = query.filter(_read_expression().is_(None))
    elif status == "read":
        query = query.filter(_read_expression().isnot(None))
    if date_from:
        query = query.filter(Notification.created_at >= _aware(date_from))
    if date_to:
        query = query.filter(Notification.created_at < _aware(date_to))
    if cursor:
        created_at, notification_id = _decode_cursor(cursor)
        query = query.filter(or_(
            Notification.created_at < created_at,
            and_(Notification.created_at == created_at, Notification.id < notification_id),
        ))
    rows = query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = tuple(_to_view(notification, receipt, url_builder) for notification, receipt in rows)
    next_cursor = _encode_cursor(rows[-1][0].created_at, rows[-1][0].id) if has_more and rows else None
    return NotificationPage(items, next_cursor, get_unread_count_for_context(context))


def list_recent_billing_activity(*, limit: int = 30) -> tuple[OperationalNotificationView, ...]:
    """Return real unified billing events for the superadmin monitor."""
    notifications = (
        Notification.query
        .filter(Notification.event_type.like("billing.%"))
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(min(max(int(limit), 1), 100))
        .all()
    )
    result = []
    for notification in notifications:
        title, _ = _render(notification)
        result.append(OperationalNotificationView(
            id=notification.id,
            tenant=notification.target_tenant,
            notification_type=notification.event_type.split(".")[-1],
            title=title,
            sent_via_email=any(
                delivery.channel == "email" and delivery.status == "sent"
                for delivery in notification.deliveries
            ),
            created_at=notification.created_at,
        ))
    return tuple(result)


def _authorized_notification(context: RecipientContext, reference: str | int):
    query = _feed_query(context)
    if isinstance(reference, int) or str(reference).isdigit():
        query = query.filter(Notification.legacy_notification_id == int(reference))
    else:
        try:
            normalized = str(uuid.UUID(str(reference)))
        except ValueError:
            return None
        query = query.filter(Notification.id == normalized)
    return query.first()


def _receipt_for(notification: Notification, context: RecipientContext) -> NotificationReceipt:
    receipt = NotificationReceipt.query.filter_by(
        notification_id=notification.id, user_id=context.user_id
    ).first()
    if receipt is None:
        receipt = NotificationReceipt(notification_id=notification.id, user_id=context.user_id)
        try:
            with db.session.begin_nested():
                db.session.add(receipt)
                db.session.flush()
        except IntegrityError:
            receipt = NotificationReceipt.query.filter_by(
                notification_id=notification.id, user_id=context.user_id
            ).one()
    return receipt


def mark_read_for_context(reference: str | int, context: RecipientContext, *, commit: bool = True) -> bool:
    row = _authorized_notification(context, reference)
    if row is None:
        return False
    notification, receipt = row
    receipt = receipt or _receipt_for(notification, context)
    receipt.read_at = receipt.read_at or _utcnow()
    if commit:
        db.session.commit()
    return True


def mark_all_read_for_context(context: RecipientContext, *, commit: bool = True) -> int:
    rows = _feed_query(context).filter(_read_expression().is_(None)).all()
    now = _utcnow()
    for notification, receipt in rows:
        receipt = receipt or _receipt_for(notification, context)
        receipt.read_at = now
    if commit:
        db.session.commit()
    return len(rows)


def archive_for_context(reference: str | int, context: RecipientContext, *, commit: bool = True) -> bool:
    row = _authorized_notification(context, reference)
    if row is None:
        return False
    notification, receipt = row
    receipt = receipt or _receipt_for(notification, context)
    receipt.archived_at = receipt.archived_at or _utcnow()
    if commit:
        db.session.commit()
    return True


def feed_etag(context: RecipientContext) -> str:
    query = _feed_query(context)
    count = query.count()
    latest_created = query.with_entities(func.max(Notification.created_at)).scalar()
    latest_receipt = (
        db.session.query(func.max(func.coalesce(NotificationReceipt.archived_at, NotificationReceipt.read_at)))
        .filter(NotificationReceipt.user_id == context.user_id)
        .scalar()
    )
    raw = f"{context.user_id}:{count}:{latest_created}:{latest_receipt}:{get_unread_count_for_context(context)}"
    return '"' + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32] + '"'


def process_pending_email_deliveries(
    *,
    recipient_resolver: Callable[[Notification], str | None],
    sender: Callable[[str, str, str], object],
    limit: int = 25,
) -> dict[str, int]:
    """Lease and deliver pending email outbox rows with bounded retries."""
    now = _utcnow()
    candidates = (
        NotificationDelivery.query
        .filter(
            NotificationDelivery.channel == "email",
            NotificationDelivery.status.in_(["pending", "failed", "processing"]),
            or_(NotificationDelivery.next_attempt_at.is_(None), NotificationDelivery.next_attempt_at <= now),
        )
        .order_by(NotificationDelivery.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(min(max(int(limit), 1), 100))
        .all()
    )
    for delivery in candidates:
        delivery.status = "processing"
        delivery.attempt_count += 1
        delivery.next_attempt_at = now + timedelta(minutes=10)
    db.session.commit()

    result = {"claimed": len(candidates), "sent": 0, "failed": 0, "dead": 0}
    for delivery in candidates:
        try:
            recipient = recipient_resolver(delivery.notification)
            if not recipient:
                raise ValueError("recipient_unavailable")
            title, body = _render(delivery.notification)
            response = sender(recipient, title, body)
            ok = response[0] if isinstance(response, tuple) else bool(response)
            provider_id = response[1] if isinstance(response, tuple) and len(response) > 1 else None
            if not ok:
                raise RuntimeError("provider_rejected")
            delivery.status = "sent"
            delivery.sent_at = _utcnow()
            delivery.next_attempt_at = None
            delivery.provider_message_id = str(provider_id)[:255] if provider_id else None
            delivery.error_class = None
            delivery.error_detail = None
            result["sent"] += 1
        except Exception as exc:
            dead = delivery.attempt_count >= MAX_DELIVERY_ATTEMPTS
            delivery.status = "dead" if dead else "failed"
            delivery.error_class = type(exc).__name__[:80]
            delivery.error_detail = str(exc)[:300]
            delivery.next_attempt_at = None if dead else _utcnow() + timedelta(
                minutes=min(60, 2 ** delivery.attempt_count)
            )
            result["dead" if dead else "failed"] += 1
        db.session.commit()
    return result


def purge_notification_retention(*, now: datetime | None = None, limit: int = 500) -> int:
    """Apply the documented bounded retention policy.

    Expired notifications are eligible immediately. Globally archived rows are
    retained for 90 days, and direct-user rows with shared legacy read state are
    retained for one year. Tenant/role events remain available unless they have
    an explicit expiry because read/archive state is per recipient.
    """
    current = _aware(now) or _utcnow()
    ids = [
        row[0]
        for row in (
            db.session.query(Notification.id)
            .filter(or_(
                and_(Notification.expires_at.isnot(None), Notification.expires_at <= current),
                and_(
                    Notification.archived_at.isnot(None),
                    Notification.archived_at <= current - timedelta(days=90),
                ),
                and_(
                    Notification.recipient_type == "user",
                    Notification.read_at.isnot(None),
                    Notification.read_at <= current - timedelta(days=365),
                ),
            ))
            .order_by(Notification.created_at.asc())
            .limit(min(max(int(limit), 1), 5000))
            .all()
        )
    ]
    if not ids:
        return 0
    deleted = Notification.query.filter(Notification.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    return int(deleted)


# Compatibility API retained for one release. It writes only the new table.
def create_notification(
    tenant_id: int, notification_type: str, title: str, message: str,
    subscription_id: int | None = None, sent_via_email: bool = False,
    commit: bool = False,
):
    return publish_notification(
        recipient=Recipient.tenant(tenant_id),
        event_type=f"legacy.{str(notification_type).lower()}",
        template_key="legacy.literal", parameters={"title": title, "message": message},
        dedupe_key=f"compat:{notification_type}:{uuid.uuid4()}",
        entity_type="subscription" if subscription_id else None,
        entity_id=subscription_id, action_route="admin.notifications",
        channels=("in_app", "email") if sent_via_email else ("in_app",),
        commit=commit,
    )[0]


def _tenant_context(tenant_id: int, user_id: int | None = None) -> RecipientContext:
    return RecipientContext.tenant_admin(user_id=int(user_id or -1), tenant_id=int(tenant_id))


def get_unread_count(tenant_id: int, user_id: int | None = None) -> int:
    return get_unread_count_for_context(_tenant_context(tenant_id, user_id))


def get_notifications(tenant_id: int, limit: int = 20, unread_only: bool = False, user_id: int | None = None):
    return list_notifications(
        _tenant_context(tenant_id, user_id), limit=limit,
        status="unread" if unread_only else None,
    ).items


def mark_notification_read(notification_id, tenant_id: int, user_id: int | None = None) -> bool:
    return mark_read_for_context(notification_id, _tenant_context(tenant_id, user_id))


def mark_all_read(tenant_id: int, user_id: int | None = None) -> int:
    return mark_all_read_for_context(_tenant_context(tenant_id, user_id))


def get_expiry_warning(tenant_id: int) -> dict | None:
    try:
        from app.models.portfolio import Subscription
        from app.services.renewal_scheduler import _days_until_expiry, _is_monthly, _is_yearly_or_longer
        sub = Subscription.current(tenant_id)
        if not sub or sub.status != "active":
            return None
        days_left = _days_until_expiry(sub)
        if days_left is None:
            return None
        if _is_monthly(sub) and 0 < days_left <= 7:
            return {"days_left": days_left, "plan": sub.plan or "Subscription", "threshold": 7}
        if _is_yearly_or_longer(sub) and 0 < days_left <= 30:
            return {"days_left": days_left, "plan": sub.plan or "Subscription", "threshold": 30}
        return None
    except Exception:
        logger.exception("get_expiry_warning failed")
        return None
