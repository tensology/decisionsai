"""Timestamp helpers for persisted database rows."""

from datetime import UTC, datetime


def utc_now_naive() -> datetime:
    """Return UTC now using the modern aware clock, stored as naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def utc_from_timestamp_naive(timestamp: float) -> datetime:
    """Return a timestamp as naive UTC for existing SQLite DateTime columns."""
    return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
