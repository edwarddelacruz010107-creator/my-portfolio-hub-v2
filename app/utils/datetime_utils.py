"""Timezone-safe datetime helpers.

SQLite commonly returns SQLAlchemy DateTime(timezone=True) values as naive
``datetime`` objects. The application stores UTC timestamps, so comparison
boundaries should normalize DB values to aware UTC before comparing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc_aware(value: datetime | None) -> datetime | None:
    """Normalize a datetime from the DB/application to aware UTC.

    Naive datetimes are treated as UTC for backwards compatibility with
    existing SQLite rows and earlier rows written without timezone info.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_expiry(minutes: int = 0, *, hours: int = 0, days: int = 0, seconds: int = 0) -> datetime:
    """Return an aware UTC expiry time relative to now."""
    return utc_now() + timedelta(minutes=minutes, hours=hours, days=days, seconds=seconds)


def is_expired(value: datetime | None, *, missing_is_expired: bool = True) -> bool:
    """Safely test whether a datetime has expired.

    By default a missing expiry is treated as expired/invalid, which is safer
    for OTPs, password reset codes, and verification links.
    """
    normalized = ensure_utc_aware(value)
    if normalized is None:
        return missing_is_expired
    return utc_now() >= normalized
