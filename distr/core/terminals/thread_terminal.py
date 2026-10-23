"""Thread -> terminal bridge.

A Development thread's terminal is the startup-terminal set of the project the
thread belongs to (``chats.project_id``). This module only *reads* the existing
PTY session registry in ``distr.core.terminal`` and delegates start/stop to
``distr.core.project_startup_terminals``; it owns no processes of its own.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

PURPOSE = "startup"


def resolve_thread_project_id(thread_id: int) -> Optional[int]:
    """Return the project id for a thread. Raises LookupError if the thread is missing."""
    from distr.core.db import Chat, get_session

    with get_session() as session:
        chat = session.query(Chat).filter(Chat.id == int(thread_id)).first()
        if chat is None:
            raise LookupError(f"Thread {thread_id} not found")
        return int(chat.project_id) if chat.project_id is not None else None


def _iso(epoch: Any) -> Optional[str]:
    try:
        return _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc).isoformat()
    except Exception:
        return None


def project_terminal_sessions(project_id: int) -> list[dict[str, Any]]:
    """Alive startup sessions for a project, read from the shared registry."""
    from distr.core import terminal as term

    term.cleanup_dead_startup_sessions()
    rows: list[dict[str, Any]] = []
    for key, sess in list(term._startup_sessions.items()):
        if getattr(sess, "project_id", None) != int(project_id) or not getattr(sess, "is_alive", False):
            continue
        if getattr(sess, "purpose", None) != PURPOSE:
            continue
        rows.append({
            "session_id": key,
            "pid": getattr(sess, "pid", None),
            "command": getattr(sess, "shell_command", "") or "",
            "started_at": _iso(getattr(sess, "created_at", None)),
        })
    rows.sort(key=lambda r: r.get("started_at") or "")
    return rows


def status_for_project(project_id: Optional[int], thread_id: Optional[int] = None) -> dict[str, Any]:
    sessions = project_terminal_sessions(project_id) if project_id else []
    first = sessions[0] if sessions else {}
    return {
        "thread_id": thread_id,
        "project_id": project_id,
        "running": bool(sessions),
        "pid": first.get("pid"),
        "session_id": first.get("session_id"),
        "started_at": first.get("started_at"),
        "session_count": len(sessions),
        "sessions": sessions,
    }


def thread_terminal_status(thread_id: int) -> dict[str, Any]:
    return status_for_project(resolve_thread_project_id(thread_id), int(thread_id))


def list_project_thread_terminals(project_id: int) -> dict[str, Any]:
    from distr.core.db import Chat, get_session

    status = status_for_project(int(project_id))
    with get_session() as session:
        ids = [int(r[0]) for r in session.query(Chat.id).filter(Chat.project_id == int(project_id)).all()]
    status.pop("thread_id", None)
    status["thread_ids"] = ids
    status["threads"] = [
        {"thread_id": tid, "running": status["running"], "pid": status["pid"],
         "session_id": status["session_id"], "started_at": status["started_at"]}
        for tid in ids
    ]
    return status
