"""Recipient-scoped notification center for platform administrators."""
from datetime import datetime, timedelta, timezone

from flask import Response, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.superadmin.blueprint import superadmin, superadmin_required
from app.services.notification_service import (
    RecipientContext,
    archive_for_context,
    feed_etag,
    list_notifications,
    mark_all_read_for_context,
    mark_read_for_context,
)


def _context() -> RecipientContext:
    return RecipientContext.superadmin(user_id=int(current_user.id))


def _date(raw: str | None, *, end: bool = False):
    value = str(raw or "").strip()
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return parsed + timedelta(days=1) if end else parsed


@superadmin.route("/notifications")
@superadmin_required
def notifications():
    try:
        page = list_notifications(
            _context(),
            limit=25,
            cursor=request.args.get("cursor"),
            event_type=request.args.get("event_type") or None,
            status=request.args.get("status") or None,
            date_from=_date(request.args.get("date_from")),
            date_to=_date(request.args.get("date_to"), end=True),
            url_builder=url_for,
        )
    except ValueError:
        flash("The notification cursor or date filter is invalid.", "warning")
        return redirect(url_for("superadmin.notifications"))
    return render_template(
        "superadmin/notifications.html",
        notifications=page.items,
        unread_count=page.unread_count,
        next_cursor=page.next_cursor,
        event_type=request.args.get("event_type", ""),
        status_filter=request.args.get("status", ""),
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
    )


@superadmin.route("/notifications/mark-all-read", methods=["POST"])
@superadmin_required
def notifications_mark_all_read():
    mark_all_read_for_context(_context())
    return redirect(url_for("superadmin.notifications"))


@superadmin.route("/notifications/<string:notification_id>/read", methods=["POST"])
@superadmin_required
def notification_mark_read(notification_id: str):
    mark_read_for_context(notification_id, _context())
    return redirect(url_for("superadmin.notifications"))


@superadmin.route("/notifications/<string:notification_id>/archive", methods=["POST"])
@superadmin_required
def notification_archive(notification_id: str):
    archive_for_context(notification_id, _context())
    return redirect(url_for("superadmin.notifications"))


@superadmin.route("/api/notifications/feed")
@superadmin_required
def api_notifications_feed():
    context = _context()
    etag = feed_etag(context)
    if request.headers.get("If-None-Match") == etag:
        response = Response(status=304)
    else:
        page = list_notifications(context, limit=5, url_builder=url_for)
        response = jsonify({
            "unread_count": page.unread_count,
            "notifications": [
                {
                    "id": item.id,
                    "type": item.event_type,
                    "title": item.title,
                    "message": item.message,
                    "is_read": item.is_read,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "action_url": item.action_url,
                }
                for item in page.items
            ],
        })
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    return response
