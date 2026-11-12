"""Where a running thread timer segment really ended.

A thread timer segment opens on Play (or when a run starts) and is closed by
auto-pause once the thread is idle. Auto-pause only runs when something polls
the thread, so a timer that nobody looked at for days used to close at the
*latest* reply in the thread. Daily automation threads therefore produced
one multi-day ``auto_pause`` row that swallowed every run in between (for
example 4 Nov 10:07 -> 11 Nov 09:07 SAST for one daily health check).

This module finds the end of the first continuous stretch of work after the
segment start instead:

- a turn is one child chat: prompt at ``created_date``, reply at ``modified_date``;
- the agent may work up to ``max_turn_seconds`` between a prompt and its reply;
- the next prompt continues the same stretch only if it arrives within
  ``idle_gap_seconds`` of the previous reply (or prompt);
- a later prompt or reply after a longer idle gap belongs to a new segment.

It never extends a segment past ``now`` and never invents time before the start.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

IDLE_GAP_SECONDS = 20 * 60          # same idle split the period report uses for CLI sessions
MAX_TURN_SECONDS = 6 * 60 * 60      # same bound the period report treats as a stale timer


def settled_segment_end(
    started_at: datetime,
    turns: Iterable[tuple[datetime | None, datetime | None]],
    *,
    last_activity_at: datetime | None = None,
    now: datetime,
    grace_seconds: int = 2,
    idle_gap_seconds: int = IDLE_GAP_SECONDS,
    max_turn_seconds: int = MAX_TURN_SECONDS,
) -> datetime:
    """Return when the segment that opened at ``started_at`` went idle (plus grace).

    ``turns`` are ``(prompt_at, reply_at)`` pairs for the thread's child chats.
    """
    end = started_at
    points: list[tuple[datetime, datetime]] = []
    for prompt_at, reply_at in turns:
        if reply_at is None and prompt_at is None:
            continue
        prompt_at = prompt_at or reply_at
        reply_at = reply_at or prompt_at
        if reply_at < started_at or prompt_at > now:
            continue
        if reply_at < prompt_at:
            # Inconsistent stamps: trust the recorded reply, never a later prompt stamp.
            prompt_at = reply_at
        # A reply stamp far after its prompt is an unrelated later row update, not work.
        if (reply_at - prompt_at).total_seconds() > max_turn_seconds:
            reply_at = prompt_at
        points.append((max(prompt_at, started_at), min(reply_at, now)))
    if last_activity_at is not None and started_at <= last_activity_at <= now:
        points.append((last_activity_at, last_activity_at))
    for prompt_at, reply_at in sorted(points):
        if (prompt_at - end).total_seconds() > idle_gap_seconds:
            break
        end = max(end, reply_at)
    return min(end + timedelta(seconds=max(0, int(grace_seconds))), now)


def thread_turns(db, chat_id: int, since: datetime) -> list[tuple[datetime | None, datetime | None]]:
    """(prompt, reply) times of replied child chats that finished at or after ``since``."""
    from distr.core.db import Chat

    rows = (
        db.query(Chat.created_date, Chat.modified_date)
        .filter(Chat.parent_id == int(chat_id), Chat.response.isnot(None), Chat.modified_date >= since)
        .order_by(Chat.modified_date.asc(), Chat.id.asc())
        .all()
    )
    return [(created, modified) for created, modified in rows]


def stale_segment_end(db, item, now: datetime, *, grace_seconds: int = 2) -> datetime | None:
    """End of a running segment that already went idle long enough ago to be closed.

    Returns None when the timer is not running or its work is still continuous
    with ``now`` (the next prompt arrived within the idle gap).
    """
    if item.time_paused or not item.time_started_at:
        return None
    end = settled_segment_end(
        item.time_started_at,
        thread_turns(db, int(item.chat_id), item.time_started_at),
        last_activity_at=None,
        now=now,
        grace_seconds=grace_seconds,
    )
    if (now - end).total_seconds() > IDLE_GAP_SECONDS:
        return end
    return None
