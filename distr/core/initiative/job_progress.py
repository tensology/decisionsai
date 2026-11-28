"""Step-by-step job narration for Paul.

A *job* is one piece of work Decisions is doing for him: a chat request run from Telegram
or the remote app, a Development thread / workflow run, or a client-issue pipeline.
Instead of one final answer, each step is reported as it happens ("picked up X",
"created proposal", "created thread", "started step: ...", "pushed ...", "blocked: ...",
"waiting your approval", "done: ...").

Routing (one place per update, never both):

* the remote app has a live session -> a ``remote_app_event`` (``topic: job_status``)
  over the existing relay socket; the remote app shows it as a status line;
* otherwise Telegram: ONE status message per job, created on the first step and then
  edited in place (``edit_message``), never a message per step.

Updates are deduped (an identical line in a row becomes ``xN``), coalesced and
rate-limited so Telegram and the relay are never flooded.

Sources, all optional and independent:

* :func:`report` - the shared emitter. Any module (the client-issue pipeline, proposal
  flow, ...) can call ``report(job, kind, text, title=..., parent=...)``; it never raises.
* :func:`on_orchestration_event` - bridges the orchestrator ledger (workflow runs, steps,
  approvals, needs-input, failures, intake tickets) via the EventBus.
* :func:`on_chat_turn_event` - bridges the tool steps of a chat turn that came in from
  Telegram or the remote app (``chat_turns.create_event``).

``install()`` subscribes the EventBus bridge once at startup.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger("distr.core.initiative.job_progress")

KINDS = ("picked_up", "proposal", "thread", "step", "pushed", "blocked", "waiting", "done", "failed", "info")
_STATE_FOR_KIND = {"blocked": "blocked", "waiting": "waiting", "done": "done", "failed": "failed"}
_LABEL = {
    "picked_up": "Picked up",
    "proposal": "Created proposal",
    "thread": "Created thread",
    "step": "",
    "pushed": "Pushed",
    "blocked": "Blocked",
    "waiting": "Waiting for you",
    "done": "Done",
    "failed": "Failed",
    "info": "",
}
_STATE_WORD = {"running": "working", "waiting": "waiting for you", "blocked": "blocked", "done": "done", "failed": "failed"}
_TERMINAL_STATES = {"done", "failed"}
_URGENT_KINDS = {"blocked", "waiting", "done", "failed"}

TOKEN_PREFIX = "job-status:"
MIN_EDIT_INTERVAL_S = 5.0      # between two updates of the same job
URGENT_EDIT_INTERVAL_S = 1.5   # blocked / waiting / done / failed go out almost at once
GLOBAL_WINDOW_S = 60.0
GLOBAL_MAX_UPDATES = 20        # across all jobs per window; the relay allows 60/min in total
ACK_WAIT_S = 20.0              # how long to wait for Telegram's message id before sending a new message
MAX_DELIVERY_FAILURES = 3      # then the update is dropped (no Telegram / relay): never retry forever
MAX_LINES = 8
MAX_LINE_CHARS = 140
MAX_TITLE_CHARS = 70
JOB_TTL_S = 6 * 3600
ROUND_CAP_PREFIX = "I hit my limit of"

_LOCK = threading.RLock()


@dataclass
class Line:
    ts: float
    kind: str
    text: str
    count: int = 1


@dataclass
class Job:
    key: str
    title: str = ""
    lines: list[Line] = field(default_factory=list)
    state: str = "running"
    created: float = 0.0
    updated: float = 0.0
    last_flush: float = 0.0
    dirty: bool = False
    urgent: bool = False
    timer: threading.Timer | None = None
    # Telegram
    token: str = ""
    message_id: int | None = None
    chat_id: Any = None
    awaiting_ack: bool = False
    sent_at: float = 0.0
    sent_text: str = ""
    failures: int = 0


_jobs: dict[str, Job] = {}
_alias: dict[str, str] = {}
_flush_times: list[float] = []
_installed = False
_turns: dict[tuple[int, int], dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Sinks (module-level so tests can replace them)
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


_enabled_cache: tuple[float, bool] = (0.0, True)


def enabled() -> bool:
    """Kill switch: the ``job_progress_enabled`` setting (default on). Cached for 15 s."""
    global _enabled_cache
    now = _now()
    if now - _enabled_cache[0] < 15.0:
        return _enabled_cache[1]
    try:
        from distr.core.utils import load_settings_from_db

        value = (load_settings_from_db() or {}).get("job_progress_enabled")
        flag = True if value is None else str(value).strip().lower() not in {"0", "false", "no", "off"}
    except Exception:
        flag = True
    _enabled_cache = (now, flag)
    return flag


def _remote_active() -> bool:
    try:
        from distr.core.integrations.telegram.remote_chat_events import _remote_recently_active

        return bool(_remote_recently_active())
    except Exception:
        return False


def _remote_push(payload: dict[str, Any]) -> bool:
    try:
        from distr.core.integrations.telegram.remote_chat_events import _send

        return bool(_send({"type": "remote_app_event", "data": payload}))
    except Exception:
        logger.debug("job_progress: remote push failed", exc_info=True)
        return False


def _manager():
    try:
        from distr.core.initiative.outcome_flow import _telegram_manager

        return _telegram_manager()
    except Exception:
        return None


def _telegram_send(text: str, token: str) -> bool:
    manager = _manager()
    if manager is None:
        return False
    try:
        return bool(manager.send_to_telegram(text, interaction_token=token))
    except Exception:
        logger.warning("job_progress: Telegram send failed", exc_info=True)
        return False


def _telegram_edit(text: str, message_id: int, chat_id: Any) -> bool:
    manager = _manager()
    if manager is None or not hasattr(manager, "edit_telegram_message"):
        return False
    try:
        return bool(manager.edit_telegram_message(text, message_id=message_id, chat_id=chat_id))
    except Exception:
        logger.warning("job_progress: Telegram edit failed", exc_info=True)
        return False


def _ticket_title(ticket_id: int) -> str:
    try:
        from sqlalchemy import text

        from distr.core.db import get_session

        with get_session() as db:
            row = db.execute(text("select title from kanban_tickets where id=:i"), {"i": int(ticket_id)}).first()
        return str(row[0]) if row and row[0] else ""
    except Exception:
        return ""


def _step_name(step_id: int) -> str:
    try:
        from sqlalchemy import text

        from distr.core.db import get_session

        with get_session() as db:
            row = db.execute(text("select name from auto_workflow_steps where id=:i"), {"i": int(step_id)}).first()
        return str(row[0]) if row and row[0] else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _clean(text: Any, limit: int) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    try:
        from distr.core.chat_turns import redact_text
        from distr.core.initiative.outcome_flow import humanize

        raw = humanize(redact_text(raw, limit=limit * 2), max_len=limit * 2, strict=False)
    except Exception:
        pass
    raw = " ".join(raw.split())
    if len(raw) > limit:
        raw = raw[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.") + "…"
    return raw


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _line_text(line: Line) -> str:
    label = _LABEL.get(line.kind, "")
    body = line.text
    if label and body:
        body = f"{label}: {body}"
    elif label:
        body = label
    if line.count > 1:
        body += f" x{line.count}"
    return body


def render(job: Job) -> str:
    head = job.title or "Your request"
    out = [f"{head} - {_STATE_WORD.get(job.state, job.state)}"]
    shown = job.lines[-MAX_LINES:]
    if len(job.lines) > len(shown):
        out.append(f"(+{len(job.lines) - len(shown)} earlier)")
    out.extend(f"{_clock(line.ts)} {_line_text(line)}" for line in shown)
    return "\n".join(out)[:3500]


def remote_payload(job: Job) -> dict[str, Any]:
    latest = job.lines[-1] if job.lines else None
    return {
        "topic": "job_status",
        "job": job.key,
        "title": job.title or "Your request",
        "state": job.state,
        "latest": _line_text(latest) if latest else "",
        "lines": [{"at": _clock(line.ts), "text": _line_text(line)} for line in job.lines[-MAX_LINES:]],
        "updated": int(job.updated),
    }


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _canon(key: str, parent: str | None) -> str:
    key = str(key)
    if key in _alias:
        return _alias[key]
    if parent:
        root = _alias.get(str(parent), str(parent))
        if key != root:
            _alias[key] = root
        return root
    return key


def _gc(now: float) -> None:
    for key in [k for k, j in _jobs.items() if now - j.updated > JOB_TTL_S]:
        job = _jobs.pop(key)
        if job.timer:
            job.timer.cancel()
    for alias in [a for a, root in _alias.items() if root not in _jobs and a not in _jobs]:
        _alias.pop(alias, None)
    for tk in [k for k, v in _turns.items() if now - v.get("ts", now) > JOB_TTL_S]:
        _turns.pop(tk, None)


def report(job: str, kind: str, text: str = "", *, title: str | None = None, parent: str | None = None) -> bool:
    """Record one step of ``job`` and queue it for Paul. Returns True when a line was added.

    ``kind`` is one of :data:`KINDS`. ``parent`` joins this job to an existing one, so a
    proposal, the thread made from it and the run that follows share one status message.
    Never raises.
    """
    try:
        if not job or not enabled():
            return False
        kind = kind if kind in KINDS else "info"
        body = _clean(text, MAX_LINE_CHARS)
        now = _now()
        with _LOCK:
            _gc(now)
            key = _canon(str(job), parent)
            j = _jobs.get(key)
            if j is not None and j.state in _TERMINAL_STATES and kind in {"picked_up", "proposal", "thread"}:
                # The earlier run is finished; this is new work on the same key. Fresh message.
                if j.timer:
                    j.timer.cancel()
                j = _jobs.pop(key)
                j = None
            if j is None:
                j = _jobs[key] = Job(key=key, created=now, updated=now, token=f"{TOKEN_PREFIX}{_token_slug(key)}.{int(now)}")
            if title and not j.title:
                j.title = _clean(title, MAX_TITLE_CHARS)
            last = j.lines[-1] if j.lines else None
            if last and last.kind == kind and last.text == body:
                if kind in {"step", "info"}:
                    last.count += 1
                    last.ts = now
                else:
                    return False  # exact repeat of a milestone: dedupe
            else:
                j.lines.append(Line(ts=now, kind=kind, text=body))
                del j.lines[:-60]
            j.state = _STATE_FOR_KIND.get(kind, "running")
            j.updated = now
            j.dirty = True
            j.urgent = j.urgent or kind in _URGENT_KINDS
        _schedule(key)
        return True
    except Exception:
        logger.debug("job_progress.report failed", exc_info=True)
        return False


def _token_slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", key)[:40]


def _schedule(key: str) -> None:
    now = _now()
    with _LOCK:
        j = _jobs.get(key)
        if j is None or not j.dirty:
            return
        interval = URGENT_EDIT_INTERVAL_S if j.urgent else MIN_EDIT_INTERVAL_S
        wait = max(0.0, j.last_flush + interval - now)
        if wait <= 0 and _global_budget_wait(now) <= 0:
            run_now = True
        else:
            run_now = False
            wait = max(wait, _global_budget_wait(now), 0.2)
            if j.timer is None:
                timer = threading.Timer(wait, _timer_fire, args=(key,))
                timer.daemon = True
                j.timer = timer
                timer.start()
    if run_now:
        flush(key)


def _timer_fire(key: str) -> None:
    with _LOCK:
        j = _jobs.get(key)
        if j is not None:
            j.timer = None
    _schedule(key)


def _global_budget_wait(now: float) -> float:
    with _LOCK:
        _flush_times[:] = [t for t in _flush_times if now - t < GLOBAL_WINDOW_S]
        if len(_flush_times) < GLOBAL_MAX_UPDATES:
            return 0.0
        return max(0.0, _flush_times[0] + GLOBAL_WINDOW_S - now)


def flush(key: str) -> str | None:
    """Deliver the job's current status now. Returns the route used (remote / telegram) or None."""
    now = _now()
    with _LOCK:
        j = _jobs.get(key)
        if j is None or not j.dirty:
            return None
        text = render(j)
        payload = remote_payload(j)
        j.dirty = False
        j.urgent = False
        prev_flush, j.last_flush = j.last_flush, now
        _flush_times.append(now)
        token, message_id, chat_id = j.token, j.message_id, j.chat_id
        awaiting, sent_at, sent_text = j.awaiting_ack, j.sent_at, j.sent_text
    try:
        if _remote_active() and _remote_push(payload):
            return "remote"
        if text == sent_text:
            return None
        if message_id:
            ok = _telegram_edit(text, message_id, chat_id)
        elif awaiting and now - sent_at < ACK_WAIT_S:
            # Telegram hasn't told us the message id yet. Nothing went out, so hand the budget
            # back and keep the update pending; record_message() flushes it on the ack.
            with _LOCK:
                j5 = _jobs.get(key)
                if j5 is not None:
                    j5.dirty, j5.last_flush = True, prev_flush
                if now in _flush_times:
                    _flush_times.remove(now)
            _arm_ack_timer(key, sent_at + ACK_WAIT_S - now)
            return None
        else:
            ok = _telegram_send(text, token)
            if ok:
                with _LOCK:
                    j2 = _jobs.get(key)
                    if j2 is not None:
                        j2.awaiting_ack, j2.sent_at = True, now
        if ok:
            with _LOCK:
                j3 = _jobs.get(key)
                if j3 is not None:
                    j3.sent_text = text
                    j3.failures = 0
            return "telegram"
        with _LOCK:
            j4 = _jobs.get(key)
            retry = False
            if j4 is not None:
                j4.failures += 1
                retry = j4.failures < MAX_DELIVERY_FAILURES
        if retry:
            _mark_dirty(key)
    except Exception:
        logger.debug("job_progress.flush failed", exc_info=True)
    return None


