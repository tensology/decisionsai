"""
Tool execution audit logging.

Records every tool execution in the current chat thread and Hermes ledger.
It deliberately does not create workflow rows for ordinary chat/tool activity.

Human-visible trace: ``distr.agent.activity`` logs one ``[agent_tool]`` line per completion
(to ``decisions.log`` and stderr by default). Disable stderr-only with
``DECISIONSAI_AGENT_ACTIVITY_CONSOLE=0``.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)
_activity_logger = logging.getLogger("distr.agent.activity")

_CHAT_COMPACT_TOOLS = {
    "execute_code",
    "file_operations",
    "mode_control",
}


def _preview_result(text: Optional[str], limit: int = 220) -> str:
    if not text:
        return ""
    one_line = " ".join(str(text).split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."


def _full_result_for_chat(text: Optional[str], limit: int = 24000) -> str:
    """Preserve multiline tool output for the chat activity expand view."""
    if not text:
        return ""
    cleaned = str(text).replace("\r\n", "\n").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "\n… [truncated for storage]"


def _load_params(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_turn_chat_row_id(session, chat_id: int) -> int:
    """Leaf chat row for the active turn (matches ChatManagerCore.add_assistant_message target).

    Tool audit uses the thread root id from the agent; assistant text is written on the latest
    child row under that root. Linking tool cards to that row keeps UI ordering correct.
    """
    from distr.core.db import Chat

    chat = session.get(Chat, int(chat_id))
    if not chat:
        return int(chat_id)
    root = chat
    guard = 0
    while root.parent_id is not None and guard < 64:
        guard += 1
        parent = session.get(Chat, int(root.parent_id))
        if not parent:
            break
        root = parent
    root_id = root.id
    children = (
        session.query(Chat)
        .filter(Chat.parent_id == root_id)
        .order_by(Chat.created_date.asc())
        .all()
    )
    if not children:
        return int(root_id)
    return int(children[-1].id)


def _parse_tool_timestamp_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _is_near_duplicate_tool_event(prev: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    if (prev.get("tool_name") or "") != (cur.get("tool_name") or ""):
        return False
    if (prev.get("result_summary") or "") != (cur.get("result_summary") or ""):
        return False
    if int(prev.get("turn_chat_id") or 0) != int(cur.get("turn_chat_id") or 0):
        return False
    t1 = _parse_tool_timestamp_iso(prev.get("timestamp"))
    t2 = _parse_tool_timestamp_iso(cur.get("timestamp"))
    if t1 is None or t2 is None:
        return False
    return abs((t2 - t1).total_seconds()) < 2.0


def _build_chat_tool_event(
    chat_id: int,
    tool_name: str,
    result: str,
    status: str,
    instruction_hint: Optional[str],
    user_text: Optional[str],
    routing_path: Optional[str],
    turn_chat_id: int,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    normalized_tool = (tool_name or "tool").strip()
    normalized_status = (status or "completed").lower()
    if _looks_like_error(result):
        normalized_status = "failed"
    title = _chat_title(normalized_tool, result, instruction_hint)
    event = {
        "id": f"tool-{chat_id}-{now}",
        "event": "tool_executed",
        "chat_id": int(chat_id),
        "turn_chat_id": int(turn_chat_id),
        "tool_name": normalized_tool,
        "title": _preview_result(title, 140) or "Tool executed",
        "result_summary": _preview_result(result, 420),
        "result_detail": _full_result_for_chat(result),
        "status": normalized_status,
        "timestamp": now,
        "chat_visible": True,
        "chat_compact": _is_compact_tool(normalized_tool),
    }
    if user_text:
        event["user_text"] = _preview_result(user_text, 180)
    if routing_path:
        event["routing_path"] = routing_path
    return event


def _looks_like_error(result: Optional[str]) -> bool:
    lowered = (result or "").strip().lower()
    return lowered.startswith(("error:", "error executing", "failed:", "traceback"))


def _chat_title(tool_name: str, result: Optional[str], instruction_hint: Optional[str]) -> str:
    if instruction_hint and not instruction_hint.startswith("["):
        return instruction_hint
    result_text = _preview_result(result, 120).rstrip(".")
    if tool_name == "smart_open" and result_text:
        return result_text
    if tool_name == "file_operations":
        return "Inspected files"
    if tool_name == "execute_code":
        return "Ran helper code"
    if tool_name == "mode_control":
        return "Checked mode control"
    return tool_name.replace("_", " ").title()


def _is_compact_tool(tool_name: str) -> bool:
    return tool_name in _CHAT_COMPACT_TOOLS


def _persist_chat_tool_event(event: Dict[str, Any], limit: int = 200) -> bool:
    """Persist tool event on thread root params. Returns False if skipped as duplicate or on error."""
    try:
        from distr.core.db import Chat, get_session

        with get_session() as session:
            chat = session.get(Chat, int(event["chat_id"]))
            if not chat:
                return False
            params = _load_params(chat.params)
            events = params.get("tool_events")
            if not isinstance(events, list):
                events = []
            if events and _is_near_duplicate_tool_event(events[-1], event):
                return False
            events.append(event)
            params["tool_events"] = events[-limit:]
            chat.params = json.dumps(params)
            chat.modified_date = datetime.utcnow()
            session.commit()
            return True
    except Exception as e:
        logger.debug("persist chat tool event failed: %s", e)
        return False


def record_tool_execution(
    chat_id: Optional[int],
    tool_name: str,
    result: str,
    status: str = "completed",
    instruction_hint: Optional[str] = None,
    event_queue: Optional[Any] = None,
    user_text: Optional[str] = None,
    routing_path: Optional[str] = None,
    routing_hint: Optional[str] = None,
) -> None:
    """Record a tool execution to the chat-local audit log."""
    if not chat_id:
        return
    turn_chat_id = int(chat_id)
    try:
        from distr.core.db import get_session

        with get_session() as session:
            turn_chat_id = _resolve_turn_chat_row_id(session, int(chat_id))
    except Exception as e:
        logger.debug("turn_chat_row_id resolution failed: %s", e)
    chat_event = _build_chat_tool_event(
        int(chat_id),
        tool_name,
        result,
        status,
        instruction_hint,
        user_text,
        routing_hint if routing_hint is not None else routing_path,
        turn_chat_id=turn_chat_id,
    )
    if not _persist_chat_tool_event(chat_event):
        return

    inst = instruction_hint or f"Executed {tool_name}"
    pv = _preview_result(result)
    _activity_logger.info(
        "[agent_tool] chat_id=%s tool=%s status=%s instruction=%s result=%s",
        chat_id,
        tool_name,
        status,
        _preview_result(inst, 120),
        pv or "(empty)",
    )

    try:
        from distr.core.signals import signal_manager

        signal_manager.tool_executed.emit(chat_event)
    except Exception as e:
        logger.debug("tool_executed signal emit failed: %s", e)
