"""Ticket lane-change history.

``created_at`` is stored as naive UTC (see ``utc_now_naive``). Convert to the
user's local zone only when displaying.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketLaneHistory
from distr.core.db.time import utc_now_naive


HUMAN_ACTOR_HEADER = "X-Decisions-Actor"
HUMAN_ACTOR_VALUE = "user"

_COMPLETE_NAMES = {"complete", "done", "completed", "closed", "shipped"}


def is_complete_lane_name(name: str | None) -> bool:
    return str(name or "").strip().lower() in _COMPLETE_NAMES


def request_is_human_board_ui(request: Any) -> bool:
    """True only when the board UI sent the human marker and a same-origin hint.

    The board JavaScript is the only caller that may set ``X-Decisions-Actor: user``.
    Agent tools, MCP, CLI helpers, and orchestrator HTTP calls must not send it.
    A matching Origin or Referer host is also required so a spoofed header alone
    is not enough.
    """
    headers = getattr(request, "headers", None)
    if headers is None:
        return False
    actor = str(headers.get("x-decisions-actor") or "").strip().lower()
    if actor != HUMAN_ACTOR_VALUE:
        return False
    try:
        request_host = (request.url.hostname or "").strip().lower()
    except Exception:
        request_host = ""
    if not request_host:
        return False
    for raw in (headers.get("origin"), headers.get("referer")):
        if not raw:
            continue
        host = (urlparse(str(raw)).hostname or "").strip().lower()
        if host == request_host:
            return True
    return False


def lane_name(session: Any, lane_id: int | None) -> str | None:
    if lane_id is None:
        return None
    lane = session.get(KanbanLane, int(lane_id)) if hasattr(session, "get") else session.query(KanbanLane).filter(KanbanLane.id == int(lane_id)).first()
    return str(lane.name).strip() if lane is not None and lane.name else None


def record_lane_change(
    session: Any,
    ticket_id: int,
    *,
    from_lane: str | None,
    to_lane: str | None,
    actor: str,
    reason: str,
    source: str,
) -> KanbanTicketLaneHistory | None:
    """Append one history row. Caller commits. Also refreshes work-item lane metadata."""
    if from_lane == to_lane and from_lane is not None:
        return None
    row = KanbanTicketLaneHistory(
        ticket_id=int(ticket_id),
        from_lane=(str(from_lane).strip() or None) if from_lane is not None else None,
        to_lane=(str(to_lane).strip() or None) if to_lane is not None else None,
        actor=str(actor or "system")[:80],
        reason=str(reason or "")[:120],
        source=str(source or "")[:120],
        created_at=utc_now_naive(),
    )
    session.add(row)
    _sync_work_item_lane(session, int(ticket_id), to_lane)
    return row


def list_lane_history(session: Any, ticket_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        session.query(KanbanTicketLaneHistory)
        .filter(KanbanTicketLaneHistory.ticket_id == int(ticket_id))
        .order_by(KanbanTicketLaneHistory.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )
    return [_history_dict(row) for row in rows]


def recent_lane_history_for_ticket(ticket_id: int | None, *, limit: int = 5) -> list[dict[str, Any]]:
    if ticket_id is None:
        return []
    from distr.core.db import get_session

    with get_session() as session:
        return list_lane_history(session, int(ticket_id), limit=limit)


def current_ticket_lane(ticket_id: int | None) -> str | None:
    if ticket_id is None:
        return None
    from distr.core.db import get_session

    with get_session() as session:
        ticket = session.get(KanbanTicket, int(ticket_id))
        if ticket is None:
            return None
        return lane_name(session, ticket.lane_id)


def _history_dict(row: KanbanTicketLaneHistory) -> dict[str, Any]:
    created = row.created_at
    created_s = created.isoformat() + ("Z" if created is not None and created.tzinfo is None else "") if created is not None else None
    return {
        "id": int(row.id) if row.id is not None else None,
        "ticket_id": int(row.ticket_id),
        "from_lane": row.from_lane,
        "to_lane": row.to_lane,
        "actor": row.actor,
        "reason": row.reason,
        "source": row.source,
        "created_at": created_s,
        "created_at_zone": "UTC",
    }


def _sync_work_item_lane(session: Any, ticket_id: int, to_lane: str | None) -> None:
    try:
        from distr.core.db.workflow import DevelopmentWorkItem
        from distr.core.db import Chat
        from distr.core.workflow.development_threads import development_thread_metadata, _write_legacy_projection
    except Exception:
        return
    items = session.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.local_ticket_id == int(ticket_id)).all()
    lane = (str(to_lane).strip() or None) if to_lane else None
    for item in items:
        item.ticket_lane = lane
        chat = session.get(Chat, int(item.chat_id)) if item.chat_id is not None else None
        if chat is None:
            continue
        metadata = development_thread_metadata(chat)
        try:
            _write_legacy_projection(
                chat,
                workflow_id=metadata.get("workflow_id") or item.workflow_id,
                ticket_id=ticket_id,
                source_type=metadata.get("source_type") or item.source_type,
                source_ref=metadata.get("source_ref"),
                board_key=item.board_key or metadata.get("board_key"),
                board_provider=item.board_provider or metadata.get("board_provider"),
                board_ticket_key=item.ticket_key or metadata.get("board_ticket_key"),
                board_ticket_title=item.ticket_title or metadata.get("board_ticket_title"),
                board_ticket_lane=lane,
            )
        except Exception:
            pass
