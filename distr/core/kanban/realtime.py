"""Publish Kanban/Development changes to subscribed web clients.

The board UI keeps a `/api/tickets/ws/boards` socket open and refreshes on
``kanban_updated``. Ticket/board writes made through the API, the agent's ticket
tool, lane tracking or automation must all publish, or an open board only
updates on a manual reload.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def publish_kanban_changed(
    *,
    board_id: Optional[int] = None,
    ticket_id: Optional[int] = None,
    event: str = "ticket_changed",
    **extra: Any,
) -> None:
    """Best-effort broadcast of a board/ticket change to open board pages.

    Never raises: a missing/stopped server loop (tests, headless tooling) is a
    silent no-op.
    """
    try:
        from distr.gui.web.kanban_events import increment_kanban_updated

        payload: dict[str, Any] = {k: v for k, v in extra.items() if v is not None}
        if ticket_id is not None:
            payload["ticket_id"] = int(ticket_id)
        increment_kanban_updated(
            int(board_id) if board_id is not None else None,
            event_type=event,
            payload=payload,
        )
    except Exception:
        logger.debug("kanban change broadcast failed", exc_info=True)
