"""Post Kanban ticket lane updates back to the originating chat thread."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_PENDING_TICKET_NOTICES = "source_chat_ticket_notices"
_COMMITTED_NOTICE_TRANSACTION = "committed_source_chat_ticket_notice_transaction"


def queue_source_chat_ticket_notice(
    session: Session, ticket_id: int, *, to_lane: str, from_lane: str | None = None, reason: str = "automation",
) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending = session.info.setdefault(_PENDING_TICKET_NOTICES, {})
    pending.setdefault(transaction, []).append((ticket_id, from_lane, to_lane, reason))


@event.listens_for(Session, "after_commit")
def _mark_committed_ticket_notices(session: Session) -> None:
    pending = session.info.get(_PENDING_TICKET_NOTICES)
    if not pending:
        return
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction.nested:
        # Releasing a savepoint does not commit the outer lane move.
        pending.setdefault(transaction.parent, []).extend(pending.pop(transaction, ()))
    else:
        session.info[_COMMITTED_NOTICE_TRANSACTION] = transaction


@event.listens_for(Session, "after_transaction_end")
def _finish_ticket_notices(session: Session, transaction) -> None:
    pending = session.info.get(_PENDING_TICKET_NOTICES)
    if pending is None:
        return
    notices = pending.pop(transaction, ())
    if not pending:
        session.info.pop(_PENDING_TICKET_NOTICES, None)
    if session.info.get(_COMMITTED_NOTICE_TRANSACTION) is not transaction:
        return
    # The original connection has been returned to the pool before notifying.
    session.info.pop(_COMMITTED_NOTICE_TRANSACTION)
    for ticket_id, from_lane, to_lane, reason in notices:
        try:
            notify_source_chat_ticket_moved(ticket_id, board_name="", from_lane_name=from_lane,
                                            to_lane_name=to_lane, reason=reason)
        except Exception:
            logger.debug("Could not publish committed ticket notice", exc_info=True)


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

    if reason == "manual":
        body = (
            f"Ticket moved: {from_lane_name} to {to_lane_name}."
            if from_lane_name
            else f"Ticket moved to {to_lane_name}."
        )
    elif reason == "created":
        body = (
            f"Ticket created in {to_lane_name}."
            if to_lane_name
            else "Ticket created on the project board."
        )
    else:
        body = (
            f"Ticket moved: {from_lane_name} to {to_lane_name}."
            if from_lane_name
            else f"Ticket moved to {to_lane_name}."
        )

    try:
        from distr.core.chat import ChatService

        chat_row_id = ChatService.append_assistant_notice(chat_id, body, hidden=False)
    except Exception:
        logger.debug("notify_source_chat_ticket_moved: persist notice failed", exc_info=True)
        return

    try:
        from distr.core.signals import signal_manager

        signal_manager.chat_message_added.emit(chat_id, "assistant", body, chat_row_id)
        signal_manager.chat_updated.emit(chat_id)
    except Exception:
        logger.debug("notify_source_chat_ticket_moved: signal emit failed", exc_info=True)
