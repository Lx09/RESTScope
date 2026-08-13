"""Time helpers for DB rows."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Normalize timestamps returned by dialects that drop timezone metadata."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
