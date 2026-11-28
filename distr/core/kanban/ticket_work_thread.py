"""Development thread, work item and timer for a ticket that is being worked.

A ticket that moves to In Progress, or is sent to a project CLI, gets its own
Development thread and work item (``ensure_ticket_work_thread``). A CLI run on
that ticket carries the thread's chat id, so the run is linked to the thread and
the thread timer counts it: ``start_ticket_run_time`` when the run starts and
``finish_ticket_run_time`` when it ends, which records the time entry.

Every function here is best effort and never raises: the board move or the
dispatch must still happen when the thread or timer cannot be set up.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def ensure_ticket_work_thread(ticket_id: Any, *, _session_provider=None) -> int | None:
    """Return the ticket's Development thread id, creating the thread and work item if needed."""
    try:
        ticket_id = int(ticket_id)
    except (TypeError, ValueError):
        return None
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
        from distr.core.workflow.development_threads import (
            ensure_development_thread,
            is_development_thread,
            ticket_development_thread_ids,
        )

        provider = _session_provider or get_session
        with provider() as db:
            ticket = db.get(KanbanTicket, ticket_id)
            if ticket is None:
                return None
            owners = ticket_development_thread_ids(db, ticket.id, None)
            if owners:
                return int(owners[0])
            if ticket.source_chat_id is not None:
                if is_development_thread(db, int(ticket.source_chat_id)):
                    return int(ticket.source_chat_id)
                # A ticket created from a chat conversation records that chat here, but the field
                # names the ticket's owning Development thread. Release it so the thread can own it.
                logger.info("Ticket %s: releasing non-thread source chat %s so its Development thread can own it",
                            ticket.id, ticket.source_chat_id)
                ticket.source_chat_id = None
                db.commit()
            lane = db.get(KanbanLane, ticket.lane_id) if ticket.lane_id else None
            board = db.get(KanbanBoard, lane.board_id) if lane is not None else None
            project_id = ticket.linked_project_id or (getattr(board, "default_project_id", None) if board else None)
            if not project_id:
                logger.info("Ticket %s has no project; no Development thread created", ticket.id)
                return None
            title = (ticket.title or "").strip() or f"Ticket #{ticket.id}"
            lane_name = lane.name if lane is not None else None
            board_id = int(board.id) if board is not None else None
        chat_id = ensure_development_thread(
            title=title,
            project_id=int(project_id),
            ticket_id=ticket_id,
            source_type="prompt",
            board_key=f"decisions:{board_id}" if board_id is not None else None,
            board_provider="local",
            board_ticket_key=str(ticket_id),
            board_ticket_title=title,
            board_ticket_lane=lane_name,
            _session_provider=_session_provider,
        )
        logger.info("Ticket %s is worked in Development thread %s", ticket_id, chat_id)
        return int(chat_id)
    except Exception:
        logger.warning("Could not ensure a Development thread for ticket %s", ticket_id, exc_info=True)
        return None


def start_ticket_run_time(chat_id: Any, *, _session_provider=None) -> bool:
    """Start (or keep running) the thread timer because a run on it has started."""
    if not chat_id:
        return False
    try:
        from distr.core.workflow.development_control import resume_thread_time

        resume_thread_time(int(chat_id), _session_provider=_session_provider)
        return True
    except Exception:
        logger.warning("Could not start the timer for Development thread %s", chat_id, exc_info=True)
        return False


def finish_ticket_run_time(chat_id: Any, *, finished_at: datetime | None = None, _session_provider=None) -> bool:
    """Pause the thread timer because the run ended, recording the segment as a time entry.

    Leaves the timer running when another run still holds the thread. Returns True when a
    running segment was closed.
    """
    if not chat_id:
        return False
    try:
        from distr.core.db import get_session
        from distr.core.db.time import utc_now_naive
        from distr.core.db.workflow import DevelopmentWorkItem
        from distr.core.workflow.development_control import (
            _close_running_segment,
            _sync_ticket_time,
            _thread_has_live_run,
        )

        now = utc_now_naive()
        with (_session_provider or get_session)() as db:
            item = db.query(DevelopmentWorkItem).filter_by(chat_id=int(chat_id)).first()
            if item is None or item.time_paused or not item.time_started_at:
                return False
            if _thread_has_live_run(db, int(chat_id), now):
                return False
            end = min(max(finished_at or now, item.time_started_at), now)
            item.time_accumulated_seconds = _close_running_segment(db, item, end, "auto_pause")
            item.time_started_at = None
            item.time_paused = True
            item.time_last_activity_at = end
            _sync_ticket_time(db, item, int(item.time_accumulated_seconds or 0))
            db.commit()
            return True
    except Exception:
        logger.warning("Could not pause the timer for Development thread %s", chat_id, exc_info=True)
        return False
