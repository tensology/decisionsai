"""Durable lifecycle ledger for multi-stage chat turns.

This module is deliberately UI- and provider-agnostic.  A turn is identified
by the ``Chat`` row containing the user's message; providers, workflows and
tools all project concise activity into the same ordered ledger.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from distr.core.db import Chat, ChatTurnEvent, get_session
from distr.core.db.time import utc_now_naive

logger = logging.getLogger(__name__)

EVENT_TYPES = {
    "turn_started",
    "acknowledgment",
    "tool_started",
    "tool_completed",
    "tool_failed",
    "synthesis_started",
    "turn_steered",
    "turn_completed",
    "turn_failed",
    "turn_cancelled",
}
TERMINAL_TURN_TYPES = {"turn_completed", "turn_failed", "turn_cancelled"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "skipped", "passed"}

_SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|token|secret|password|passwd|cookie|credential|private[_-]?key)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|"
    r"\b(sk-[a-z0-9_-]{12,}|xox[baprs]-[a-z0-9-]{12,}|gh[pousr]_[a-z0-9]{12,})\b"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*([^\s,;]+)"
)
_ABS_PATH_RE = re.compile(r"(?<![\w.])(?:/Users|/home|/var|/tmp|/[A-Za-z][\w.-]*)/(?:[^\s'\"<>]+)")
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\s'\"<>]+)")


def _root_and_turn(session, chat_id: int, turn_id: Optional[int] = None) -> tuple[int, Optional[int]]:
    row = session.get(Chat, int(chat_id))
    if not row:
        return int(chat_id), None
    root = row
    guard = 0
    while root.parent_id is not None and guard < 64:
        guard += 1
        parent = session.get(Chat, int(root.parent_id))
        if not parent:
            break
        root = parent
    if turn_id is not None:
        candidate = session.get(Chat, int(turn_id))
        if candidate:
            candidate_root, _ = _root_and_turn(session, int(candidate.id), None)
            if candidate_root == int(root.id):
                return int(root.id), int(candidate.id)
        return int(root.id), None
    try:
        params = json.loads(root.params or "{}")
    except (TypeError, json.JSONDecodeError):
        params = {}
    active = params.get("active_turn_chat_row_id") if isinstance(params, dict) else None
    try:
        return int(root.id), int(active) if active is not None else None
    except (TypeError, ValueError):
        return int(root.id), None


def redact_text(value: Any, *, limit: int = 8000, preserve_paths: bool = False) -> str:
    """Return a bounded human-readable value safe for persistence/broadcast."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "").replace("\r\n", "\n").strip()
    text = _SECRET_VALUE_RE.sub(lambda m: (m.group(1) if m.lastindex else "") + "[redacted]", text)
    text = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    if not preserve_paths:
        text = _ABS_PATH_RE.sub("[local path]", text)
        text = _WIN_PATH_RE.sub("[local path]", text)
    if len(text) > limit:
        text = text[: max(0, limit - 28)].rstrip() + "\n… [truncated for safety]"
    return text


