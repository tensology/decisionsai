"""Post Kanban ticket lane updates back to the originating chat thread."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def notify_source_chat_ticket_moved(
    ticket_id: int,
    *,
    board_name: str,
    to_lane_name: str,
    from_lane_name: Optional[str] = None,
    reason: str = "workflow_completed",
) -> None:
    """If the ticket has ``source_chat_id``, append an assistant notice + emit chat signals."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket

    chat_id: Optional[int] = None
    title = ""
    try:
        with get_session() as db:
            t = db.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
            if not t:
                return
            cid = getattr(t, "source_chat_id", None)
            if not cid:
                return
            chat_id = int(cid)
            title = (t.title or "").strip() or "Untitled ticket"
    except Exception:
        logger.debug("notify_source_chat_ticket_moved: load ticket failed", exc_info=True)
        return

    board_part = f' on board "{board_name}"' if board_name else ""
    if reason == "manual":
        from_part = f' from "{from_lane_name}"' if from_lane_name else ""
        body = (
            f'Ticket #{ticket_id} "{title}"{board_part} was moved{from_part} '
            f'to lane "{to_lane_name}".'
        )
    else:
        body = (
            f'Ticket #{ticket_id} "{title}"{board_part} advanced to lane "{to_lane_name}" '
            f"after the board workflow completed."
        )

    try:
        from distr.core.chat import ChatService

        ChatService.append_assistant_notice(chat_id, body, hidden=False)
    except Exception:
        logger.debug("notify_source_chat_ticket_moved: persist notice failed", exc_info=True)
        return

    try:
        from distr.core.signals import signal_manager

        signal_manager.chat_message_added.emit(chat_id, "assistant", body)
        signal_manager.chat_updated.emit(chat_id)
    except Exception:
        logger.debug("notify_source_chat_ticket_moved: signal emit failed", exc_info=True)
