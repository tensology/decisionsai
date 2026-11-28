"""Pi write work waits for Paul's approval.

The chat agent's ``pi_agent`` tool used to hand any instruction straight to a Pi
coding session, so code was edited before anyone approved it. Now an instruction
that would change something is filed as a proposal instead:

1. a Backlog ticket on the project's board holds the full instruction (the record
   of what was proposed; nothing runs from Backlog on its own);
2. an Approve / Decline card goes to Telegram (``pw:<ticket>:approve|decline``),
   and the job status line says it is waiting for Paul;
3. the same proposal is mirrored to Tensology's Incoming as its own "decisions" item
   (no second Telegram card from Tensology);
4. only Approve sends that ticket to the project CLI (``send_to_cli``), which runs it
   on the ticket's Development thread with the thread timer running. Decline leaves
   the ticket where it is, untouched otherwise, and dismisses the Tensology item.

A chat ``send_to_cli`` on an existing ticket whose work would change code goes through
the same card (``request_ticket_approval``).

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


def _default_post_incoming(payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    from distr.core.tensology_client import create_curated_work_request

    return create_curated_work_request(payload, idempotency_key=idempotency_key)


def _default_dismiss_incoming(tensology_id: int) -> Any:
    from distr.core.tensology_client import decide_work_request

    return decide_work_request(int(tensology_id), "dismiss")


def _default_accept_incoming(tensology_id: int, decisions_ticket_id: int | None = None) -> Any:
    from distr.core.tensology_client import decide_work_request

    return decide_work_request(int(tensology_id), "accept_linked", decisions_ticket_id=decisions_ticket_id)


def _default_edit_card(text: str, message_id: Any, chat_id: Any) -> bool:
    if not message_id:
        return False
    from distr.core.initiative import outcome_flow as of

    return of.edit_telegram(text, message_id=message_id, chat_id=chat_id, require_toggle=False)


def _as_telegram_id(value: Any) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _remember_card(ticket_id: int, message_id: Any = None, chat_id: Any = None) -> None:
    mid, cid = _as_telegram_id(message_id), _as_telegram_id(chat_id)
    if mid is None and cid is None:
        return
    with _LOCK:
        state = _load()
        rec = state.get(str(ticket_id))
        if rec is None:
            return
        if mid is not None:
            rec["telegram_message_id"] = mid
        if cid is not None:
            rec["telegram_chat_id"] = cid
        _save(state)


def _last_telegram_ids() -> tuple[Any, Any]:
    try:
        from distr.core.initiative import outcome_flow as of

        manager = of._telegram_manager()
        if not manager:
            return None, None
        return getattr(manager, "last_telegram_message_id", None), getattr(manager, "last_telegram_chat_id", None)
    except Exception:
        return None, None


def _resolve_card(rec: dict[str, Any] | None, ticket_id: int, *, text: str,
                  telegram_message_id: Any = None, telegram_chat_id: Any = None,
                  edit: Callable[..., bool] | None = None) -> dict[str, Any]:
    """Edit the Approve / Decline card to show it is handled, and drop its buttons."""
    mid = _as_telegram_id(telegram_message_id) or _as_telegram_id((rec or {}).get("telegram_message_id"))
    cid = _as_telegram_id(telegram_chat_id) or _as_telegram_id((rec or {}).get("telegram_chat_id"))
    _remember_card(ticket_id, mid, cid)
    edited = False
    try:
        edited = bool((edit or _default_edit_card)(text, mid, cid)) if mid else False
    except Exception:
        logger.warning("pi write proposal: could not edit Telegram card for ticket %s", ticket_id, exc_info=True)
        edited = False
    out: dict[str, Any] = {"text": text, "reply_markup": {"inline_keyboard": []}}
    if edited:
        out["suppress"] = True
        out["edit_message_id"] = mid
    return out


def _mirror_to_incoming(ticket_id: int, project_id: int, title: str, instruction: str,
                        post: Callable[[dict[str, Any], str], dict[str, Any]]) -> None:
    """Put the proposal in Tensology's Incoming too (its own "decisions" item). Best effort; never raises.
    Tensology sends no second Telegram card for it: the Decisions card above is the one to answer."""
    try:
        data = post({
            "origin": "decisions", "origin_ref": f"decisions-ticket-{ticket_id}",
            "decisions_project_id": int(project_id), "title": title, "instruction": str(instruction).strip(),
            "description": (f"Proposed in Decisions as ticket #{ticket_id}. Nothing has been changed. "
                            "Approve or decline it on the Decisions Telegram card."),
            "kind": "pi_write", "priority": "medium",
            "rationale": "Raised from a Decisions chat; waiting for Paul's approval.",
        }, f"pi-write-{ticket_id}") or {}
        tensology_id = data.get("id") if isinstance(data, dict) else None
        with _LOCK:
            state = _load()
            if str(ticket_id) in state and tensology_id is not None:
                state[str(ticket_id)]["tensology_id"] = int(tensology_id)
                _save(state)
        logger.info("pi write proposal mirrored to Tensology Incoming: ticket=%s tensology_id=%s", ticket_id, tensology_id)
    except Exception:
        logger.warning("pi write proposal: Tensology Incoming mirror failed for ticket %s", ticket_id, exc_info=True)


def _announce(ticket_id: int, project_id: int, project_name: str, title: str, instruction: str, *,
              source_chat_id: int | None, notify: Callable[[str, dict[str, Any]], bool] | None,
              post_incoming: Callable[[dict[str, Any], str], dict[str, Any]] | None, sync: bool) -> bool:
    """Record the pending proposal, send the Approve / Decline card and mirror it to Tensology Incoming."""
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
    mid = cid = None
    try:
        result = (notify or _default_notify)(card, markup)
        if isinstance(result, dict):
            sent = bool(result.get("ok", True))
            mid, cid = result.get("message_id"), result.get("chat_id")
        else:
            sent = bool(result)
            mid, cid = _last_telegram_ids()
    except Exception:
        logger.warning("pi write proposal: Telegram card failed for ticket %s", ticket_id, exc_info=True)
        sent = False
    _remember_card(ticket_id, mid, cid)
    args = (ticket_id, project_id, title, instruction, post_incoming or _default_post_incoming)
    if sync:
        _mirror_to_incoming(*args)
    else:
        threading.Thread(target=_mirror_to_incoming, args=args, name=f"pi-write-incoming-{ticket_id}", daemon=True).start()
    job = f"ticket:{ticket_id}"
    _report(job, "proposal", f"ticket #{ticket_id} in {project_name}", title=title)
    _report(job, "waiting", "your approval on the Telegram card", title=title)
    return sent


def file_write_proposal(project_id: int, project_name: str, instruction: str, *,
                        source_chat_id: int | None = None,
                        notify: Callable[[str, dict[str, Any]], bool] | None = None,
                        post_incoming: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
                        _sync: bool = False,
                        _session_provider=None) -> dict[str, Any]:
    """File the proposal (Backlog ticket + Telegram card + Tensology Incoming). Never runs anything. Never raises."""
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
        sent = _announce(ticket_id, project_id, project_name, title, instruction, source_chat_id=source_chat_id,
                         notify=notify, post_incoming=post_incoming, sync=_sync)
        logger.info("pi write proposal filed: ticket=%s project=%s card_sent=%s", ticket_id, project_id, sent)
        where = "and sent you the Approve card on Telegram" if sent else "but the Telegram card did not go out"
        return {"status": "proposed", "ticket_id": ticket_id, "card_sent": sent, "message": (
            f"Proposal filed as ticket #{ticket_id} in {project_name} Backlog {where}. Nothing has been changed. "
            "The work starts only when Paul approves it. Do not start it any other way.")}
    except Exception as exc:
        logger.warning("pi write proposal could not be filed", exc_info=True)
        return {"status": "failed", "message": f"Not started: the proposal could not be filed ({type(exc).__name__}). Nothing was changed."}


def request_ticket_approval(ticket_id: int, project_id: int, project_name: str, title: str, instruction: str, *,
                            source_chat_id: int | None = None,
                            notify: Callable[[str, dict[str, Any]], bool] | None = None,
                            post_incoming: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
                            _sync: bool = False) -> dict[str, Any]:
    """An existing ticket was sent to the CLI from chat and its work would change code: hold it for the
    same Approve / Decline card instead of starting it. One card per ticket while it waits. Never raises."""
    try:
        tid = int(ticket_id)
        existing = get(tid)
        if existing and existing.get("status") == "pending":
            return {"status": "waiting", "ticket_id": tid, "message": (
                f"Ticket #{tid} is already waiting for Paul's approval on Telegram. Nothing was started.")}
        sent = _announce(tid, project_id, project_name, str(title or f"Ticket #{tid}")[:120], instruction,
                         source_chat_id=source_chat_id, notify=notify, post_incoming=post_incoming, sync=_sync)
        logger.info("send_to_cli held for approval: ticket=%s project=%s card_sent=%s", tid, project_id, sent)
        where = "I sent you the Approve card on Telegram" if sent else "the Telegram card did not go out"
        return {"status": "proposed", "ticket_id": tid, "card_sent": sent, "message": (
            f"Ticket #{tid} would change code, so it waits for Paul's approval; {where}. Nothing was started. "
            "Do not start it any other way.")}
    except Exception as exc:
        logger.warning("send_to_cli approval request failed for ticket %s", ticket_id, exc_info=True)
        return {"status": "failed", "message": f"Not started: the approval card could not be prepared ({type(exc).__name__})."}


def decide(ticket_id: Any, decision: str, *, start: Callable[[int], str] | None = None,
           dismiss_incoming: Callable[[int], Any] | None = None,
           accept_incoming: Callable[..., Any] | None = None,
           telegram_message_id: Any = None, telegram_chat_id: Any = None,
           edit_card: Callable[..., bool] | None = None) -> dict[str, Any]:
    """Apply Paul's Approve / Decline (Telegram card or Tensology Incoming). Dispatch at most once."""
    if decision not in {"approve", "decline"}:
        return {"text": "Choose Approve or Decline on the proposal card."}
    try:
        tid = int(ticket_id)
    except (TypeError, ValueError):
        return {"text": "That proposal reference is not valid. Nothing changed."}
    _remember_card(tid, telegram_message_id, telegram_chat_id)
    with _LOCK:
        state = _load()
        rec = state.get(str(tid))
        if rec is None:
            return {"text": "I don't have that proposal. Nothing changed."}
        if rec.get("status") != "pending":
            text = f"Handled. Ticket #{tid} was already {rec.get('status')}. This card is closed."
            return _resolve_card(rec, tid, text=text, telegram_message_id=telegram_message_id,
                                 telegram_chat_id=telegram_chat_id, edit=edit_card)
        rec["status"] = "approved" if decision == "approve" else "declined"
        rec["decided"] = time.time()
        rec["dispatch_key"] = f"pi-write-dispatch-{tid}"
        _save(state)
    title = rec.get("title") or f"ticket #{tid}"
    if decision == "decline":
        _report(f"ticket:{tid}", "done", "declined; nothing started", title=title)
        logger.info("pi write proposal declined: ticket=%s", tid)
        if rec.get("tensology_id"):
            try:
                (dismiss_incoming or _default_dismiss_incoming)(int(rec["tensology_id"]))
            except Exception:
                logger.warning("pi write proposal: could not dismiss Tensology item %s", rec.get("tensology_id"), exc_info=True)
        text = f"Declined. Ticket #{tid} was not started and nothing was changed. This card is closed."
        return _resolve_card(rec, tid, text=text, telegram_message_id=telegram_message_id,
                             telegram_chat_id=telegram_chat_id, edit=edit_card)
    logger.info("pi write proposal approved: ticket=%s key=pi-write-dispatch-%s", tid, tid)
    try:
        if start is not None:
            out = start(tid)
        else:
            from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool

            out = KanbanTicketTool()._action_send_to_cli(tid, approved=True)
    except Exception:
        logger.warning("pi write proposal: start failed for ticket %s", tid, exc_info=True)
        with _LOCK:
            state = _load()
            if str(tid) in state:
                state[str(tid)]["status"] = "pending"
                state[str(tid)].pop("dispatch_key", None)
                _save(state)
        return {"text": f"Approved, but ticket #{tid} could not be started. It is still waiting; try Approve again."}
    with _LOCK:
        state = _load()
        if str(tid) in state:
            state[str(tid)]["dispatched"] = True
            state[str(tid)]["dispatched_at"] = time.time()
            _save(state)
            rec = state[str(tid)]
    if rec.get("tensology_id"):
        try:
            (accept_incoming or _default_accept_incoming)(int(rec["tensology_id"]), tid)
        except Exception:
            logger.warning("pi write proposal: could not mark Tensology item %s as accepted", rec.get("tensology_id"), exc_info=True)
    spoken = str(out or "").split("\n", 1)[0].strip()
    text = (f"Approved. {spoken}" if spoken else f"Approved. Ticket #{tid} has started.")
    if "This card is closed." not in text:
        text = text.rstrip(".") + ". This card is closed."
    return _resolve_card(rec, tid, text=text, telegram_message_id=telegram_message_id,
                         telegram_chat_id=telegram_chat_id, edit=edit_card)


