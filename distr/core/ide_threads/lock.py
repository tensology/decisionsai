"""Sticky Cursor/Codex thread identity for the current Decisions conversation.

This is not the Development section harness thread. A lock says which external
IDE session follow-up messages should continue, per surface (cursor or codex).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

LOCK_KEY = "ide_thread_locks"
_ID_KEYS = (
    "thread_id",
    "threadId",
    "session_id",
    "sessionId",
    "chatId",
    "conversation_id",
    "conversationId",
)
_DEVELOPMENT_REQUEST = re.compile(
    r"\b(development\s+section|development\s+harness|in\s+development)\b",
    re.IGNORECASE,
)


def summarize_cli_output(output: str) -> tuple[str, str]:
    """Pull an external thread id and the last assistant text out of CLI JSON."""
    thread_id = ""
    texts: list[str] = []
    raw = str(output or "")
    for line in raw.splitlines():
        event = _json_object(line.strip())
        if event is None:
            continue
        thread_id = thread_id or _id_from_event(event)
        text = _text_from_event(event)
        if text:
            texts.append(text)
    if not thread_id:
        whole = _json_object(raw.strip())
        if whole is not None:
            thread_id = _id_from_event(whole)
    preview = texts[-1] if texts else raw.strip()
    return thread_id, preview[:4000]


def load_state(*, chat_id: int | None = None) -> dict[str, Any]:
    """Return `{active_surface, by_surface}` for the conversational chat."""
    state = _read_chat_state(_conversational_chat_id(chat_id))
    if state.get("by_surface"):
        return state
    bridged = _state_from_bridge()
    return bridged or {"active_surface": "", "by_surface": {}}


def resolve_lock(
    *,
    surface: str,
    project: str = "",
    project_id: int | None = None,
    chat_id: int | None = None,
) -> dict[str, Any] | None:
    """Return the locked thread for this IDE when it still matches the project."""
    row = (load_state(chat_id=chat_id).get("by_surface") or {}).get(surface)
    if not isinstance(row, dict) or not str(row.get("thread_id") or "").strip():
        return None
    if not _project_matches(row, project=project, project_id=project_id):
        return None
    return row


def remember_lock(
    *,
    surface: str,
    thread_id: str,
    project: str = "",
    project_id: int | None = None,
    folder: str = "",
    session_id: int | None = None,
    chat_id: int | None = None,
) -> dict[str, Any] | None:
    """Store the external thread id on the conversational chat, not Development."""
    tid = str(thread_id or "").strip()
    surface = (surface or "").strip().lower()
    if surface not in {"cursor", "codex"} or not tid or tid.isdigit():
        return None
    row = {
        "surface": surface,
        "thread_id": tid,
        "project": (project or "").strip(),
        "project_id": int(project_id) if project_id else None,
        "folder": (folder or "").strip(),
        "session_id": int(session_id) if session_id else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    target = _conversational_chat_id(chat_id)
    if target is None:
        return row
    try:
        _write_chat_state(target, surface, row)
    except Exception:
        logger.debug("could not persist ide thread lock", exc_info=True)
    return row


def hold_ide_thread_for_message(text: str, *, chat_id: int | None = None) -> bool:
    """Keep the ide_thread tool available while a Cursor or Codex lock exists.

    Development-section requests stay on that harness and do not grab the lock.
    """
    raw = (text or "").strip()
    if not raw or _DEVELOPMENT_REQUEST.search(raw):
        return False
    try:
        by_surface = load_state(chat_id=chat_id).get("by_surface") or {}
    except Exception:
        return False
    return any(isinstance(row, dict) and row.get("thread_id") for row in by_surface.values())


def format_locks_for_prompt(state: dict[str, Any] | None) -> list[str]:
    by_surface = (state or {}).get("by_surface") if isinstance(state, dict) else None
    if not isinstance(by_surface, dict) or not by_surface:
        return []
    lines = [
        "- locked_ide_threads (Cursor or Codex sessions; not the Development harness):",
        f"  active_surface={state.get('active_surface') or ''}",
    ]
    for surface, row in by_surface.items():
        if not isinstance(row, dict) or not row.get("thread_id"):
            continue
        lines.append(
            "  - {surface}: project={project} thread_id={thread_id} folder={folder}".format(
                surface=surface,
                project=row.get("project") or "unspecified",
                thread_id=row.get("thread_id"),
                folder=row.get("folder") or "",
            )
        )
    lines.append(
        "  Follow-up instructions for that project stay on this thread via ide_thread "
        "action=amend on active_surface. Do not send them through the Development harness "
        "unless the user asks for the Development section."
    )
    return lines


def _conversational_chat_id(chat_id: int | None = None) -> int | None:
    if chat_id:
        if _is_development_chat(int(chat_id)):
            chat_id = None
        else:
            return int(chat_id)
    try:
        from distr.core.chat import ChatService

        current = ChatService.get_current_chat_id()
    except Exception:
        return None
    if current and not _is_development_chat(int(current)):
        return int(current)
    return None


def _is_development_chat(chat_id: int) -> bool:
    try:
        from distr.core.db import Chat, get_session
        from distr.core.workflow.development_threads import is_development_thread

        with get_session() as session:
            return bool(is_development_thread(session, session.get(Chat, int(chat_id))))
    except Exception:
        return False


def _read_chat_state(chat_id: int | None) -> dict[str, Any]:
    if not chat_id:
        return {}
    try:
        from distr.core.chat import _chat_params
        from distr.core.db import Chat, get_session

        with get_session() as session:
            chat = session.get(Chat, int(chat_id))
            if chat is None:
                return {}
            params = _chat_params(getattr(chat, "params", None))
    except Exception:
        logger.debug("ide thread lock read failed", exc_info=True)
        return {}
    bucket = params.get(LOCK_KEY)
    if not isinstance(bucket, dict):
        return {}
    by_surface = bucket.get("by_surface")
    if not isinstance(by_surface, dict):
        return {}
    return {
        "active_surface": str(bucket.get("active_surface") or ""),
        "by_surface": {key: value for key, value in by_surface.items() if isinstance(value, dict)},
    }


def _write_chat_state(chat_id: int, surface: str, row: dict[str, Any]) -> None:
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, get_session

    with get_session() as session:
        chat = session.get(Chat, int(chat_id))
        if chat is None:
            return
        params = _chat_params(getattr(chat, "params", None))
        bucket = params.get(LOCK_KEY)
        if not isinstance(bucket, dict):
            bucket = {}
        by_surface = bucket.get("by_surface")
        if not isinstance(by_surface, dict):
            by_surface = {}
        by_surface[surface] = row
        bucket["by_surface"] = by_surface
        bucket["active_surface"] = surface
        params[LOCK_KEY] = bucket
        chat.params = json.dumps(params)
        session.commit()


def _state_from_bridge() -> dict[str, Any]:
    """Recover the newest external thread id per IDE when the chat has no lock yet."""
    try:
        from distr.core.ide_bridge import list_ide_sessions
    except Exception:
        return {}
    by_surface: dict[str, dict[str, Any]] = {}
    active = ""
    for surface in ("cursor", "codex"):
        try:
            sessions = list_ide_sessions(source=surface, limit=8)
        except Exception:
            continue
        for session in sessions:
            tid = str(session.get("external_thread_id") or "").strip()
            if not tid or tid.isdigit():
                continue
            by_surface[surface] = {
                "surface": surface,
                "thread_id": tid,
                "project": session.get("project_name") or "",
                "project_id": session.get("project_id"),
                "folder": session.get("folder") or "",
                "session_id": session.get("id"),
                "updated_at": session.get("updated_at") or "",
            }
            active = active or surface
            break
    if not by_surface:
        return {}
    return {"active_surface": active, "by_surface": by_surface}


def _project_matches(row: dict[str, Any], *, project: str, project_id: int | None) -> bool:
    if project_id and row.get("project_id") and int(project_id) != int(row["project_id"]):
        return False
    hint = (project or "").strip().lower()
    if not hint:
        return True
    haystack = f"{row.get('project') or ''} {row.get('folder') or ''}".lower()
    return hint in haystack or (str(row.get("project") or "").lower() in hint if row.get("project") else False)


def _json_object(text: str) -> dict[str, Any] | None:
    if not text or text[0] not in "{[":
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _usable_id(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) < 8 or text.isdigit():
        return ""
    return text


def _id_from_event(event: dict[str, Any]) -> str:
    for key in _ID_KEYS:
        found = _usable_id(event.get(key))
        if found:
            return found
    for nested_key in ("thread", "item", "message", "payload"):
        nested = event.get(nested_key)
        if isinstance(nested, dict):
            for key in _ID_KEYS:
                found = _usable_id(nested.get(key))
                if found:
                    return found
    return ""


def _text_from_event(event: dict[str, Any]) -> str:
    kind = str(event.get("type") or "").lower()
    if kind == "item.completed":
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if item.get("type") == "agent_message":
            return str(item.get("text") or "").strip()
        return ""
    if kind == "result" and not event.get("is_error"):
        return str(event.get("result") or "").strip()
    if kind == "assistant":
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content") if isinstance(message, dict) else event.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and str(part.get("type") or "") == "text"
            ]
            return "".join(chunks).strip()
    return ""
