"""Wall-clock expiry for the exact source messages retained as proposal evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


ACTIVE_STATUSES = frozenset({'', 'pending', 'candidate', 'expired'})


def source_instant(value: Any) -> datetime | None:
    if value in (None, '') or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return datetime.fromtimestamp(number / 1000 if number > 10_000_000_000 else number, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        try:
            instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None


def proposal_source_instants(proposal: dict[str, Any]) -> list[datetime]:
    """Only messages actually retained as evidence may define its source window."""
    ids = {str(value) for value in proposal.get('message_ids') or proposal.get('source_message_ids') or [] if value}
    return [instant for row in proposal.get('evidence') or []
            if isinstance(row, dict) and (not ids or str(row.get('id') or '') in ids)
            and (instant := source_instant(row.get('sent_at'))) is not None]


def proposal_source_started_at(proposal: dict[str, Any]) -> datetime | None:
    """A collection starts at its earliest referenced message, not generation."""
    supplied = source_instant(proposal.get('source_started_at'))
    if supplied is not None:
        return supplied
    times = proposal_source_instants(proposal)
    return min(times) if times else None


def proposal_source_order_key(proposal: dict[str, Any]) -> tuple[bool, float, str]:
    """Newest source collections first; unknown source dates remain last."""
    started = proposal_source_started_at(proposal)
    return (started is None, -started.timestamp() if started else 0,
            str(proposal.get('id') or proposal.get('dedupe_key') or ''))


def proposal_expiry(proposal: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Never substitute proposal creation or unrelated conversation activity."""
    times = proposal_source_instants(proposal)
    source_at = max(times) if times else None
    expires = source_at + timedelta(hours=96) if source_at else None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    status = str(proposal.get('stored_status') or proposal.get('status') or 'pending').lower()
    expired = bool(status in ACTIVE_STATUSES and expires and current >= expires)
    return {**proposal, 'source_message_at': source_at.isoformat() if source_at else None,
            'expires_at': expires.isoformat() if expires else None, 'is_expired': expired}
