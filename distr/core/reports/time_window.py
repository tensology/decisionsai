"""Read stored timer rows for one Johannesburg window, including rows History hides.

This does not create, delete, or relabel anything. A stored title is not an outcome.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

SAST = ZoneInfo("Africa/Johannesburg")


def window_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    if end < start:
        raise ValueError("end must be on or after start")
    lo = datetime(start.year, start.month, start.day, tzinfo=SAST).astimezone(UTC).replace(tzinfo=None)
    hi_local = datetime(end.year, end.month, end.day, tzinfo=SAST) + timedelta(days=1)
    hi = hi_local.astimezone(UTC).replace(tzinfo=None)
    return lo, hi


def list_timer_rows(start: date, end: date, get_session=None) -> dict:
    """Same overlap rule as the period chart, without the History reset cutoff."""
    from distr.core.db import Chat
    from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem

    lo, hi = window_bounds(start, end)
    if get_session is None:
        from distr.core.reports.service import get_session as get_session
    with get_session() as db:
        latest_reset: dict[int, int] = {}
        for chat_id, reset_id in (
            db.query(DevelopmentTimeEntry.chat_id, DevelopmentTimeEntry.id)
            .filter(DevelopmentTimeEntry.source == "reset")
            .all()
        ):
            cid, rid = int(chat_id), int(reset_id)
            if cid not in latest_reset or rid > latest_reset[cid]:
                latest_reset[cid] = rid
        rows = (
            db.query(DevelopmentTimeEntry, DevelopmentWorkItem, Chat)
            .outerjoin(DevelopmentWorkItem, DevelopmentTimeEntry.work_item_id == DevelopmentWorkItem.id)
            .outerjoin(Chat, Chat.id == DevelopmentTimeEntry.chat_id)
            .filter(
                DevelopmentTimeEntry.source != "reset",
                DevelopmentTimeEntry.started_at < hi,
                DevelopmentTimeEntry.ended_at > lo,
            )
            .order_by(DevelopmentTimeEntry.started_at.asc(), DevelopmentTimeEntry.id.asc())
            .all()
        )
        items = []
        for entry, item, chat in rows:
            if entry.started_at is None or entry.ended_at is None:
                continue
            raw = (entry.ended_at - entry.started_at).total_seconds()
            project_id = None
            project_provenance = "unknown"
            if item is not None and item.project_id:
                project_id = int(item.project_id)
                project_provenance = "work_item"
            else:
                seen: set[int] = set()
                current = chat
                first = True
                while current is not None and int(current.id) not in seen:
                    seen.add(int(current.id))
                    if current.project_id:
                        project_id = int(current.project_id)
                        project_provenance = "chat" if first else "parent_chat"
                        break
                    first = False
                    current = db.get(Chat, current.parent_id) if current.parent_id else None
            reset_at = latest_reset.get(int(entry.chat_id))
            items.append({
                "durable_id": f"time:{int(entry.id)}",
                "id": int(entry.id),
                "chat_id": int(entry.chat_id),
                "work_item_id": int(entry.work_item_id),
                "started_at": entry.started_at.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                "ended_at": entry.ended_at.strftime("%Y-%m-%dT%H:%M:%S.%f"),
                "stored_seconds": int(entry.seconds or 0),
                "derived_seconds": int(raw) if raw >= 0 else None,
                "source": str(entry.source or ""),
                "project_id": project_id,
                "project_provenance": project_provenance,
                "stored_ticket_title": (item.ticket_title if item is not None else None) or None,
                "hidden_from_history": bool(reset_at is not None and int(entry.id) <= reset_at),
                "chart_keeps_row": bool(raw >= 1),
                "evidence": "stored timer row",
            })
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "count": len(items),
        "items": items,
        "not_outcome_evidence": True,
    }