def redact_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:80]:
            key = str(raw_key)[:120]
            if _SECRET_KEY_RE.search(key) or key.lower() in {"prompt", "raw_prompt", "system_prompt", "arguments", "args"}:
                result[key] = "[redacted]"
            else:
                result[key] = redact_metadata(raw_value, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [redact_metadata(item, depth=depth + 1) for item in list(value)[:80]]
    if isinstance(value, (str, Path)):
        return redact_text(value, limit=1000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, limit=1000)


def _event_payload(event: ChatTurnEvent) -> dict[str, Any]:
    try:
        metadata = json.loads(event.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    created = event.created_date.isoformat() if event.created_date else None
    modified = event.modified_date.isoformat() if event.modified_date else created
    completed = event.completed_date.isoformat() if event.completed_date else None
    return {
        "chat_id": int(event.chat_id),
        "turn_id": int(event.turn_id),
        "sequence": int(event.sequence),
        "event_id": event.event_id,
        "event_type": event.event_type,
        "status": event.status,
        "title": event.title or "",
        "summary": event.summary or "",
        "detail": event.detail or "",
        "metadata": metadata if isinstance(metadata, dict) else {},
        "timestamp": created,
        "updated_at": modified,
        "completed_at": completed,
    }


def _broadcast(payload: dict[str, Any]) -> None:
    envelope = {"type": "turn_event", "event": "turn_event", **payload}
    try:
        from distr.gui.web.routes.chat import publish_chat_event_threadsafe

        publish_chat_event_threadsafe(envelope)
    except Exception:
        # Persistence is authoritative. GET recovery handles a stopped web server,
        # startup races, dropped sockets and tests without an embedded server.
        logger.debug("Turn event persisted without live broadcast", exc_info=True)


def create_event(
    chat_id: int,
    event_type: str,
    *,
    turn_id: Optional[int] = None,
    status: str = "running",
    title: str = "",
    summary: str = "",
    detail: str = "",
    metadata: Optional[dict[str, Any]] = None,
    event_id: Optional[str] = None,
    broadcast: bool = True,
) -> Optional[dict[str, Any]]:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported chat turn event type: {event_type}")
    for attempt in range(5):
        try:
            with get_session() as session:
                root_id, resolved_turn_id = _root_and_turn(session, int(chat_id), turn_id)
                if resolved_turn_id is None:
                    return None
                if event_id:
                    existing = (
                        session.query(ChatTurnEvent)
                        .filter(ChatTurnEvent.event_id == str(event_id))
                        .first()
                    )
                    if existing:
                        payload = _event_payload(existing)
                        return payload
                sequence = int(
                    session.query(func.coalesce(func.max(ChatTurnEvent.sequence), 0))
                    .filter(
                        ChatTurnEvent.chat_id == root_id,
                        ChatTurnEvent.turn_id == resolved_turn_id,
                    )
                    .scalar()
                    or 0
                ) + 1
                now = utc_now_naive()
                row = ChatTurnEvent(
                    chat_id=root_id,
                    turn_id=resolved_turn_id,
                    sequence=sequence,
                    event_id=str(event_id or f"cte_{uuid.uuid4().hex}"),
                    event_type=event_type,
                    status=(status or "running")[:24],
                    title=redact_text(title, limit=240),
                    summary=redact_text(summary, limit=1200),
                    detail=redact_text(detail, limit=8000),
                    metadata_json=json.dumps(redact_metadata(metadata or {}), ensure_ascii=False),
                    created_date=now,
                    modified_date=now,
                    completed_date=now if status in TERMINAL_STATUSES else None,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                payload = _event_payload(row)
            if broadcast:
                _broadcast(payload)
            return payload
        except IntegrityError:
            if attempt == 4:
                raise
            continue
    return None


def update_event(
    event_id: str,
    *,
    event_type: Optional[str] = None,
    status: Optional[str] = None,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    detail: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    broadcast: bool = True,
) -> Optional[dict[str, Any]]:
    if event_type is not None and event_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported chat turn event type: {event_type}")
    with get_session() as session:
        row = (
            session.query(ChatTurnEvent)
            .filter(ChatTurnEvent.event_id == str(event_id))
            .first()
        )
        if not row:
            return None
        if event_type is not None:
            row.event_type = event_type
        if status is not None:
            row.status = status[:24]
        if title is not None:
            row.title = redact_text(title, limit=240)
        if summary is not None:
            row.summary = redact_text(summary, limit=1200)
        if detail is not None:
            row.detail = redact_text(detail, limit=8000)
        if metadata is not None:
            try:
                prior = json.loads(row.metadata_json or "{}")
            except (TypeError, json.JSONDecodeError):
                prior = {}
            if not isinstance(prior, dict):
                prior = {}
            prior.update(redact_metadata(metadata))
            row.metadata_json = json.dumps(prior, ensure_ascii=False)
        row.modified_date = utc_now_naive()
        if row.status in TERMINAL_STATUSES:
            row.completed_date = row.completed_date or row.modified_date
        session.commit()
        session.refresh(row)
        payload = _event_payload(row)
    if broadcast:
        _broadcast(payload)
    return payload


def ensure_turn_started(chat_id: int, turn_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return None
        existing = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type == "turn_started",
            )
            .first()
        )
        if existing:
            return _event_payload(existing)
    return create_event(
        chat_id,
        "turn_started",
        turn_id=resolved,
        title="Request received",
        summary="Preparing the response.",
    )


def _ack_text(tool_name: str) -> str:
    name = (tool_name or "").lower()
    if any(word in name for word in ("search", "research", "browser", "web")):
        return "I’m checking that now."
    if any(word in name for word in ("workflow", "ticket", "project", "agent")):
        return "I’m getting that work underway."
    if any(word in name for word in ("file", "code", "shell", "execute")):
        return "I’m working through that now."
    return "Working on it."


def ensure_acknowledgment(chat_id: int, turn_id: int, tool_name: str = "") -> tuple[Optional[dict[str, Any]], bool]:
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return None, False
        existing = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type == "acknowledgment",
            )
            .first()
        )
        if existing:
            return _event_payload(existing), False
    text = _ack_text(tool_name)
    payload = create_event(
        chat_id,
        "acknowledgment",
        turn_id=turn_id,
        status="completed",
        title=text,
        summary=text,
        metadata={"speak": True},
    )
    return payload, payload is not None


