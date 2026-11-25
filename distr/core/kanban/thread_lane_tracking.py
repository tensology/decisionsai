"""Keep a Development thread's linked ticket in step with the agent's work.

When an agent turn starts on a project-linked Development thread, the linked
local ticket moves from Backlog (or To Do) to In Progress. When a turn
finishes the work, the ticket moves to QA, or to Done when the board has no
QA lane. Paul validates QA work and makes the final move himself.

Rules:
- Forward only: Backlog -> In Progress -> QA (or Done). Never backwards and
  never from any other lane.
- A move Paul made by hand on the board is never overridden: no automatic move
  within ``MANUAL_MOVE_GRACE`` of a hand move, nor after a hand move made while
  the finishing turn was running.
- Client-synced tickets (Jira, Trello, Tensology) and automation threads are
  left alone. Automations keep their own lifecycle.
- Lanes are never created or renamed; a missing target lane means no move.
- Idempotent: a ticket already past the source lane is not touched again.
- Every move is written to the lane history as actor ``automation``.

Every public function is best effort and never raises into the turn.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable

logger = logging.getLogger(__name__)

MANUAL_MOVE_GRACE = timedelta(minutes=30)
HISTORY_SOURCE = "thread_lane_tracking"

_START_LANES = {"backlog", "to do", "todo", "scoped work", "queue", "queued"}
_IN_PROGRESS_LANES = {"in progress", "current", "doing", "active"}
_QA_LANES = {"qa", "qa / assess", "qa/assess", "review", "testing"}
_CLIENT_SYNCED = {"jira", "trello", "tensology"}
_PENDING_COMMAND_STATUSES = ("queued", "dispatching")
_THREAD_TAB = re.compile(r"^thread-(\d+)$")


def _lane_kind(name: Any) -> str | None:
    from distr.core.kanban.lane_history import is_complete_lane_name

    key = str(name or "").strip().lower()
    if key in _START_LANES:
        return "start"
    if key in _IN_PROGRESS_LANES:
        return "in_progress"
    if key in _QA_LANES:
        return "qa"
    if is_complete_lane_name(key):
        return "done"
    return None


def _root_chat(db, chat_id: int):
    from distr.core.db import Chat

    chat = db.get(Chat, int(chat_id))
    seen: set[int] = set()
    while chat is not None and getattr(chat, "parent_id", None) is not None and int(chat.id) not in seen:
        seen.add(int(chat.id))
        chat = db.get(Chat, int(chat.parent_id))
    return chat


def _linked_ticket(db, root_id: int):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.workflow import DevelopmentWorkItem

    item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == int(root_id)).first()
    ticket = None
    if item is not None and item.local_ticket_id is not None:
        ticket = db.get(KanbanTicket, int(item.local_ticket_id))
    if ticket is None:
        # A chat may now raise several tickets; prefer the newest unmerged one so
        # lane tracking cannot pick an arbitrary (or superseded) row.
        ticket = (
            db.query(KanbanTicket)
            .filter(
                KanbanTicket.source_chat_id == int(root_id),
                KanbanTicket.merged_into_ticket_id.is_(None),
            )
            .order_by(KanbanTicket.id.desc())
            .first()
        )
    return ticket, item


def _skip_reason(ticket, item, board, root) -> str | None:
    if root is None or root.project_id is None:
        return "not_project_thread"
    if item is not None and str(item.source_type or "").strip().lower() == "automation":
        return "automation_thread"
    if item is not None and str(item.board_provider or "local").strip().lower() not in {"", "local"}:
        return "client_synced"
    if ticket is None or board is None:
        return "no_ticket"
    if ticket.merged_into_ticket_id is not None:
        return "merged"
    if bool(getattr(board, "archived", False)):
        return "board_archived"
    if str(board.source or "database").strip().lower() in _CLIENT_SYNCED:
        return "client_synced"
    if str(ticket.external_source or "").strip().lower():
        return "client_synced"
    if str(ticket.source_provider or "").strip().lower() in _CLIENT_SYNCED:
        return "client_synced"
    return None


def _hand_moved_since(db, ticket_id: int, since: datetime) -> bool:
    from distr.core.db.kanban import KanbanTicketLaneHistory

    return (
        db.query(KanbanTicketLaneHistory.id)
        .filter(
            KanbanTicketLaneHistory.ticket_id == int(ticket_id),
            KanbanTicketLaneHistory.actor == "user",
            KanbanTicketLaneHistory.created_at >= since,
        )
        .first()
        is not None
    )


def _has_queued_instructions(db, root_id: int) -> bool:
    from distr.core.db.workflow import DevelopmentCommand

    return (
        db.query(DevelopmentCommand.id)
        .filter(
            DevelopmentCommand.chat_id == int(root_id),
            DevelopmentCommand.status.in_(_PENDING_COMMAND_STATUSES),
        )
        .first()
        is not None
    )


def _publish(board_id: int, ticket_id: int, lane_name: str) -> None:
    try:
        from distr.gui.web.kanban_events import increment_kanban_updated

        increment_kanban_updated(
            int(board_id),
            event_type="ticket_lane_move",
            payload={
                "board_id": int(board_id),
                "ticket_ids": [int(ticket_id)],
                "target_lane": lane_name,
                "source": HISTORY_SOURCE,
            },
        )
    except Exception:
        logger.debug("Board refresh after thread lane move failed", exc_info=True)


def _advance(
    chat_id: int,
    *,
    phase: str,
    started_at: datetime | None,
    now: datetime | None,
    session_provider: Callable | None,
) -> str | None:
    from sqlalchemy import func

    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.time import utc_now_naive
    from distr.core.kanban.lane_history import record_lane_change

    if session_provider is None:
        from distr.core.db import get_session as session_provider

    now = now or utc_now_naive()
    with session_provider() as db:
        root = _root_chat(db, int(chat_id))
        if root is None:
            return None
        ticket, item = _linked_ticket(db, int(root.id))
        current = db.get(KanbanLane, int(ticket.lane_id)) if ticket is not None and ticket.lane_id else None
        board = db.get(KanbanBoard, int(current.board_id)) if current is not None else None
        reason = _skip_reason(ticket, item, board, root)
        if reason:
            logger.debug("Thread %s lane tracking skipped: %s", chat_id, reason)
            return None

        kind = _lane_kind(current.name)
        if phase == "started":
            if kind != "start":
                return None
            wanted = ("in_progress",)
        else:
            if kind != "in_progress":
                return None
            if _has_queued_instructions(db, int(root.id)):
                return None
            wanted = ("qa", "done")

        since = now - MANUAL_MOVE_GRACE
        if started_at is not None and started_at < since:
            since = started_at
        if _hand_moved_since(db, int(ticket.id), since):
            logger.debug("Thread %s lane tracking skipped: ticket moved by hand", chat_id)
            return None

        lanes = (
            db.query(KanbanLane)
            .filter(KanbanLane.board_id == int(board.id))
            .order_by(KanbanLane.position, KanbanLane.id)
            .all()
        )
        target = None
        for wanted_kind in wanted:
            target = next((lane for lane in lanes if _lane_kind(lane.name) == wanted_kind), None)
            if target is not None:
                break
        if target is None or int(target.id) == int(current.id):
            return None

        max_position = (
            db.query(func.max(KanbanTicket.position))
            .filter(KanbanTicket.lane_id == int(target.id))
            .scalar()
        )
        from_name = str(current.name or "").strip() or None
        to_name = str(target.name or "").strip()
        ticket.lane_id = int(target.id)
        ticket.position = (int(max_position) + 1) if max_position is not None else 0
        record_lane_change(
            db,
            int(ticket.id),
            from_lane=from_name,
            to_lane=to_name,
            actor="automation",
            reason="thread_work_started" if phase == "started" else "thread_work_finished",
            source=HISTORY_SOURCE,
        )
        db.commit()
        board_id, ticket_id = int(board.id), int(ticket.id)
    _publish(board_id, ticket_id, to_name)
    return to_name


def track_work_started(
    chat_id: int | None,
    *,
    now: datetime | None = None,
    _session_provider: Callable | None = None,
) -> str | None:
    """Backlog/To Do -> In Progress when an agent turn starts. Returns the new lane or None."""
    if not chat_id:
        return None
    try:
        return _advance(int(chat_id), phase="started", started_at=None, now=now, session_provider=_session_provider)
    except Exception:
        logger.debug("Thread lane tracking (start) failed for chat %s", chat_id, exc_info=True)
        return None


def track_work_finished(
    chat_id: int | None,
    *,
    started_at: datetime | None = None,
    now: datetime | None = None,
    _session_provider: Callable | None = None,
) -> str | None:
    """In Progress -> QA (or Done without a QA lane) when a turn finishes the work."""
    if not chat_id:
        return None
    try:
        return _advance(int(chat_id), phase="finished", started_at=started_at, now=now, session_provider=_session_provider)
    except Exception:
        logger.debug("Thread lane tracking (finish) failed for chat %s", chat_id, exc_info=True)
        return None


def thread_for_cli_pane(project_id: int, terminal_id: str, *, _load_tabs: Callable | None = None) -> int | None:
    """Return the one thread whose CLI pane owns this PTY, or None when unknown or shared."""
    if _load_tabs is None:
        from distr.core.project_cli_backends.pi_cli_tabs import load_project_tabs as _load_tabs

    tabs = (_load_tabs(int(project_id)) or {}).get("tabs") or []
    owners = set()
    for tab in tabs:
        if str(tab.get("process_id") or "") != str(terminal_id):
            continue
        match = _THREAD_TAB.match(str(tab.get("tab_id") or ""))
        if match:
            owners.add(int(match.group(1)))
    return owners.pop() if len(owners) == 1 else None


def track_cli_pane_submit(
    project_id: int,
    terminal_id: str,
    *,
    _load_tabs: Callable | None = None,
    _session_provider: Callable | None = None,
) -> str | None:
    """A prompt was submitted in a thread's CLI pane: treat it as work starting."""
    try:
        chat_id = thread_for_cli_pane(int(project_id), str(terminal_id), _load_tabs=_load_tabs)
        if chat_id is None:
            return None
        if _session_provider is None:
            from distr.core.db import get_session as _session_provider
        with _session_provider() as db:
            root = _root_chat(db, chat_id)
            if root is None or root.project_id is None or int(root.project_id) != int(project_id):
                return None
        return track_work_started(chat_id, _session_provider=_session_provider)
    except Exception:
        logger.debug("CLI pane lane tracking failed for %s", terminal_id, exc_info=True)
        return None
