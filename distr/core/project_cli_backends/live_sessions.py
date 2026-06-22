"""Shared live session registry for project CLI surfaces."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
import time
from typing import Any


@dataclass
class LiveProjectCliSession:
    project_id: int
    backend_id: str
    workflow_id: int | None = None
    board_id: int | None = None
    replay_buffer: deque = field(default_factory=lambda: deque(maxlen=80))
    assistant_buffer: str = ""
    running_count: int = 0
    connected: bool = False
    external_session_id: str = ""
    listeners: set[Any] = field(default_factory=set)
    last_activity: float = field(default_factory=time.time)
    last_presence_ping_at: float = 0.0
    workflow_area_present: bool = False
    expires_after_seconds: float = 180.0
    pending_disconnect: bool = False


_LOCK = threading.RLock()
_SESSIONS: dict[tuple[int, str, int | None], LiveProjectCliSession] = {}


def _normalize_board_id(board_id: int | None) -> int | None:
    try:
        if board_id in (None, "", False):
            return None
        return int(board_id)
    except Exception:
        return None


def _key(project_id: int, backend_id: str, board_id: int | None = None) -> tuple[int, str, int | None]:
    return int(project_id), str(backend_id or "").strip(), _normalize_board_id(board_id)


def _project_backend_matches(project_id: int, backend_id: str) -> tuple[int, str]:
    return int(project_id), str(backend_id or "").strip()


def _find_session_unlocked(
    project_id: int,
    backend_id: str,
    *,
    board_id: int | None = None,
    create: bool = True,
) -> LiveProjectCliSession | None:
    exact_key = _key(project_id, backend_id, board_id)
    session = _SESSIONS.get(exact_key)
    if session:
        return session

    normalized_board_id = _normalize_board_id(board_id)
    legacy_key = _key(project_id, backend_id, None)
    legacy = _SESSIONS.get(legacy_key)
    if normalized_board_id is not None and legacy and legacy is not session:
        legacy_board_id = _normalize_board_id(getattr(legacy, "board_id", None))
        if legacy_board_id in (None, normalized_board_id):
            if create:
                _SESSIONS.pop(legacy_key, None)
                legacy.board_id = normalized_board_id
                _SESSIONS[exact_key] = legacy
            return legacy

    if normalized_board_id is None and not create:
        matches = [
            existing
            for (pid, bid, _sid), existing in _SESSIONS.items()
            if (pid, bid) == _project_backend_matches(project_id, backend_id)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    if not create:
        return None

    session = LiveProjectCliSession(
        project_id=int(project_id),
        backend_id=str(backend_id or "").strip(),
        board_id=normalized_board_id,
    )
    _SESSIONS[exact_key] = session
    return session


def get_live_session(
    project_id: int,
    backend_id: str,
    *,
    board_id: int | None = None,
    create: bool = True,
) -> LiveProjectCliSession | None:
    with _LOCK:
        return _find_session_unlocked(project_id, backend_id, board_id=board_id, create=create)


def register_live_session_listener(project_id: int, backend_id: str, listener: Any, *, board_id: int | None = None) -> None:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=True)
    if not session:
        return
    with _LOCK:
        session.listeners.add(listener)


def unregister_live_session_listener(project_id: int, backend_id: str, listener: Any, *, board_id: int | None = None) -> None:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=False)
    if not session:
        return
    with _LOCK:
        session.listeners.discard(listener)


def set_live_session_running(project_id: int, backend_id: str, running: bool, *, board_id: int | None = None) -> None:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=True)
    if not session:
        return
    with _LOCK:
        session.last_activity = time.time()
        session.pending_disconnect = False
        if running:
            session.running_count += 1
            return
        session.running_count = max(0, int(session.running_count or 0) - 1)


def set_live_session_connected(
    project_id: int,
    backend_id: str,
    connected: bool,
    *,
    board_id: int | None = None,
    external_session_id: str = "",
) -> None:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=True)
    if not session:
        return
    with _LOCK:
        session.last_activity = time.time()
        session.connected = bool(connected)
        session.pending_disconnect = False
        if external_session_id or not connected:
            session.external_session_id = str(external_session_id or "").strip()
        if not connected:
            session.workflow_area_present = False


def live_session_connected(project_id: int, backend_id: str, *, board_id: int | None = None) -> bool:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=False)
    return bool(session and session.connected)


def live_session_external_id(project_id: int, backend_id: str, *, board_id: int | None = None) -> str:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=False)
    return str(session.external_session_id or "").strip() if session else ""


def any_live_session_running(project_id: int, *, exclude_backend_id: str = "", board_id: int | None = None) -> bool:
    project_id = int(project_id)
    exclude_backend_id = str(exclude_backend_id or "").strip()
    normalized_board_id = _normalize_board_id(board_id)
    with _LOCK:
        for (pid, backend_id, session_board_id), session in _SESSIONS.items():
            if pid != project_id:
                continue
            if exclude_backend_id and backend_id == exclude_backend_id:
                continue
            if normalized_board_id is not None and _normalize_board_id(session_board_id) != normalized_board_id:
                continue
            if int(session.running_count or 0) > 0:
                return True
    return False


def live_session_is_alive(project_id: int, backend_id: str, *, board_id: int | None = None) -> bool:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=False)
    return bool(session and int(session.running_count or 0) > 0)


def clear_live_session_buffer(project_id: int, backend_id: str, *, board_id: int | None = None) -> None:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=True)
    if not session:
        return
    with _LOCK:
        session.replay_buffer.clear()
        session.assistant_buffer = ""
        session.last_activity = time.time()
        session.pending_disconnect = False


def mark_live_session_presence(
    project_id: int,
    backend_id: str,
    *,
    workflow_id: int | None,
    board_id: int | None,
    present: bool,
    now: float | None = None,
) -> None:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=True)
    if not session:
        return
    stamp = float(now if now is not None else time.time())
    with _LOCK:
        session.workflow_id = int(workflow_id) if workflow_id is not None else session.workflow_id
        session.board_id = int(board_id) if board_id is not None else session.board_id
        session.workflow_area_present = bool(present)
        session.last_presence_ping_at = stamp
        session.last_activity = max(float(session.last_activity or 0.0), stamp)
        session.pending_disconnect = not present


def live_session_should_expire(
    project_id: int,
    backend_id: str,
    *,
    board_id: int | None = None,
    now: float | None = None,
) -> bool:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=False)
    if not session:
        return False
    stamp = float(now if now is not None else time.time())
    with _LOCK:
        if not session.connected:
            return False
        if int(session.running_count or 0) > 0:
            return False
        if session.workflow_area_present:
            return False
        if float(session.last_presence_ping_at or 0.0) <= 0:
            return False
        return (stamp - float(session.last_presence_ping_at)) >= float(session.expires_after_seconds or 180.0)


def snapshot_live_session_meta(
    project_id: int,
    backend_id: str,
    *,
    board_id: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=False)
    if not session:
        return {
            "workflow_id": None,
            "board_id": None,
            "last_presence_ping_at": 0.0,
            "workflow_area_present": False,
            "expires_after_seconds": 180.0,
            "expires_in_seconds": 0.0,
            "pending_disconnect": False,
        }
    stamp = float(now if now is not None else time.time())
    with _LOCK:
        expires_after = float(session.expires_after_seconds or 180.0)
        last_ping = float(session.last_presence_ping_at or 0.0)
        expires_in = 0.0
        if last_ping > 0:
            expires_in = max(0.0, expires_after - max(0.0, stamp - last_ping))
        return {
            "workflow_id": session.workflow_id,
            "board_id": session.board_id,
            "last_presence_ping_at": last_ping,
            "workflow_area_present": bool(session.workflow_area_present),
            "expires_after_seconds": expires_after,
            "expires_in_seconds": expires_in,
            "pending_disconnect": bool(session.pending_disconnect),
        }


def _update_replay_buffer(session: LiveProjectCliSession, event_dict: dict[str, Any]) -> None:
    evt_type = str((event_dict or {}).get("type") or "").strip()
    if evt_type == "message_end":
        message = event_dict.get("message") or {}
        role = str(message.get("role") or "").strip()
        content = message.get("content") or ""
        if role == "user" and content:
            session.replay_buffer.append({"role": "user", "content": content})
    elif evt_type == "message_update":
        assistant_event = event_dict.get("assistantMessageEvent") or {}
        assistant_type = str(assistant_event.get("type") or "").strip()
        if assistant_type == "text_delta":
            session.assistant_buffer += str(assistant_event.get("delta") or "")
        elif assistant_type == "done":
            if session.assistant_buffer.strip():
                session.replay_buffer.append({"role": "assistant", "content": session.assistant_buffer})
            session.assistant_buffer = ""
    elif evt_type == "error":
        message = str(event_dict.get("message") or "").strip()
        if message:
            session.replay_buffer.append({"role": "assistant", "content": message})
            session.assistant_buffer = ""


def publish_live_session_event(
    project_id: int,
    backend_id: str,
    event_dict: dict[str, Any],
    *,
    board_id: int | None = None,
) -> None:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=True)
    if not session:
        return
    stale: list[Any] = []
    with _LOCK:
        session.last_activity = time.time()
        _update_replay_buffer(session, event_dict)
        listeners = list(session.listeners)
    for listener in listeners:
        try:
            listener.put_nowait(event_dict)
        except Exception:
            stale.append(listener)
    if stale:
        with _LOCK:
            for listener in stale:
                session.listeners.discard(listener)


def snapshot_live_session(project_id: int, backend_id: str, *, board_id: int | None = None) -> list[dict[str, Any]]:
    session = get_live_session(project_id, backend_id, board_id=board_id, create=False)
    if not session:
        return []
    with _LOCK:
        items = list(session.replay_buffer)
        if session.assistant_buffer.strip():
            items.append({"role": "assistant", "content": session.assistant_buffer})
        return items


def replay_buffer_text(entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in entries or []:
        role = str((entry or {}).get("role") or "").strip()
        content = str((entry or {}).get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"> {content}")
        else:
            lines.append(content)
    return "\n\n".join(lines).strip()