def _arm_ack_timer(key: str, wait: float) -> None:
    with _LOCK:
        j = _jobs.get(key)
        if j is None or j.timer is not None:
            return
        timer = threading.Timer(max(wait, 0.2) + 0.1, _timer_fire, args=(key,))
        timer.daemon = True
        j.timer = timer
        timer.start()


def _mark_dirty(key: str) -> None:
    with _LOCK:
        j = _jobs.get(key)
        if j is not None:
            j.dirty = True
    _schedule(key)


def record_message(token: str, message_id: Any, chat_id: Any = None) -> bool:
    """Telegram confirmed the status message; later updates edit it. Called from the socket handler."""
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return False
    if mid <= 0 or not str(token or "").startswith(TOKEN_PREFIX):
        return False
    with _LOCK:
        for j in _jobs.values():
            if j.token == token:
                j.message_id, j.chat_id, j.awaiting_ack = mid, chat_id, False
                dirty = j.dirty
                key = j.key
                break
        else:
            return False
    if dirty:
        _schedule(key)
    return True


def get_job(job: str) -> Job | None:
    with _LOCK:
        return _jobs.get(_alias.get(str(job), str(job)))


def reset_for_tests() -> None:
    global _installed
    with _LOCK:
        for j in _jobs.values():
            if j.timer:
                j.timer.cancel()
        _jobs.clear()
        _alias.clear()
        _flush_times.clear()
        _turns.clear()
        _installed = False


