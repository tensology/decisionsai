"""Parse and format ticket time_spent values with second precision."""

from __future__ import annotations

import re
from typing import Optional

_TIME_TOKEN_RE = re.compile(r"(\d+)\s*([wdhms])", re.I)


def parse_time_tracking_seconds(value: Optional[str]) -> int:
    """Parse Jira-style duration strings (including seconds) into total seconds."""
    if not value or not str(value).strip():
        return 0
    total = 0
    for amount, unit in _TIME_TOKEN_RE.findall(str(value)):
        count = int(amount)
        label = unit.lower()
        if label == "w":
            total += count * 7 * 24 * 60 * 60
        elif label == "d":
            total += count * 24 * 60 * 60
        elif label == "h":
            total += count * 60 * 60
        elif label == "m":
            total += count * 60
        else:
            total += count
    return total


def format_time_tracking_seconds(seconds: int) -> str:
    """Format seconds as a compact duration string (e.g. 1h 5m 30s)."""
    remaining = max(0, int(seconds or 0))
    if remaining <= 0:
        return ""
    parts: list[str] = []
    for size, label in (
        (7 * 24 * 60 * 60, "w"),
        (24 * 60 * 60, "d"),
        (60 * 60, "h"),
        (60, "m"),
        (1, "s"),
    ):
        if remaining >= size:
            count = remaining // size
            remaining %= size
            parts.append(f"{count}{label}")
    return " ".join(parts)


def add_time_spent_seconds(existing: Optional[str], seconds: int) -> str:
    """Add seconds to an existing time_spent value."""
    total = parse_time_tracking_seconds(existing) + max(0, int(seconds or 0))
    return format_time_tracking_seconds(total)