def _ticket_for_remote_event(event: dict[str, Any]) -> int | None:
    raw = event.get("request_id") if event.get("request_id") is not None else event.get("id")
    try:
        tensology_id = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        tensology_id = None
    thread = str(event.get("thread_id") or event.get("source_thread_id") or "")
    with _LOCK:
        state = _load()
        if tensology_id is not None:
            for key, rec in state.items():
                if isinstance(rec, dict) and rec.get("tensology_id") == tensology_id:
                    try:
                        return int(key)
                    except (TypeError, ValueError):
                        return None
        match = re.fullmatch(r"decisions:decisions-ticket-(\d+)", thread)
        if match and match.group(1) in state:
            return int(match.group(1))
    return None


def handle_remote_decision(event: Any, *, start: Callable[[int], str] | None = None,
                           edit_card: Callable[..., bool] | None = None) -> dict[str, Any] | None:
    """A Tensology Incoming decision on a Decisions-origin proposal: resolve the card and dispatch once.

    Approve / send marks the local proposal approved and starts the existing ticket if it has not
    already been started. Dismiss leaves it declined. Unknown events are ignored. Never raises.
    """
    try:
        if not isinstance(event, dict):
            return None
        tid = _ticket_for_remote_event(event)
        if tid is None:
            return None
        status = str(event.get("status") or "").strip().lower()
        if status == "dismissed":
            return decide(tid, "decline", start=start, edit_card=edit_card)
        if status in {"approved", "sent_to_decisions"}:
            return decide(tid, "approve", start=start, edit_card=edit_card)
        return None
    except Exception:
        logger.warning("pi write proposal: remote decision failed", exc_info=True)
        return None