# ---------------------------------------------------------------------------
# Bridge: orchestrator ledger -> steps
# ---------------------------------------------------------------------------

_SURFACES_TO_NARRATE = {"telegram", "remote"}


def _legacy(ev: dict[str, Any]) -> str:
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    orch = payload.get("orchestration") if isinstance(payload.get("orchestration"), dict) else {}
    return str(orch.get("legacy_event_type") or "")


def on_orchestration_event(_event_type: str, ev: Any) -> None:
    """EventBus handler for ``ORCHESTRATION_EVENT``. Never raises."""
    try:
        if not isinstance(ev, dict):
            return
        source = str(ev.get("source") or "")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if source == "chat":
            if payload.get("subtype") == "chat_user_message" and str(payload.get("surface") or "") in _SURFACES_TO_NARRATE:
                try:
                    key = (int(payload["chat_id"]), int(payload["chat_row_id"]))
                except (KeyError, TypeError, ValueError):
                    return
                with _LOCK:
                    _turns[key] = {"surface": payload.get("surface"), "tools": 0, "ts": _now(),
                                   "title": _clean(payload.get("content_preview"), MAX_TITLE_CHARS)}
            return
        if source == "initiative":
            steps = _map_outcome(str(ev.get("event_type") or ""), payload, ev)
            if steps:
                threading.Thread(target=_report_outcome, args=(steps,), daemon=True).start()
            return
        step = _map_orchestration(ev, source, str(ev.get("event_type") or ""), _legacy(ev),
                                  str(ev.get("status") or ""), str(ev.get("summary") or ""))
        if step is None:
            return
        # Title and step-name lookups touch the database, so never on the emitting thread.
        threading.Thread(target=_report_mapped, args=(ev, *step), daemon=True).start()
    except Exception:
        logger.debug("job_progress.on_orchestration_event failed", exc_info=True)