def start_tool(
    chat_id: int,
    tool_name: str,
    *,
    turn_id: Optional[int] = None,
    title: str = "",
    summary: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> tuple[Optional[str], bool, Optional[str]]:
    ensure_turn_started(chat_id, turn_id)
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
    if resolved is None:
        return None, False, None
    ack, created_ack = ensure_acknowledgment(root_id, resolved, tool_name)
    label = title or tool_name.replace("_", " ").strip().title() or "Tool"
    payload = create_event(
        root_id,
        "tool_started",
        turn_id=resolved,
        title=label,
        summary=summary or "Running…",
        metadata={"tool_name": tool_name, **(metadata or {})},
    )
    return (
        payload.get("event_id") if payload else None,
        created_ack,
        ack.get("summary") if ack else None,
    )


def finish_tool(
    event_id: str,
    *,
    success: bool,
    summary: str,
    detail: str = "",
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    return update_event(
        event_id,
        event_type="tool_completed" if success else "tool_failed",
        status="completed" if success else "failed",
        summary=summary,
        detail=detail,
        metadata=metadata,
    )


def find_running_tool_event(chat_id: int, tool_name: str, turn_id: Optional[int] = None) -> Optional[str]:
    """Return the newest unfinished event for this tool in the active turn."""
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return None
        rows = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type == "tool_started",
                ChatTurnEvent.status == "running",
            )
            .order_by(ChatTurnEvent.sequence.desc())
            .all()
        )
        for row in rows:
            try:
                metadata = json.loads(row.metadata_json or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if str((metadata or {}).get("tool_name") or "") == str(tool_name or ""):
                return str(row.event_id)
    return None


def recently_finished_tool_event(
    chat_id: int,
    tool_name: str,
    turn_id: Optional[int] = None,
    *,
    within_seconds: float = 3.0,
) -> Optional[str]:
    """Detect compatibility callers that report the same completed call twice."""
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return None
        rows = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type.in_(("tool_completed", "tool_failed")),
            )
            .order_by(ChatTurnEvent.sequence.desc())
            .limit(8)
            .all()
        )
        now = utc_now_naive()
        for row in rows:
            try:
                metadata = json.loads(row.metadata_json or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if str((metadata or {}).get("tool_name") or "") != str(tool_name or ""):
                continue
            changed = row.modified_date or row.created_date
            if changed and abs((now - changed).total_seconds()) <= within_seconds:
                return str(row.event_id)
    return None


def begin_synthesis(chat_id: int, turn_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return None
        existing = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type == "synthesis_started",
                ChatTurnEvent.status == "running",
            )
            .order_by(ChatTurnEvent.sequence.desc())
            .first()
        )
        if existing:
            return _event_payload(existing)
    return create_event(
        root_id,
        "synthesis_started",
        turn_id=resolved,
        title="Preparing the answer",
        summary="Synthesizing the verified results.",
    )


def complete_turn(
    chat_id: int,
    *,
    turn_id: Optional[int] = None,
    display_text: str = "",
    speech_text: str = "",
) -> Optional[dict[str, Any]]:
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return None
        existing = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type.in_(tuple(TERMINAL_TURN_TYPES)),
            )
            .first()
        )
        if existing:
            return _event_payload(existing)
    payload = create_event(
        root_id,
        "turn_completed",
        turn_id=resolved,
        status="completed",
        title="Answer ready",
        summary=display_text,
        metadata={"display_text": display_text, "speech_text": speech_text or display_text, "speak": True},
    )
    if payload:
        _clear_active_pointer(root_id, resolved)
    return payload


def _clear_active_pointer(root_id: int, turn_id: int) -> None:
    try:
        with get_session() as session:
            root = session.get(Chat, int(root_id))
            if not root:
                return
            try:
                params = json.loads(root.params or "{}")
            except (TypeError, json.JSONDecodeError):
                params = {}
            if isinstance(params, dict) and int(params.get("active_turn_chat_row_id") or 0) == int(turn_id):
                params.pop("active_turn_chat_row_id", None)
                root.params = json.dumps(params)
                session.commit()
    except Exception:
        logger.debug("Could not clear terminal chat turn pointer", exc_info=True)


