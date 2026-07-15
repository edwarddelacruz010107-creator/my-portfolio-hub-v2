"""Explicit founder-dashboard capability and strong reauthentication policy."""
from __future__ import annotations

from datetime import datetime, timezone

from app.utils.datetime_utils import ensure_utc_aware


FOUNDER_DASHBOARD_CAPABILITY = "platform.founder_dashboard.read"
FOUNDER_EXPORT_CAPABILITY = "platform.founder_dashboard.export"
STRONG_REAUTH_MAX_AGE_SECONDS = 600


def has_founder_capability(user, capability: str = FOUNDER_DASHBOARD_CAPABILITY) -> bool:
    return bool(
        capability in {FOUNDER_DASHBOARD_CAPABILITY, FOUNDER_EXPORT_CAPABILITY}
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superadmin", False)
    )


def mark_strong_reauth(session_data, user, *, now: datetime | None = None) -> None:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session_data["founder_reauth_at"] = stamp.timestamp()
    session_data["founder_reauth_user_id"] = int(user.id)


def has_recent_strong_reauth(session_data, user, *, now: datetime | None = None) -> bool:
    if not has_founder_capability(user, FOUNDER_EXPORT_CAPABILITY):
        return False
    if not getattr(user, "totp_enabled", False):
        return False
    try:
        if int(session_data.get("founder_reauth_user_id")) != int(user.id):
            return False
        reauth_at = float(session_data.get("founder_reauth_at"))
    except (TypeError, ValueError):
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if reauth_at > current.timestamp() + 5:
        return False
    if current.timestamp() - reauth_at > STRONG_REAUTH_MAX_AGE_SECONDS:
        return False
    verified_at = ensure_utc_aware(getattr(user, "last_totp_verified_at", None))
    return bool(
        verified_at
        and 0 <= (current - verified_at).total_seconds() <= STRONG_REAUTH_MAX_AGE_SECONDS
    )
