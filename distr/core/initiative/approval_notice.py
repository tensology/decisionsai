"""Plain-language approval requests sent once to the active surface.

One place for three things the orchestrator used to do badly:

* say *which ticket* it wants to do *what* to, in a sentence a person can answer
  ("I want to move ticket 335, EFT banking details, from Backlog to In Progress and start Pi on it
  to edit the checkout code. The card is waiting in Telegram - say approve or decline here, or tap it there.");
* ask on the active surface (remote app, else Telegram, else desktop), nudging once;
* answer an approve / decline said on any surface for the one pending approval, and update the others.

The Telegram card and the Tensology Incoming copy always exist for the record; this module only decides who is
told, and in what words.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()

# Lanes the orchestrator moves tickets through while it is doing the work. Moving between them is routine:
# do it, then say so. Finishing, archiving or deleting is never routine.
ROUTINE_TARGET_LANES = frozenset({"in progress", "doing", "wip", "backlog", "to do", "todo", "qa", "review", "testing"})


def ticket_label(ticket_id: Any = None, title: str = "", *, closed: bool = False) -> str:
    """"ticket 335, EFT banking details" - the number and the name, whichever are known. ``closed`` adds the comma
    that ends the name when more words follow ("move ticket 335, EFT banking details, to In Progress")."""
    name = re.sub(r"\s+", " ", str(title or "")).strip().rstrip(".")
    try:
        number = int(ticket_id) if ticket_id not in (None, "", 0) else None
    except (TypeError, ValueError):
        number = None
    if number and name:
        return f"ticket {number}, {name}" + ("," if closed else "")
    if number:
        return f"ticket {number}"
    return (name + ("," if closed and name else "")) or "this work"


def card_line(surface: str = "remote") -> str:
    """Where the card is and how to answer on this surface."""
    if surface == "telegram":
        return "Tap Approve or Decline on the card."
    if surface == "desktop":
        return "The card is waiting in Telegram - say approve or decline, or tap it there."
    return "The card is waiting in Telegram - say approve or decline here, or tap it there."


def compose_ask(wants: str, *, why: str = "", surface: str = "remote") -> tuple[str, str]:
    """(written text, spoken text): what I want to do, why, and how to answer. Short and natural."""
    wants = re.sub(r"\s+", " ", str(wants or "")).strip().rstrip(".")
    why = re.sub(r"\s+", " ", str(why or "")).strip().rstrip(".")
    body = f"I want to {wants}" + (f", {why}" if why else "") + "."
    text = f"{body} {card_line(surface)}"
    return text, text.replace(" - ", ", ")


def inform_text(done: str) -> str:
    """Routine work that was just done: a past-tense line, no question."""
    done = re.sub(r"\s+", " ", str(done or "")).strip()
    return done if done.endswith((".", "!")) else done + "."


# --- what counts as routine ---------------------------------------------------------------------------------------

def is_routine_lane_move(action_type: str, context: dict[str, Any] | None, settings: dict[str, Any] | None = None) -> bool:
    """A single-ticket move between working lanes is the orchestrator's own bookkeeping: do it, then tell.

    Not routine: several tickets at once, finishing/archiving/deleting, or a move with no named lane.
    ``initiative_routine_lane_moves_inform_only`` (default on) turns the whole rule off."""
    if str(action_type or "").lower() != "ticket_lane_move":
        return False
    if str((settings or {}).get("initiative_routine_lane_moves_inform_only", True)).strip().lower() in {"0", "false", "no", "off"}:
        return False
    ctx = context or {}
    ids = ctx.get("ticket_ids") or ([ctx["ticket_id"]] if ctx.get("ticket_id") else [])
    lane = str(ctx.get("target_lane") or "").strip().lower()
    return len(ids) == 1 and lane in ROUTINE_TARGET_LANES


# --- active surface ------------------------------------------------------------------------------------------------

def _telegram_manager() -> Any:
    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        return _telegram_manager_from_app()
    except Exception:
        return None


def primary_surface(manager: Any = None, *, allow_telegram: bool = True, now: float | None = None) -> str | None:
    """"remote", "telegram" or "desktop", from recent activity (logged at info by the router).

    Approval asks default to desktop when neither the remote app nor Telegram is active, so a stuck
    approval is never left with nowhere to go."""
    try:
        from distr.core.notification_routing import choose_notification_route

        route = choose_notification_route(
            telegram_manager=manager if manager is not None else _telegram_manager(),
            allow_telegram=allow_telegram,
            now=now,
            strict_presence=True,
        )
        return route.surface if route else None
    except Exception:
        logger.debug("presence routing failed", exc_info=True)
        return None


# --- one nudge per ask --------------------------------------------------------------------------------------------

def _ledger_path() -> Path:
    base = Path(os.environ.get("DECISIONS_DB_DIR") or os.path.expanduser("~/.decisions"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "approval_asks.json"


def _ledger() -> dict[str, Any]:
    try:
        return json.loads(_ledger_path().read_text())
    except Exception:
        return {}


def _claim(key: str, surface: str) -> bool:
    """True the first time this ask is made; False for every later nudge of the same ask."""
    with _LOCK:
        state = _ledger()
        if key in state:
            return False
        state[key] = {"at": time.time(), "surface": surface}
        if len(state) > 500:
            for old in sorted(state, key=lambda k: state[k].get("at", 0))[:100]:
                state.pop(old, None)
        path = _ledger_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(path)
        return True


def forget_ask(key: str) -> None:
    with _LOCK:
        state = _ledger()
        if state.pop(key, None) is not None:
            _ledger_path().write_text(json.dumps(state))


_SOURCE_WORDS = {"telegram": "on Telegram", "tensology": "in Tensology", "remote": "from the remote app", "desktop": "on the desktop"}


def announce_decision(*, approved: bool, ticket_id: Any, title: str = "", source: str = "telegram", key: str = "") -> dict[str, Any]:
    """An approval was settled on ``source``. The Telegram card and Tensology copy are updated by the caller;
    this keeps the active surface in step ("Approved on Telegram: ticket 335, EFT banking details. Pi is
    starting."). Silent when he is on the surface that made the decision, or on Telegram (the card shows it)."""
    surface = primary_surface()
    if surface in (None, "telegram") or surface == source:
        logger.info("approval %s settled on %s; nothing to tell surface=%s", key or ticket_id, source, surface)
        return {"surface": surface, "sent": False}
    verb = "Approved" if approved else "Declined"
    tail = " Pi is starting on it." if approved else " Nothing was started."
    return inform_after(f"{verb} {_SOURCE_WORDS.get(source, source)}: {ticket_label(ticket_id, title)}.{tail}",
                        key=f"decision:{key or ticket_id}", surface=surface)


def _deliver(surface: str, spoken: str, written: str, *, data: dict[str, Any] | None = None) -> bool:
    try:
        if surface == "remote":
            from distr.core.integrations.telegram.remote_tts_delivery import enqueue_remote_tts_delivery

            return bool(enqueue_remote_tts_delivery(spoken, data={
                "mode": "proactive", "source_command": "approval_ask", "explicit_notification_intent": True,
                **(data or {}),
            }))
        if surface == "desktop":
            from distr.core.signals import speak_text_directly_event_queue

            speak_text_directly_event_queue(spoken)
            return True
        return False  # Telegram already holds the card itself
    except Exception:
        logger.debug("approval ask delivery failed on %s", surface, exc_info=True)
        return False


def ask_once(key: str, text: str, voice: str | None = None, *, deliver: bool = True,
             surface: str | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tell the user once what is waiting for approval on the active surface.

    The Telegram card (and Tensology copy) exist already; this adds the plain-words ask on the remote app or
    the desktop when that is where he is. A repeat of the same ``key`` does nothing."""
    chosen = surface or primary_surface()
    if chosen is None:
        logger.info("approval ask %s: no active surface, left on the Telegram card", key)
        return {"surface": None, "sent": False, "first": False}
    if not _claim(key, chosen):
        logger.info("approval ask %s: already asked, not nudging again", key)
        return {"surface": chosen, "sent": False, "first": False}
    sent = _deliver(chosen, voice or text, text, data=data) if deliver else False
    logger.info("approval ask %s: surface=%s delivered=%s", key, chosen, sent)
    return {"surface": chosen, "sent": sent, "first": True}


def inform_after(done: str, *, key: str | None = None, surface: str | None = None) -> dict[str, Any]:
    """Report the orchestrator's completed action on the active surface."""
    text = inform_text(done)
    chosen = surface or primary_surface()
    if chosen in (None, "telegram"):
        # Telegram or nobody around: a quiet line in the activity feed is enough, no ping.
        logger.info("inform-only: %s (surface=%s, not spoken)", text, chosen)
        return {"surface": chosen, "sent": False}
    if key and not _claim(f"inform:{key}", chosen):
        return {"surface": chosen, "sent": False}
    sent = _deliver(chosen, text, text, data={"source_command": "orchestrator_inform"})
    logger.info("inform-only: %s (surface=%s delivered=%s)", text, chosen, sent)
    return {"surface": chosen, "sent": sent}


def preparing_status(what: str = "") -> str:
    """A factual line for the moment before a tool runs: the request is queued, no work has started.

    This is the pre-execution acknowledgment. Nothing here claims progress; the confirmed-start line is
    ``working_or_queued_line(started=True, ...)`` once the job actually begins."""
    what = re.sub(r"\s+", " ", str(what or "")).strip().rstrip(".")
    return f"Queued: {what}." if what else "Queued: getting ready to start."


def working_or_queued_line(*, started: bool, what: str, waiting_on: str = "") -> str:
    """Say "Working on it" only after execution starts. Report queued or waiting status until then.

    The remote app and the desktop both spoke "Working on it." the moment a request was understood, even when
    nothing had started and the work was sitting behind an approval. This is the one line that decides between
    the two, from the caller's honest ``started`` flag."""
    what = re.sub(r"\s+", " ", str(what or "")).strip().rstrip(".")
    if started:
        return "Working on it."
    tail = f" Waiting on {waiting_on}." if waiting_on else " Nothing has started yet."
    return f"Queued: {what}.{tail}"


# --- workflow waits -----------------------------------------------------------------------------------------------

def workflow_wait_ask(*, waiting_kind: str, ticket_id: Any = None, ticket_title: str = "", workflow_name: str = "",
                      step_name: str = "", project_name: str = "", surface: str = "remote") -> tuple[str, str] | None:
    """Return the workflow approval request, if this checkpoint requires approval."""
    label = ticket_label(ticket_id, ticket_title or workflow_name, closed=True)
    project = str(project_name or "").strip()
    where = f" in {project}" if project and project.lower() not in label.lower() else ""
    label = label.rstrip(",") if not where else label
    step = str(step_name or "").strip()
    kind = (waiting_kind or "").strip().lower()
    if kind == "approval":
        return compose_ask(f"carry on with {label}{where}", why=(f"it paused at {step} for your sign-off" if step else
                                                               "it paused for your sign-off"), surface=surface)
    if kind == "run_briefing":
        return compose_ask(f"start work on {label}{where}", why="it is ready to go", surface=surface)
    if kind == "route_approval":
        return compose_ask(f"change how {label}{where} is run", why="the planned route needs your okay", surface=surface)
    if kind in {"", "feedback", "step_review"}:
        return None
    return None


# --- approving / declining from any surface ------------------------------------------------------------------------

def pending_approvals(*, now: float | None = None) -> list[dict[str, Any]]:
    """Pending Pi write proposals and workflow checkpoints, newest first.

    Stale actions (older than 48h, or tied to a closed / deprecated ticket) are expired first and never
    returned, so stale work does not generate a request."""
    try:
        expire_stale_approvals(now=now)
    except Exception:
        logger.debug("stale approval cleanup failed", exc_info=True)
    items: list[dict[str, Any]] = []
    try:
        from distr.core.initiative import pi_write_proposals as pw

        for tid, rec in pw._load().items():
            if rec.get("status") == "pending":
                items.append({"kind": "proposal", "ref": str(tid), "ticket_id": int(tid), "title": rec.get("title") or "",
                              "at": float(rec.get("created") or 0)})
    except Exception:
        logger.debug("pending proposals unavailable", exc_info=True)
    try:
        from distr.core.workflow.interactions import pending_interactions

        for row in pending_interactions():
            items.append({"kind": "workflow", "ref": row["token"], "run_id": row.get("run_id"), "title": "",
                          "interaction": row, "at": float(row.get("created_at") or 0)})
    except Exception:
        logger.debug("pending workflow checkpoints unavailable", exc_info=True)
    items.sort(key=lambda i: i["at"], reverse=True)
    return items


def _match(items: list[dict[str, Any]], ticket_id: Any) -> list[dict[str, Any]]:
    if ticket_id in (None, "", 0):
        return items
    try:
        wanted = int(ticket_id)
    except (TypeError, ValueError):
        return []
    return [i for i in items if i.get("ticket_id") == wanted]


# --- stale cleanup (48h / closed tickets) -------------------------------------------------------------------------

STALE_APPROVAL_S = 48 * 60 * 60
CLOSED_LANE_WORDS = ("complete", "done", "archived", "archive", "deprecated", "cancelled", "canceled",
                     "closed", "wontfix", "wont do", "duplicate")
CLOSED_WORKFLOW_STATES = {"completed", "cancelled", "canceled", "failed", "archived"}


def _closed_ticket_ids() -> set[int]:
    """Tickets sitting in a closed / archived / deprecated lane, or finished / cancelled by workflow state."""
    try:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanLane, KanbanTicket

        with get_session() as db:
            rows = (db.query(KanbanTicket.id, KanbanLane.name, KanbanTicket.workflow_status)
                    .join(KanbanLane, KanbanTicket.lane_id == KanbanLane.id).all())
        closed: set[int] = set()
        for tid, lane, workflow_status in rows:
            if any(word in str(lane or "").lower() for word in CLOSED_LANE_WORDS):
                closed.add(int(tid))
            elif str(workflow_status or "").strip().lower() in CLOSED_WORKFLOW_STATES:
                closed.add(int(tid))
        return closed
    except Exception:
        return set()


def expire_stale_approvals(*, now: float | None = None, max_age_s: float = STALE_APPROVAL_S) -> list[dict[str, Any]]:
    """Expire pending proposals older than ``max_age_s`` (48h) or tied to a closed / deprecated ticket.

    Returns the items that were expired. Retiring also closes the live Telegram card (buttons gone) and
    marks the Decisions / Tensology mirror as Expired, without a new ask."""
    now = float(now if now is not None else time.time())
    closed = _closed_ticket_ids()
    try:
        from distr.core.initiative import pi_write_proposals as pw

        state = pw._load() or {}
    except Exception:
        return []
    expired: list[dict[str, Any]] = []
    for key, rec in list(state.items()):
        if not isinstance(rec, dict) or rec.get("status") != "pending":
            continue
        try:
            tid: int | None = int(key)
        except (TypeError, ValueError):
            tid = None
        created = float(rec.get("created") or 0)
        reason = ""
        if created and now - created > float(max_age_s):
            reason = f"waiting more than {int(max_age_s // 3600)}h"
        elif tid is not None and tid in closed:
            reason = "its ticket is closed or deprecated"
        if not reason:
            continue
        rec["status"] = "expired"
        rec["expired_at"] = now
        rec["expired_reason"] = reason
        state[key] = rec
        expired.append({"ticket_id": tid, "title": rec.get("title") or "", "reason": reason})
    if expired:
        pw._save(state)
        for row in expired:
            pw._retire_expired(row["ticket_id"], state.get(str(row["ticket_id"])) or {})
        logger.info("approval_notice: expired %d stale pending action(s): %s", len(expired),
                    ", ".join(str(e["ticket_id"]) for e in expired))
    return expired


def _draft_ticket_ids(entry: Any) -> list[int]:
    payload = getattr(entry, "execute_payload", None)
    if not isinstance(payload, dict):
        return []
    action = payload.get("action")
    if isinstance(action, dict) and isinstance(action.get("payload"), dict):
        payload = action["payload"]
    raw = payload.get("ticket_ids") or ([payload["ticket_id"]] if payload.get("ticket_id") else [])
    ids: list[int] = []
    for value in raw if isinstance(raw, list) else []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(ids))


def draft_stale_reason(entry: Any) -> str:
    """Retire drafts that target closed tickets or an old batch of lane moves."""
    ids = _draft_ticket_ids(entry)
    if getattr(entry, "action_type", None) == "ticket_lane_move" and len(ids) > 1:
        return "each ticket needs its own approval"
    payload = getattr(entry, "execute_payload", None)
    if isinstance(payload, dict) and payload.get("kind") == "initiative_action":
        try:
            created = datetime.fromisoformat(entry.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if time.time() - created.timestamp() > STALE_APPROVAL_S:
                return "waiting more than 48h"
        except (AttributeError, TypeError, ValueError):
            pass
    if ids and any(tid in _closed_ticket_ids() for tid in ids):
        return "its ticket is closed or deprecated"
    return ""


# --- "approve all" (never a blind batch) ---------------------------------------------------------------------------

_APPROVABLE_KINDS = {"proposal", "workflow", "draft"}


def _approve_all_line(number: int, item: dict[str, Any]) -> str:
    """One waiting item, with the exact single-ticket phrase that approves only it."""
    if item.get("kind") == "proposal":
        tid = item.get("ticket_id")
        return f"{number}. {ticket_label(tid, item.get('title'))} - to start it, say 'approve {tid}'."
    if item.get("kind") == "draft":
        ref = str(item.get("ref") or "")
        return f"{number}. {item.get('title') or ref} - to approve it, say 'approve {ref[:8]}'."
    ref = str(item.get("ref") or "")
    return f"{number}. A workflow checkpoint is waiting ({ref[:8]}); open it in Workflows to continue."


def approve_all_preview(*, items: list[dict[str, Any]] | None = None, now: float | None = None,
                        max_age_s: float = STALE_APPROVAL_S) -> dict[str, Any]:
    """List the pending items and ask the user to approve them one at a time.

    "approve all" is never itself an approval: nothing is claimed or dispatched here. Each line shows the
    single-ticket phrase ("approve 335") that the one-approval-key path accepts, so the user's explicit, named
    phrase stays the only proof that dispatches a ticket."""
    if items is None:
        expire_stale_approvals(now=now, max_age_s=max_age_s)
        items = pending_approvals(now=now)
    pool = [i for i in items if i.get("kind") in _APPROVABLE_KINDS]
    if not pool:
        return {"count": 0, "items": [],
                "text": "Nothing is waiting for your approval right now, so there is nothing to approve."}
    body = "\n".join(_approve_all_line(n, item) for n, item in enumerate(pool, start=1))
    text = (f"{len(pool)} item(s) are waiting. Nothing has been approved:\n{body}\n\n"
            "I approve one ticket at a time, so name the one you mean exactly as shown. Nothing starts until you do.")
    return {"count": len(pool), "items": pool, "text": text}


def is_bulk_approval(text: Any) -> bool:
    return isinstance(text, str) and bool(re.fullmatch(
        r"\s*(?:please\s+)?(?:confirm\s+)?approve\s*(?:all|everything)(?:\s+of\s+them)?[.!?\s]*",
        text, flags=re.IGNORECASE,
    ))


def decide_pending(decision: str, *, ticket_id: Any = None, surface: str = "remote",
                   items: list[dict[str, Any]] | None = None,
                   decide_draft: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Apply "approve" / "decline", said on the remote app or desktop, to ONE named item.

    A batch phrase ("approve all", including a bare "confirm approve all") only lists the waiting items and
    asks for one named ticket; it never dispatches. A proposal goes through the same ``decide`` as the
    Telegram button (one dispatch key, card edited, Tensology copy updated); a workflow checkpoint goes
    through its own claim. Never guesses between several."""
    normalized = re.sub(r"\s+", " ", str(decision or "").strip().lower().replace("-", " "))
    if is_bulk_approval(normalized):
        # A batch phrase is never an approval. List the items, ask for one named ticket.
        return approve_all_preview(items=items)
    named = re.fullmatch(r"(approve|decline)(?: ticket)?\s+(\d{1,7})", normalized)
    if named:
        decision, ticket_id = named.group(1), int(named.group(2))
    if decision not in {"approve", "decline"}:
        return {"ok": False, "text": "Say approve or decline, naming the ticket."}
    pool = _match(items if items is not None else pending_approvals(), ticket_id)
    if not pool:
        return {"ok": False, "text": "Nothing is waiting for your approval right now."}
    if len(pool) > 1:
        names = "; ".join(ticket_label(i.get("ticket_id"), i.get("title")) for i in pool[:4])
        return {"ok": False, "ambiguous": True,
                "text": f"{len(pool)} things are waiting: {names}. Which ticket do you mean?"}
    item = pool[0]
    logger.info("approval %s from %s: %s %s", decision, surface, item["kind"], item["ref"])
    if item["kind"] == "draft":
        if decide_draft is None:
            return {"ok": False, "text": "That pending item is managed in Telegram; open it there."}
        try:
            ok = bool(decide_draft(item))
        except Exception:
            logger.warning("draft approval failed for %s", item.get("ref"), exc_info=True)
            ok = False
        return {"ok": ok, "text": ("Approved: " if ok else "I could not approve that item: ") + str(item.get("title") or "")}
    if item["kind"] == "proposal":
        from distr.core.initiative import pi_write_proposals as pw

        rec = pw.get(item["ticket_id"]) or {}
        if rec.get("status") != "pending":
            state = rec.get("status") or "no longer waiting"
            return {"ok": False, "expired": state == "expired",
                    "text": f"That proposal is {state}; nothing was dispatched."}
        out = pw.decide(item["ticket_id"], decision, source=surface)
        return {"ok": True, "text": out.get("text") or "Done."}
    row = item.get("interaction") or {}
    allowed = json.loads(row.get("allowed_actions") or "[]")
    pick = {"approve": ("approve", "continue"), "decline": ("reject", "stop")}[decision]
    action = next((a for a in pick if a in allowed), None)
    if action is None:
        return {"ok": False, "text": "That checkpoint needs a different answer. Open it in Workflows."}
    from distr.core.workflow.interactions import resolve_interaction
    result = resolve_interaction(token=item["ref"], action=action, response_text=action, source="remote_voice") or {}
    if result.get("error") and not result.get("idempotent"):
        return {"ok": False, "text": str(result["error"])}
    return {"ok": True, "text": "Approved. I'm carrying on." if decision == "approve" else "Declined. I've stopped it."}


def on_interaction_resolved(item: dict[str, Any], action: str, source: str,
                            edit: Callable[..., bool] | None = None) -> bool:
    """A workflow checkpoint was answered off Telegram: edit its Telegram card to say so and drop the buttons."""
    if str(source or "").lower().startswith("telegram"):
        return False
    mid, cid = item.get("telegram_message_id"), item.get("telegram_chat_id")
    if not mid:
        return False
    verdict = "Approved" if action in {"approve", "continue"} or str(action).startswith("model_") else "Declined"
    text = f"{verdict} from the {'remote app' if 'remote' in str(source) else 'app'}. This card is closed."
    try:
        if edit is not None:
            return bool(edit(text, mid, cid))
        from distr.core.initiative import outcome_flow as of

        return bool(of.edit_telegram(text, message_id=int(mid), chat_id=cid, require_toggle=False))
    except Exception:
        logger.warning("could not edit the Telegram card for a checkpoint answered elsewhere", exc_info=True)
        return False