def _report_outcome(steps: list[dict[str, Any]]) -> None:
    for step in steps:
        report(step["job"], step["kind"], step.get("text", ""), title=step.get("title"), parent=step.get("parent"))


def _map_outcome(etype: str, payload: dict[str, Any], ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Initiative outcome-flow audit events (client mail watch, replies) -> steps."""
    if not etype.startswith("outcome_"):
        return []
    name = etype[len("outcome_"):]
    mid = str(payload.get("message_id") or "").strip()
    ticket = payload.get("ticket_id") or ev.get("ticket_id")
    mail_job = f"mail:{mid}" if mid else ""
    kind = str(payload.get("kind") or "").replace("_", " ").strip()
    mail_title = f"Client mail: {kind}" if kind else "Client mail"
    if name == "client_mail_proposal" and mail_job:
        return [{"job": mail_job, "kind": "picked_up", "text": "a client mail that needs action", "title": mail_title},
                {"job": mail_job, "kind": "proposal", "text": "problem, cause and fix plan, in Incoming and on Telegram"},
                {"job": mail_job, "kind": "waiting", "text": "your approval of the proposal"}]
    if name == "client_mail_card" and mail_job:
        if payload.get("delivered") is False:
            return [{"job": mail_job, "kind": "blocked", "text": "the Telegram card for this mail did not go out",
                     "title": mail_title}]
        return [{"job": mail_job, "kind": "picked_up", "text": "a client mail that needs action", "title": mail_title},
                {"job": mail_job, "kind": "waiting", "text": "your approval on the Telegram card"}]
    if name == "client_issue_started" and mail_job:
        steps = [{"job": mail_job, "kind": "step", "text": "approved, opening the ticket"}]
        if ticket:
            steps.append({"job": f"ticket:{int(ticket)}", "kind": "thread", "text": "ticket and thread opened", "parent": mail_job})
        else:
            steps.append({"job": mail_job, "kind": "waiting", "text": "which project this belongs to"})
        return steps
    if name == "client_issue_dismissed" and mail_job:
        return [{"job": mail_job, "kind": "done", "text": "set aside by you, nothing sent"}]
    if ticket and name == "own_client_reply_held_for_approval":
        return [{"job": f"ticket:{int(ticket)}", "kind": "waiting", "text": "your OK to send the client reply"}]
    if ticket and name in {"own_client_reply_sent", "operate_client_reply_confirmed"} and payload.get("delivered") is True:
        return [{"job": f"ticket:{int(ticket)}", "kind": "done", "text": "client reply sent"}]
    return []


def _report_mapped(ev: dict[str, Any], job: str, kind: str, text: str) -> None:
    try:
        title = ""
        if job.startswith("ticket:"):
            title = _ticket_title(int(job.split(":", 1)[1]))
        if text == "@step" and ev.get("step_id"):
            name = _step_name(int(ev["step_id"]))
            text = f"started step: {name}" if name else "started the next step"
        report(job, kind, text, title=title or None)
    except Exception:
        logger.debug("job_progress._report_mapped failed", exc_info=True)


def _map_orchestration(ev: dict[str, Any], source: str, etype: str, legacy: str, status: str,
                       summary: str) -> tuple[str, str, str] | None:
    ticket, run = ev.get("ticket_id"), ev.get("run_id")
    if ticket:
        job = f"ticket:{int(ticket)}"
    elif run:
        job = f"run:{int(run)}"
    else:
        return None
    if etype == "channel_intake_ticket_created":
        return job, "thread", f"from {source} intake"
    if source == "workflow":
        if legacy == "workflow_run_started":
            return job, "picked_up", "the work run started"
        if legacy == "workflow_run_resumed":
            return job, "step", "resumed"
        if legacy == "workflow_run_completed":
            return job, "done", summary or "the work run finished"
        if legacy == "workflow_run_cancelled":
            return job, "failed", "the run was cancelled"
        if etype == "workflow_step_completed":
            return job, "step", f"finished step: {summary}" if summary else "finished a step"
        if etype == "worker_failed":
            return job, "blocked", summary or "a step failed"
    if etype == "approval_requested":
        return job, "waiting", f"your approval: {summary}" if summary else "your approval"
    if etype == "needs_input":
        return job, "waiting", f"your input: {summary}" if summary else "your input"
    if etype == "worker_failed":
        return job, "blocked", summary or "a worker failed"
    if etype == "worker_dispatched" and ev.get("step_id") and source != "workflow":
        return job, "step", "@step"
    return None


# ---------------------------------------------------------------------------
# Bridge: chat turn (Telegram / remote origin) -> steps
# ---------------------------------------------------------------------------

def on_chat_turn_event(p: dict[str, Any]) -> None:
    """Called after ``chat_turns.create_event`` persists an event. Never raises."""
    try:
        chat_id, turn_id = int(p["chat_id"]), int(p["turn_id"])
        with _LOCK:
            origin = _turns.get((chat_id, turn_id))
            if origin is None:
                return
        etype = str(p.get("event_type") or "")
        title = str(p.get("title") or "").strip()
        summary = str(p.get("summary") or "")
        key = f"turn:{chat_id}:{turn_id}"
        head = origin.get("title") or None
        if etype in {"tool_started", "tool_completed", "tool_failed"}:
            with _LOCK:
                origin["tools"] = int(origin.get("tools", 0)) + 1
            if etype == "tool_started":
                report(key, "step", f"running {title}", title=head)
            elif etype == "tool_failed":
                report(key, "step", f"{title} failed: {summary}" if summary else f"{title} failed", title=head)
            else:
                report(key, "step", title or "ran a tool", title=head)
            return
        if not origin.get("tools"):
            return  # a plain answer with no work behind it needs no status message
        if etype == "synthesis_started":
            report(key, "step", "preparing the answer", title=head)
        elif etype == "turn_completed":
            if summary.startswith(ROUND_CAP_PREFIX):
                report(key, "blocked", "hit the tool-round limit before finishing; say continue to resume", title=head)
            else:
                report(key, "done", "answer sent", title=head)
        elif etype == "turn_failed":
            report(key, "failed", summary or "the request failed", title=head)
        elif etype == "turn_cancelled":
            report(key, "failed", "stopped", title=head)
    except Exception:
        logger.debug("job_progress.on_chat_turn_event failed", exc_info=True)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def install(bus: Any = None) -> bool:
    """Subscribe the orchestrator bridge once. Safe to call repeatedly."""
    global _installed
    with _LOCK:
        if _installed:
            return False
        try:
            if bus is None:
                from distr.core.events import get_event_bus

                bus = get_event_bus()
            from distr.core.events import ORCHESTRATION_EVENT

            bus.subscribe(ORCHESTRATION_EVENT, on_orchestration_event)
        except Exception:
            logger.warning("job_progress: could not subscribe to the event bus", exc_info=True)
            return False
        _installed = True
    return True
