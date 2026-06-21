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

from distr.core.db.time import utc_now_naive

logger = logging.getLogger(__name__)
_activity_logger = logging.getLogger("distr.agent.activity")

_CHAT_COMPACT_TOOLS = {
    "execute_code",
    "file_operations",
    "mode_control",
}

_ACTIVITY_STYLE_PASSIVE = "passive"
_ACTIVITY_STYLE_ACTIVE = "active"


def _preview_result(text: Optional[str], limit: int = 220) -> str:
    if not text:
        return ""
    one_line = " ".join(str(text).split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."


def _format_llm_settings_pair(provider: Optional[str], model: Optional[str]) -> str:
    """Human-readable LLM provider/model for chat settings activity."""
    from distr.core.chat import _normalize_provider

    pname = _normalize_provider(provider) if provider else "—"
    mname = (model or "").strip() or "—"
    return f"{pname} / {mname}"


def _format_voice_settings_pair(voice_provider: Optional[str], voice_model: Optional[str]) -> str:
    """Human-readable voice provider/voice for chat settings activity."""
    from distr.core.agent.constants import normalize_voice_provider
    from distr.core.agent.service_factory import resolve_voice_to_display_name
    from distr.core.agent.services.tts.registry import tts_registry
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    vp = normalize_voice_provider(voice_provider or "")
    try:
        descriptor = tts_registry.get(vp)
        prov_name = descriptor.name or (voice_provider or "—")
    except KeyError:
        prov_name = (voice_provider or "").strip() or "—"
    voice_name = resolve_voice_to_display_name(vp, voice_model or "", settings) if voice_model else "—"
    return f"{prov_name} / {voice_name}"


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


def _resolve_turn_chat_row_id(session, chat_id: int) -> int | None:
    """Chat row for the active user turn, when the agent is working a reply.

    Tool audit uses the thread root id from the agent; assistant text is written on the latest
    child row under that root. Linking tool cards to that row keeps UI ordering correct.
    Outside an active turn, returns None so background work does not attach to the wrong reply.
    """
    from distr.core.db import Chat

    chat = session.get(Chat, int(chat_id))
    if not chat:
        return None
    root = chat
    guard = 0
    while root.parent_id is not None and guard < 64:
        guard += 1
        parent = session.get(Chat, int(root.parent_id))
        if not parent:
            break
        root = parent
    params = _load_params(root.params)
    active = params.get("active_turn_chat_row_id")
    if active is not None:
        try:
            return int(active)
        except (TypeError, ValueError):
            pass
    return None


def _latest_thread_row_id(session, chat_id: int) -> int | None:
    """Most recent row in the chat thread (root or child), for anchoring feed activity."""
    from sqlalchemy import text

    from distr.core.db import Chat

    row = session.execute(
        text(
            """
            WITH RECURSIVE chat_thread AS (
                SELECT id, parent_id, created_date FROM chats WHERE id = :root_id
                UNION ALL
                SELECT c.id, c.parent_id, c.created_date FROM chats c
                INNER JOIN chat_thread ct ON c.parent_id = ct.id
            )
            SELECT id FROM chat_thread ORDER BY created_date DESC LIMIT 1
            """
        ),
        {"root_id": int(chat_id)},
    ).fetchone()
    if row and row[0] is not None:
        return int(row[0])
    chat = session.get(Chat, int(chat_id))
    return int(chat.id) if chat and chat.id is not None else None


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
    turn_chat_id: Optional[int],
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    normalized_tool = (tool_name or "tool").strip()
    normalized_status = (status or "completed").lower()
    if _looks_like_error(result):
        normalized_status = "failed"
    activity_style = _tool_activity_style(normalized_tool, result, instruction_hint)
    title = _tool_activity_title(normalized_tool, result, instruction_hint)
    event = {
        "id": f"tool-{chat_id}-{now}",
        "event": "tool_executed",
        "chat_id": int(chat_id),
        "tool_name": normalized_tool,
        "title": _preview_result(title, 140) or "Tool executed",
        "result_summary": _preview_result(result, 420),
        "result_detail": _full_result_for_chat(result),
        "status": normalized_status,
        "timestamp": now,
        "chat_visible": True,
        "chat_compact": _is_compact_tool(normalized_tool) or activity_style == _ACTIVITY_STYLE_PASSIVE,
        "activity_style": activity_style,
    }
    if turn_chat_id is not None:
        event["turn_chat_id"] = int(turn_chat_id)
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
    if tool_name == "chat_settings":
        hint = (instruction_hint or "").strip()
        if hint:
            return hint
        first_line = (result or "").strip().splitlines()[0].strip() if result else ""
        if first_line:
            return first_line
        return "Updated chat settings"
    if tool_name == "read_aloud":
        return (instruction_hint or "").strip() or "Read aloud"
    return tool_name.replace("_", " ").title()


def _is_compact_tool(tool_name: str) -> bool:
    return tool_name in _CHAT_COMPACT_TOOLS


def _tool_activity_style(tool_name: str, result: Optional[str], instruction_hint: Optional[str]) -> str:
    normalized_tool = (tool_name or "").strip()
    lowered_result = (result or "").strip().lower()
    lowered_hint = (instruction_hint or "").strip().lower()
    if normalized_tool in {"execute_code", "file_operations", "mode_control", "chat_settings", "read_aloud"}:
        return _ACTIVITY_STYLE_PASSIVE
    if normalized_tool == "clipboard_action":
        if lowered_result.startswith("clipboard updated"):
            return _ACTIVITY_STYLE_ACTIVE
        if lowered_result.startswith("clipboard content:"):
            return _ACTIVITY_STYLE_PASSIVE
        if lowered_result.startswith("reading ") or lowered_result.startswith("read_action:"):
            return _ACTIVITY_STYLE_ACTIVE
        if "explain" in lowered_hint or "elaborate" in lowered_hint:
            return _ACTIVITY_STYLE_PASSIVE
    return _ACTIVITY_STYLE_ACTIVE


def _tool_activity_title(tool_name: str, result: Optional[str], instruction_hint: Optional[str]) -> str:
    normalized_tool = (tool_name or "").strip()
    raw_result = (result or "").strip()
    lowered_result = raw_result.lower()
    lowered_hint = (instruction_hint or "").strip().lower()
    if normalized_tool == "clipboard_action":
        if lowered_result.startswith("clipboard content:"):
            return "Ingested clipboard into context"
        if lowered_result.startswith("clipboard updated"):
            return "Updated clipboard"
        if lowered_result.startswith("reading ") or lowered_result.startswith("read_action:"):
            return "Read clipboard aloud"
        if "explain" in lowered_hint:
            return "Used selected text for explanation"
        if "elaborate" in lowered_hint:
            return "Used selected text for elaboration"
    if normalized_tool == "mouse_movement" and lowered_result.startswith("moved mouse"):
        return raw_result.rstrip(".")
    return _chat_title(normalized_tool, result, instruction_hint)


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
            chat.modified_date = utc_now_naive()
            session.commit()
            return True
    except Exception as e:
        logger.debug("persist chat tool event failed: %s", e)
        return False


def record_chat_settings_change(
    chat_id: int,
    *,
    previous: Dict[str, Optional[str]],
    current: Dict[str, Optional[str]],
) -> Optional[Dict[str, Any]]:
    """Record LLM/voice settings changes as visible system activity in the chat feed."""
    def _norm_pair(provider: Optional[str], model: Optional[str]) -> tuple[str, str]:
        return ((provider or "").strip(), (model or "").strip())

    llm_prev = _norm_pair(previous.get("provider"), previous.get("model_name"))
    llm_cur = _norm_pair(current.get("provider"), current.get("model_name"))
    voice_prev = _norm_pair(previous.get("voice_provider"), previous.get("voice_model"))
    voice_cur = _norm_pair(current.get("voice_provider"), current.get("voice_model"))

    lines: list[str] = []
    if llm_cur != llm_prev and any(llm_cur):
        if any(llm_prev):
            lines.append(
                f"LLM: {_format_llm_settings_pair(llm_prev[0], llm_prev[1])} "
                f"→ {_format_llm_settings_pair(llm_cur[0], llm_cur[1])}"
            )
        else:
            lines.append(f"LLM: {_format_llm_settings_pair(llm_cur[0], llm_cur[1])}")
    if voice_cur != voice_prev and any(voice_cur):
        if any(voice_prev):
            lines.append(
                f"Voice: {_format_voice_settings_pair(voice_prev[0], voice_prev[1])} "
                f"→ {_format_voice_settings_pair(voice_cur[0], voice_cur[1])}"
            )
        else:
            lines.append(f"Voice: {_format_voice_settings_pair(voice_cur[0], voice_cur[1])}")
    if not lines:
        return None

    summary = "\n".join(lines)
    summary_title = lines[0] if lines else "Updated chat settings"

    event = _build_chat_tool_event(
        int(chat_id),
        "chat_settings",
        summary,
        "completed",
        summary_title,
        None,
        None,
        turn_chat_id=None,
    )
    event["chat_visible"] = True
    event["chat_compact"] = False
    if not _persist_chat_tool_event(event):
        return None

    _activity_logger.info(
        "[agent_tool] chat_id=%s tool=chat_settings status=completed instruction=Changed chat settings result=%s",
        chat_id,
        _preview_result(summary, 120) or "(empty)",
    )
    try:
        from distr.core.signals import signal_manager

        signal_manager.tool_executed.emit(event)
    except Exception as e:
        logger.debug("tool_executed signal emit failed: %s", e)
    return event


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
    chat_visible: Optional[bool] = None,
) -> None:
    """Record a tool execution to the chat-local audit log."""
    if not chat_id:
        return
    turn_chat_id: int | None = None
    try:
        from distr.core.db import get_session

        with get_session() as session:
            turn_chat_id = _resolve_turn_chat_row_id(session, int(chat_id))
    except Exception as e:
        logger.debug("turn_chat_row_id resolution failed: %s", e)
    visible = chat_visible
    if visible is None:
        visible = turn_chat_id is not None
    if turn_chat_id is None:
        turn_chat_id = int(chat_id)
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
    chat_event["chat_visible"] = bool(visible)
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