def terminal_turn(chat_id: int, event_type: str, *, turn_id: Optional[int] = None, summary: str = "") -> Optional[dict[str, Any]]:
    if event_type not in {"turn_failed", "turn_cancelled"}:
        raise ValueError("terminal_turn requires turn_failed or turn_cancelled")
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return None
        existing = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type.in_(tuple(TERMINAL_TURN_TYPES)),
            )
            .first()
        )
        if existing:
            return _event_payload(existing)
    payload = create_event(
        root_id,
        event_type,
        turn_id=resolved,
        status="failed" if event_type == "turn_failed" else "cancelled",
        title="Request failed" if event_type == "turn_failed" else "Request stopped",
        summary=summary,
    )
    if payload:
        _clear_active_pointer(root_id, resolved)
    return payload


def steer_turn(chat_id: int, turn_id: int, guidance: str) -> Optional[dict[str, Any]]:
    clean = redact_text(guidance, limit=4000, preserve_paths=True)
    if not clean:
        raise ValueError("Steering guidance is required")
    return create_event(
        chat_id,
        "turn_steered",
        turn_id=turn_id,
        status="completed",
        title="Guidance added",
        summary=clean,
        detail=clean,
        metadata={"guidance_hash": hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16], "applied": False},
    )


def pending_steering(chat_id: int, turn_id: int) -> list[dict[str, Any]]:
    with get_session() as session:
        root_id, resolved = _root_and_turn(session, chat_id, turn_id)
        if resolved is None:
            return []
        rows = (
            session.query(ChatTurnEvent)
            .filter(
                ChatTurnEvent.chat_id == root_id,
                ChatTurnEvent.turn_id == resolved,
                ChatTurnEvent.event_type == "turn_steered",
            )
            .order_by(ChatTurnEvent.sequence.asc())
            .all()
        )
        return [p for row in rows if not (_event_payload(row).get("metadata") or {}).get("applied") for p in [_event_payload(row)]]


def mark_steering_applied(event_ids: Iterable[str]) -> None:
    for event_id in event_ids:
        update_event(str(event_id), metadata={"applied": True}, broadcast=False)


def apply_pending_steering_to_messages(service: Any, chat_id: Optional[int]) -> str:
    """Inject new human guidance once at a provider-safe boundary.

    The guidance is a user-authored instruction, not hidden reasoning.  It is
    appended as a system boundary note so providers do not mistake it for a
    second independent request while the current turn is still running.
    """
    if not chat_id:
        return ""
    turn_id = latest_active_turn_id(int(chat_id))
    if turn_id is None:
        return ""
    steers = pending_steering(int(chat_id), turn_id)
    if not steers:
        return ""
    guidance = "\n".join(
        f"- {item.get('detail') or item.get('summary')}" for item in steers
    ).strip()
    if not guidance:
        return ""
    messages = getattr(service, "_messages", None)
    if isinstance(messages, list):
        messages.append(
            {
                "role": "system",
                "content": (
                    "The user steered the active turn. Apply this guidance from the next safe "
                    f"boundary without repeating completed work:\n{guidance}"
                ),
            }
        )
    mark_steering_applied(item["event_id"] for item in steers)
    return guidance


def get_turns(chat_id: int) -> dict[str, Any]:
    with get_session() as session:
        root_id, active_turn_id = _root_and_turn(session, chat_id)
        rows = (
            session.query(ChatTurnEvent)
            .filter(ChatTurnEvent.chat_id == root_id)
            .order_by(ChatTurnEvent.turn_id.asc(), ChatTurnEvent.sequence.asc())
            .all()
        )
        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(int(row.turn_id), []).append(_event_payload(row))
    turns: list[dict[str, Any]] = []
    active: Optional[dict[str, Any]] = None
    for turn, events in grouped.items():
        terminal = next((e for e in reversed(events) if e["event_type"] in TERMINAL_TURN_TYPES), None)
        item = {
            "turn_id": turn,
            "status": terminal["status"] if terminal else "running",
            "active": terminal is None and turn == active_turn_id,
            "last_sequence": events[-1]["sequence"] if events else 0,
            "started_at": events[0]["timestamp"] if events else None,
            "completed_at": terminal.get("completed_at") if terminal else None,
            "events": events,
        }
        turns.append(item)
        if item["active"]:
            active = item
    turns.sort(key=lambda item: (item.get("started_at") or "", item["turn_id"]))
    return {"active_turn": active, "turns": turns}


def latest_active_turn_id(chat_id: int) -> Optional[int]:
    with get_session() as session:
        _, turn_id = _root_and_turn(session, chat_id)
        return turn_id
