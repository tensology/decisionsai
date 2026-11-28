"""Pi write work waits for Paul's approval.

The chat agent's ``pi_agent`` tool used to hand any instruction straight to a Pi
coding session, so code was edited before anyone approved it. Now an instruction
that would change something is filed as a proposal instead:

1. a Backlog ticket on the project's board holds the full instruction (the record
   of what was proposed; nothing runs from Backlog on its own);
2. an Approve / Decline card goes to Telegram (``pw:<ticket>:approve|decline``),
   and the job status line says it is waiting for Paul;
3. only Approve sends that ticket to the project CLI (``send_to_cli``), which runs it
   on the ticket's Development thread with the thread timer running. Decline leaves
   the ticket in Backlog, untouched otherwise.

Read-only questions (inspect, find, report, run the tests) still go to Pi at once.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

CALLBACK_KIND = "pw"
_WRITE_RE = re.compile(
    r"\b(implement\w*|fix(?:es|ed|ing)?|chang(?:e|es|ed|ing)|updat(?:e|es|ed|ing)|edit\w*|modif\w*|refactor\w*|"
    r"add(?:s|ed|ing)?|remov\w*|delet\w*|creat\w*|writ(?:e|es|ing)|rewrit\w*|replac\w*|swap\w*|strip\w*|"
    r"renam\w*|commit\w*|push\w*|deploy\w*|merg\w*|migrat\w*|install\w*|upgrad\w*|apply|applied|patch\w*|"
    r"restart\w*|revert\w*|insert\w*|set up|configure\w*)\b",
    re.IGNORECASE,
)
READ_ONLY_PREFIX = (
    "READ ONLY. Inspect and report only: do not modify, create or delete files, do not commit, push or deploy, "
    "do not run migrations, and do not use sudo or change anything on a server. If the task needs a change, "
    "stop and describe the change instead.\n\n"
)
_LOCK = threading.RLock()


def is_write_instruction(text: str) -> bool:
    """True when the instruction asks Pi to change something. Ambiguous wording counts as a change."""
    return bool(_WRITE_RE.search(str(text or "")))


def _store_path() -> Path:
    base = Path(os.environ.get("DECISIONS_DB_DIR") or os.path.expanduser("~/.decisions"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "pi_write_proposals.json"


def _load() -> dict[str, Any]:
    try:
        return json.loads(_store_path().read_text())
    except Exception:
        return {}


def _save(state: dict[str, Any]) -> None:
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(path)


def get(ticket_id: Any) -> dict[str, Any] | None:
    with _LOCK:
        return _load().get(str(int(ticket_id)))


def _title(instruction: str) -> str:
    first = next((ln.strip() for ln in str(instruction).splitlines() if ln.strip()), "Pi change")
    first = re.sub(r"\s+", " ", first)
    return (first[:117] + "...") if len(first) > 120 else first


def _project_board_id(db: Any, project_id: int) -> int | None:
    from distr.core.db.kanban import KanbanBoard

    board = (
        db.query(KanbanBoard)
        .filter(KanbanBoard.default_project_id == int(project_id), KanbanBoard.archived == False)  # noqa: E712
        .order_by(KanbanBoard.id.asc())
        .first()
    )
    return int(board.id) if board is not None else None


def _report(job: str, kind: str, text: str, title: str | None = None) -> None:
    try:
        from distr.core.initiative.job_progress import report

        report(job, kind, text, title=title)
    except Exception:
        logger.debug("job progress report skipped", exc_info=True)


def _default_notify(text: str, markup: dict[str, Any]) -> bool:
    from distr.core.initiative import outcome_flow as of

    return of.send_telegram(text, reply_markup=markup, require_toggle=False)


def file_write_proposal(project_id: int, project_name: str, instruction: str, *,
                        source_chat_id: int | None = None,
                        notify: Callable[[str, dict[str, Any]], bool] | None = None,
                        _session_provider=None) -> dict[str, Any]:
    """File the proposal (Backlog ticket + Telegram card). Never runs anything. Never raises."""
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanTicket
        from distr.core.kanban.lifecycle import ensure_delivery_lanes

        provider = _session_provider or get_session
        title = _title(instruction)
        with provider() as db:
            board_id = _project_board_id(db, int(project_id))
            if board_id is None:
                return {"status": "blocked", "message": (
                    f"Not started: {project_name} has no board to file the proposal on, so nothing was changed. "
                    "Link a board to the project first.")}
            backlog = ensure_delivery_lanes(db, board_id)["Backlog"]
            max_pos = max([int(t.position or 0) for t in backlog.tickets] or [0])
            ticket = KanbanTicket(
                lane_id=int(backlog.id),
                title=title,
                description=str(instruction).strip(),
                linked_project_id=int(project_id),
                position=max_pos + 1,
                send_to_cli=False,
                context_notes=("Proposal from chat (pi_agent). Waiting for Paul's approval; nothing has been "
                               "changed. Approve on the Telegram card to start it."),
            )
            db.add(ticket)
            db.commit()
            ticket_id = int(ticket.id)
        with _LOCK:
            state = _load()
            state[str(ticket_id)] = {"status": "pending", "project_id": int(project_id), "project": project_name,
                                     "title": title, "source_chat_id": source_chat_id, "created": time.time()}
            _save(state)
        markup = {"inline_keyboard": [[
            {"text": "Approve", "callback_data": f"{CALLBACK_KIND}:{ticket_id}:approve"},
            {"text": "Decline", "callback_data": f"{CALLBACK_KIND}:{ticket_id}:decline"},
        ]]}
        card = (f"Proposal for {project_name} (ticket #{ticket_id}): {title}\n\n"
                f"{str(instruction).strip()[:1500]}\n\nNothing has been changed. Approve to start it.")
        try:
            sent = bool((notify or _default_notify)(card, markup))
        except Exception:
            logger.warning("pi write proposal: Telegram card failed for ticket %s", ticket_id, exc_info=True)
            sent = False
        job = f"ticket:{ticket_id}"
        _report(job, "proposal", f"ticket #{ticket_id} in {project_name} Backlog", title=title)
        _report(job, "waiting", "your approval on the Telegram card", title=title)
        logger.info("pi write proposal filed: ticket=%s project=%s card_sent=%s", ticket_id, project_id, sent)
        where = "and sent you the Approve card on Telegram" if sent else "but the Telegram card did not go out"
        return {"status": "proposed", "ticket_id": ticket_id, "card_sent": sent, "message": (
            f"Proposal filed as ticket #{ticket_id} in {project_name} Backlog {where}. Nothing has been changed. "
            "The work starts only when Paul approves it. Do not start it any other way.")}
    except Exception as exc:
        logger.warning("pi write proposal could not be filed", exc_info=True)
        return {"status": "failed", "message": f"Not started: the proposal could not be filed ({type(exc).__name__}). Nothing was changed."}


def decide(ticket_id: Any, decision: str, *, start: Callable[[int], str] | None = None) -> dict[str, Any]:
    """Apply Paul's Approve / Decline from the card. Approve sends the ticket to the CLI once."""
    if decision not in {"approve", "decline"}:
        return {"text": "Choose Approve or Decline on the proposal card."}
    try:
        tid = int(ticket_id)
    except (TypeError, ValueError):
        return {"text": "That proposal reference is not valid. Nothing changed."}
    with _LOCK:
        state = _load()
        rec = state.get(str(tid))
        if rec is None:
            return {"text": "I don't have that proposal. Nothing changed."}
        if rec.get("status") != "pending":
            return {"text": f"Ticket #{tid} was already {rec.get('status')}. Nothing changed."}
        rec["status"] = "approved" if decision == "approve" else "declined"
        rec["decided"] = time.time()
        _save(state)
    title = rec.get("title") or f"ticket #{tid}"
    if decision == "decline":
        _report(f"ticket:{tid}", "done", "declined; left in Backlog", title=title)
        logger.info("pi write proposal declined: ticket=%s", tid)
        return {"text": f"Declined. Ticket #{tid} stays in Backlog and nothing was changed."}
    logger.info("pi write proposal approved: ticket=%s", tid)
    try:
        if start is not None:
            out = start(tid)
        else:
            from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool

            out = KanbanTicketTool()._action_send_to_cli(tid)
    except Exception:
        logger.warning("pi write proposal: start failed for ticket %s", tid, exc_info=True)
        with _LOCK:
            state = _load()
            if str(tid) in state:
                state[str(tid)]["status"] = "pending"
                _save(state)
        return {"text": f"Approved, but ticket #{tid} could not be started. It is still waiting; try Approve again."}
    spoken = str(out or "").split("\n", 1)[0].strip()
    return {"text": f"Approved. {spoken}" if spoken else f"Approved. Ticket #{tid} has started."}
