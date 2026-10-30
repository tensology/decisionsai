"""
API routes/endpoints for Chat web UI
"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from starlette.requests import ClientDisconnect, Request
from pydantic import BaseModel, ConfigDict, Field
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Iterable
from collections import defaultdict
import logging
import json
import re
import asyncio
import threading
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

# WebSocket connection manager: chat_id -> set of WebSocket instances subscribed to that chat
_chat_ws_connections: Dict[int, Set[WebSocket]] = {}
_chat_ws_lock = threading.Lock()


async def broadcast_chat_event(payload: Dict[str, Any]) -> int:
    """Broadcast an already validated event to subscribers for its chat."""
    chat_id = int(payload["chat_id"])
    encoded = json.dumps(payload)
    with _chat_ws_lock:
        conns = list(_chat_ws_connections.get(chat_id, set()))
    failed = []
    for ws in conns:
        try:
            await ws.send_text(encoded)
        except Exception as exc:
            logger.debug("WebSocket send error: %s", exc)
            failed.append(ws)
    if failed:
        with _chat_ws_lock:
            registered = _chat_ws_connections.get(chat_id)
            if registered is not None:
                registered.difference_update(failed)
                if not registered:
                    _chat_ws_connections.pop(chat_id, None)
    if payload.get("event") in {"message_added", "stream_finished"}:
        await broadcast_topic_event("chat_list", {"type": "chat_list_changed", "chat_id": chat_id})
    return len(conns) - len(failed)


# Topic subscribers (e.g. "incoming") on the same /api/ws socket: send {"subscribe_topic": "incoming"}.
_topic_ws_connections: Dict[str, Set[WebSocket]] = {}
INCOMING_TOPIC = "incoming"
INCOMING_MESSAGE_EVENT = "incoming_message"


async def broadcast_topic_event(topic: str, payload: Dict[str, Any]) -> int:
    """Broadcast an event to every socket subscribed to ``topic``."""
    encoded = json.dumps(payload)
    with _chat_ws_lock:
        conns = list(_topic_ws_connections.get(topic, set()))
    failed = []
    for ws in conns:
        try:
            await ws.send_text(encoded)
        except Exception as exc:
            logger.debug("WebSocket topic send error: %s", exc)
            failed.append(ws)
    if failed:
        with _chat_ws_lock:
            registered = _topic_ws_connections.get(topic)
            if registered is not None:
                registered.difference_update(failed)
                if not registered:
                    _topic_ws_connections.pop(topic, None)
    return len(conns) - len(failed)


def publish_topic_event_threadsafe(topic: str, payload: Dict[str, Any]):
    """Schedule a topic broadcast on the embedded server loop (no-op when it is not running)."""
    from distr.gui.web.server import get_unified_server

    server = get_unified_server()
    loop = getattr(server, "asyncio_loop", None) if server else None
    if not server or not server.is_running or loop is None or loop.is_closed():
        return None
    return asyncio.run_coroutine_threadsafe(broadcast_topic_event(topic, dict(payload)), loop)


def _register_incoming_push_listeners() -> None:
    """Push ``incoming_message`` after a WhatsApp or Telegram message row is committed."""
    try:
        from sqlalchemy import event as sa_event
        from sqlalchemy.orm import Session as SASession
        from distr.core.db import TelegramGroupMessage, WhatsAppMessage
    except Exception as exc:  # pragma: no cover - optional at import time
        logger.debug("Incoming push listeners unavailable: %s", exc)
        return

    def _queue(channel):
        def _after_insert(_mapper, _connection, target):
            try:
                from sqlalchemy.orm import object_session

                session = object_session(target)
                if session is not None:
                    session.info.setdefault("_incoming_push", []).append((channel, getattr(target, "id", None)))
            except Exception:
                pass

        return _after_insert

    sa_event.listen(WhatsAppMessage, "after_insert", _queue("whatsapp"))
    sa_event.listen(TelegramGroupMessage, "after_insert", _queue("telegram"))

    @sa_event.listens_for(SASession, "after_commit")
    def _publish_incoming(session):
        pending = session.info.pop("_incoming_push", None)
        for channel, message_id in pending or []:
            try:
                publish_topic_event_threadsafe(
                    INCOMING_TOPIC,
                    {"type": INCOMING_MESSAGE_EVENT, "channel": channel, "message_id": message_id},
                )
            except Exception as exc:
                logger.debug("Incoming push failed: %s", exc)

    @sa_event.listens_for(SASession, "after_rollback")
    def _drop_incoming(session):
        session.info.pop("_incoming_push", None)


_register_incoming_push_listeners()


def publish_chat_event_threadsafe(payload: Dict[str, Any]):
    """Schedule a chat broadcast directly on the embedded server loop."""
    from distr.gui.web.server import get_unified_server

    server = get_unified_server()
    loop = getattr(server, "asyncio_loop", None) if server else None
    if not server or not server.is_running or loop is None or loop.is_closed():
        raise RuntimeError("Unified web server event loop is unavailable")
    return asyncio.run_coroutine_threadsafe(broadcast_chat_event(dict(payload)), loop)

from distr.core.db import get_session, Chat
from distr.core.chat import ChatService, record_chat_audit_event
from distr.core.chat_sidebar import chat_skin, set_chat_skin, sidebar_state, validate_chat_skin
from distr.core.chat_agents import agent_identity
from distr.core.settings import load_settings_from_db
from distr.core.agent.service_factory import resolve_voice_to_display_name
from distr.core.agent.constants import normalize_voice_provider
from distr.core.agent.services.tts.registry import tts_registry
from distr.core.chat_title_auto import (
    TITLE_REFRESH_MESSAGE_INTERVAL,
    maybe_refresh_chat_title as _maybe_refresh_chat_title,
    _title_auto_meta,
)
from distr.core.workflow.development_threads import is_development_thread
from distr.gui.web.security import (
    is_allowed_local_origin,
    websocket_has_valid_internal_token,
    rate_limiter,
    require_internal_token_dependency,
)
from fastapi import Depends

logger = logging.getLogger(__name__)


def _latest_turn_summary(chat_id: int, limit: int = 8000) -> Dict[str, Any]:
    """Cheap latest-turn fields for the WS ``chat_updated`` event (one indexed query)."""
    with get_session() as session:
        root = session.get(Chat, int(chat_id))
        if root is None:
            return {}
        row = (
            session.query(Chat)
            .filter(Chat.parent_id == root.id)
            .order_by(Chat.id.desc())
            .first()
        ) or root
        try:
            params = json.loads(root.params or "{}")
        except (TypeError, ValueError):
            params = {}
        active = params.get("active_turn_chat_row_id") if isinstance(params, dict) else None
        text = row.response or ""
        return {
            "turn_id": int(row.id),
            "assistant_text": text[:limit],
            "assistant_text_truncated": len(text) > limit,
            "final": bool(text) and str(active or "") != str(row.id),
        }


def _reject_development_lifecycle(session, chat: Chat) -> None:
    if is_development_thread(session, chat):
        raise HTTPException(
            status_code=409,
            detail="Development tasks must use the Development lifecycle API.",
        )

_usage_status_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
_usage_status_lock = threading.Lock()
_USAGE_STATUS_TTL_SEC = 90.0


class ChatRequestModel(BaseModel):
    """Base request model for chat API payloads."""

    model_config = ConfigDict(protected_namespaces=())


def _voice_model_to_display_name(
    voice_provider: Optional[str], voice_model: Optional[str], settings: dict
) -> str:
    """Derive assistant display name from voice (chat or settings). Resolves ElevenLabs IDs to names."""
    tts = (settings.get("tts_provider") or "").strip()
    vp = normalize_voice_provider(voice_provider or tts)
    return resolve_voice_to_display_name(vp, voice_model or "", settings)


def _safe_json_obj(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _chat_tool_event_messages(chat: Chat) -> List[Dict[str, Any]]:
    params = _safe_json_obj(getattr(chat, "params", None))
    events = params.get("tool_events")
    if not isinstance(events, list):
        return []
    messages: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if not _is_visible_tool_event(event):
            continue
        messages.append(
            {
                "role": "tool",
                "content": event.get("result_summary") or "",
                "timestamp": event.get("timestamp"),
                "turn_chat_id": event.get("turn_chat_id"),
                "tool_event": {
                    "tool_name": event.get("tool_name") or "tool",
                    "title": event.get("title") or "Tool executed",
                    "status": event.get("status") or "completed",
                    "result_summary": event.get("result_summary") or "",
                    "result_detail": event.get("result_detail") or "",
                    "routing_path": event.get("routing_path") or "",
                    "user_text": event.get("user_text") or "",
                    "compact": _is_compact_tool_event(event),
                    "activity_style": event.get("activity_style") or "active",
                },
            }
        )
    return messages


def _is_visible_tool_event(event: Dict[str, Any]) -> bool:
    if event.get("chat_suppressed") is True:
        return False
    if event.get("chat_visible") is False:
        return False
    return True


def _is_compact_tool_event(event: Dict[str, Any]) -> bool:
    if event.get("chat_compact") is not None:
        return bool(event.get("chat_compact"))
    tool_name = (event.get("tool_name") or "").strip()
    return tool_name in {"execute_code", "file_operations", "mode_control"}


def _is_visible_workflow_event(event: Dict[str, Any]) -> bool:
    if event.get("chat_suppressed") is True:
        return False
    if event.get("chat_visible") is False:
        return False
    # Agent-chat automations already run via orchestrator + Automations hub history.
    if str(event.get("type") or "").strip().lower() == "automation_run":
        return False
    return True


def _chat_workflow_event_messages(chat: Chat) -> List[Dict[str, Any]]:
    params = _safe_json_obj(getattr(chat, "params", None))
    events = params.get("workflow_events")
    if not isinstance(events, list):
        return []
    messages: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if not _is_visible_workflow_event(event):
            continue
        messages.append(
            {
                "role": "workflow",
                "content": event.get("summary") or "",
                "timestamp": event.get("timestamp"),
                "workflow_event": {
                    "id": event.get("id"),
                    "run_id": event.get("run_id"),
                    "workflow_id": event.get("workflow_id"),
                    "workflow_name": event.get("workflow_name") or "Workflow",
                    "type": event.get("type") or "event",
                    "status": event.get("status") or "running",
                    "summary": event.get("summary") or "",
                    "phase": event.get("phase") or "",
                    "step_id": event.get("step_id"),
                    "step_name": event.get("step_name") or "",
                    "agent_activity": event.get("agent_activity") or {},
                },
            }
        )
    return messages


def _message_sort_key(message: Dict[str, Any]) -> str:
    ts = message.get("timestamp")
    if ts is None:
        return ""
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def _timestamp_as_naive_utc(ts: Optional[datetime]) -> Optional[datetime]:
    """Normalize DB and ISO timestamps to naive UTC for safe comparison."""
    if ts is None:
        return None
    if ts.tzinfo is not None:
        return ts.astimezone(UTC).replace(tzinfo=None)
    return ts


def _api_timestamp(ts: Any) -> Optional[str]:
    """Serialize persisted timestamps for the web UI (always UTC with Z suffix)."""
    if ts is None:
        return None
    if isinstance(ts, str):
        text = ts.strip()
        if not text:
            return None
        if text.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", text):
            return text
        normalized = text.replace(" ", "T", 1)
        if "T" in normalized:
            return normalized + "Z"
        return text
    if isinstance(ts, datetime):
        naive = _timestamp_as_naive_utc(ts)
        return f"{naive.isoformat()}Z" if naive is not None else None
    return str(ts)


def _duration_seconds_between(started_at: Any, completed_at: Any) -> int:
    """Return a stable non-negative duration for DB or ISO timestamps."""
    def parse(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return _timestamp_as_naive_utc(value)
        text = _api_timestamp(value)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _timestamp_as_naive_utc(parsed)

    started = parse(started_at)
    completed = parse(completed_at)
    if not started or not completed:
        return 0
    return max(0, int((completed - started).total_seconds()))


def _parse_message_timestamp(message: Dict[str, Any]) -> Optional[datetime]:
    """Parse a message timestamp for chronological insertion."""
    ts = message.get("timestamp")
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return _timestamp_as_naive_utc(ts)
    text = str(ts).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return _timestamp_as_naive_utc(datetime.fromisoformat(normalized))
    except ValueError:
        return None


def _insert_extra_messages(messages: List[Dict[str, Any]], extras: List[Dict[str, Any]]) -> None:
    """Insert orphan tool/workflow rows into the thread by timestamp."""
    for extra in extras:
        extra_ts = _parse_message_timestamp(extra)
        if extra_ts is None:
            messages.append(extra)
            continue
        inserted = False
        for index, msg in enumerate(messages):
            msg_ts = _parse_message_timestamp(msg)
            if msg_ts is not None and extra_ts < msg_ts:
                messages.insert(index, extra)
                inserted = True
                break
        if not inserted:
            messages.append(extra)


def _estimate_tokens(text: str) -> int:
    """Cheap context estimate for UI pressure. Avoids provider tokenizers on hot paths."""
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return 0
    return max(1, int(len(cleaned) / 4))


def _context_window_for_model(provider: str, model_name: str) -> int:
    """Best-effort context window used for the header ring."""
    from distr.core.services.context_window import context_window_for_model

    return context_window_for_model(provider, model_name)


def _context_stats(messages: List[Dict[str, Any]], provider: str, model_name: str) -> Dict[str, Any]:
    token_estimate = sum(_estimate_tokens(m.get("content") or "") for m in messages)
    window = _context_window_for_model(provider, model_name)
    percent = min(100, round((token_estimate / max(window, 1)) * 100))
    if token_estimate > 0 and percent == 0:
        percent = 1
    return {
        "estimated_tokens": token_estimate,
        "context_window": window,
        "percent_used": percent,
        "message_count": len([m for m in messages if m.get("role") in {"user", "assistant"}]),
        "auto_compact_threshold": 75,
    }


def _cached_usage_status(cache_key: str, fetch_fn) -> Dict[str, Any]:
    now = time.time()
    with _usage_status_lock:
        cached = _usage_status_cache.get(cache_key)
        if cached and now - cached[0] < _USAGE_STATUS_TTL_SEC:
            return dict(cached[1])
    payload = fetch_fn()
    with _usage_status_lock:
        _usage_status_cache[cache_key] = (now, dict(payload))
    return payload


def _request_json(url: str, headers: Dict[str, str], timeout: float = 8.0) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    parsed = json.loads(raw or "{}")
    return parsed if isinstance(parsed, dict) else {}


def _elevenlabs_usage_status(settings: Dict[str, Any]) -> Dict[str, Any]:
    key = (settings.get("elevenlabs_key") or "").strip()
    if not key:
        return {"available": False, "provider": "elevenlabs", "error": "ElevenLabs API key is not configured."}

    def _fetch() -> Dict[str, Any]:
        try:
            data = _request_json(
                "https://api.elevenlabs.io/v1/user/subscription",
                {"xi-api-key": key},
            )
            used = int(data.get("character_count") or 0)
            limit = int(data.get("character_limit") or 0)
            remaining = max(limit - used, 0) if limit else None
            percent = round((used / limit) * 100) if limit else 0
            reset = data.get("next_character_count_reset_unix")
            return {
                "available": True,
                "provider": "elevenlabs",
                "label": "ElevenLabs",
                "used": used,
                "limit": limit,
                "remaining": remaining,
                "percent_used": max(0, min(100, percent)),
                "tier": data.get("tier") or "",
                "status": data.get("status") or "",
                "max_credit_limit_extension": data.get("max_credit_limit_extension"),
                "current_overage": data.get("current_overage") or {},
                "billing_period": data.get("billing_period") or "",
                "character_refresh_period": data.get("character_refresh_period") or "",
                "next_reset_unix": reset,
            }
        except Exception as exc:
            logger.debug("ElevenLabs usage status unavailable: %s", exc)
            return {"available": False, "provider": "elevenlabs", "error": "ElevenLabs usage unavailable."}

    return _cached_usage_status("elevenlabs", _fetch)


def _openai_usage_status(settings: Dict[str, Any]) -> Dict[str, Any]:
    key = (settings.get("openai_key") or "").strip()
    if not key:
        return {"available": False, "provider": "openai", "error": "OpenAI API key is not configured."}

    def _fetch() -> Dict[str, Any]:
        end = int(time.time())
        start_dt = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = int(start_dt.timestamp())
        qs = urllib.parse.urlencode({"start_time": start, "end_time": end, "limit": 31})
        try:
            data = _request_json(
                f"https://api.openai.com/v1/organization/costs?{qs}",
                {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            total = 0.0
            currency = "usd"
            for bucket in data.get("data") or []:
                for result in bucket.get("results") or []:
                    amount = result.get("amount") or {}
                    try:
                        total += float(amount.get("value") or 0)
                    except (TypeError, ValueError):
                        pass
                    currency = amount.get("currency") or currency
            return {
                "available": True,
                "provider": "openai",
                "label": "OpenAI",
                "period_cost": round(total, 4),
                "currency": currency,
                "period_start_unix": start,
                "period_end_unix": end,
                "note": "Costs endpoint does not expose remaining balance/cap for every account.",
            }
        except Exception as exc:
            logger.debug("OpenAI usage status unavailable: %s", exc)
            return {"available": False, "provider": "openai", "error": "OpenAI billing usage unavailable for this key."}

    return _cached_usage_status("openai-costs", _fetch)


def _effective_context_messages(
    messages: List[Dict[str, Any]], checkpoint: Any
) -> List[Dict[str, Any]]:
    if not isinstance(checkpoint, dict) or not (checkpoint.get("summary") or "").strip():
        return messages
    try:
        checkpoint_row_id = int(checkpoint.get("chat_row_id") or 0)
    except (TypeError, ValueError):
        checkpoint_row_id = 0
    effective = [{"role": "system", "content": str(checkpoint.get("summary") or "")}]
    for msg in messages:
        try:
            row_id = int(msg.get("chat_row_id") or 0)
        except (TypeError, ValueError):
            row_id = 0
        if row_id and checkpoint_row_id and row_id <= checkpoint_row_id:
            continue
        effective.append(msg)
    return effective


def _chat_additional_context(raw: Optional[str]) -> Dict[str, Any]:
    return _safe_json_obj(raw)


def _row_chat_marker(row: Any) -> Optional[Dict[str, Any]]:
    ctx = _chat_additional_context(getattr(row, "additional_context", None))
    marker = ctx.get("chat_marker")
    return marker if isinstance(marker, dict) else None


def _legacy_chat_marker_from_response(response: Optional[str]) -> Optional[Dict[str, Any]]:
    text = (response or "").strip()
    if text.startswith("Context compacted."):
        return {"type": "compact", "reason": "manual"}
    fork_match = re.match(
        r"^Fork started from compact checkpoint for chat #(\d+)",
        text,
    )
    if fork_match:
        return {
            "type": "fork",
            "source_chat_id": int(fork_match.group(1)),
            "compacted": True,
        }
    return None


def _divider_message_from_row(row: Any, marker: Dict[str, Any]) -> Dict[str, Any]:
    ts = getattr(row, "modified_date", None) or getattr(row, "created_date", None)
    return {
        "role": "divider",
        "timestamp": _api_timestamp(ts),
        "chat_row_id": row.id,
        "divider": dict(marker),
    }


def _append_row_messages(messages: List[Dict[str, Any]], row: Any) -> None:
    """Turn one chat thread row into user/assistant/divider API messages."""
    if getattr(row, "is_hidden", False):
        return
    marker = _row_chat_marker(row)
    if not marker and getattr(row, "response", None):
        marker = _legacy_chat_marker_from_response(row.response)
    if marker:
        if marker.get("type") == "compact" and marker.get("hide_in_source_on_fork"):
            return
        messages.append(_divider_message_from_row(row, marker))
        if marker.get("type") in {"compact", "fork"}:
            return
    if row.input:
        message = {
            "role": "user",
            "content": row.input,
            "timestamp": _api_timestamp(row.created_date),
            "chat_row_id": row.id,
        }
        try:
            params = json.loads(getattr(row, "params", None) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            params = {}
        turn_metadata = params.get("development_turn") if isinstance(params, dict) else {}
        skill_ids = turn_metadata.get("skill_ids") if isinstance(turn_metadata, dict) else []
        if isinstance(skill_ids, list):
            message["skills"] = [str(skill_id) for skill_id in skill_ids if str(skill_id).strip()][:12]
        if getattr(row, "image", None):
            from distr.core.chat_images import chat_image_url, resolve_chat_image

            image_file = resolve_chat_image(row.image)
            if image_file:
                message["image_url"] = chat_image_url(image_file.name)
        messages.append(message)
    if row.response:
        started_at = getattr(row, "created_date", None)
        completed_at = getattr(row, "modified_date", None)
        duration_seconds = _duration_seconds_between(started_at, completed_at)
        messages.append(
            {
                "role": "assistant",
                "content": row.response,
                "timestamp": _api_timestamp(row.modified_date),
                "turn_started_at": _api_timestamp(started_at),
                "turn_duration_seconds": duration_seconds,
                "chat_row_id": row.id,
            }
        )
        _spoken_ctx = _chat_additional_context(getattr(row, "additional_context", None))
        if _spoken_ctx.get("spoken") is True:
            messages[-1]["spoken"] = True
            if _spoken_ctx.get("spoken_source_row_id") is not None:
                messages[-1]["spoken_source_row_id"] = _spoken_ctx.get("spoken_source_row_id")


def _thread_rows_for_compaction(session, chat_id: int):
    from sqlalchemy import text

    thread_query = text("""
        WITH RECURSIVE chat_thread AS (
            SELECT id, parent_id, input, response, created_date, modified_date, is_hidden, additional_context
            FROM chats
            WHERE id = :root_id
            UNION ALL
            SELECT c.id, c.parent_id, c.input, c.response, c.created_date, c.modified_date, c.is_hidden, c.additional_context
            FROM chats c
            INNER JOIN chat_thread ct ON c.parent_id = ct.id
        )
        SELECT * FROM chat_thread ORDER BY created_date ASC
    """)
    return session.execute(thread_query, {"root_id": chat_id}).fetchall()


def _messages_from_rows(rows) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for row in rows:
        _append_row_messages(messages, row)
    return messages


def _fallback_compact_summary(messages: List[Dict[str, Any]]) -> str:
    if not messages:
        return "No prior conversation content."
    first = messages[:4]
    last = messages[-8:]
    lines = [
        "Conversation checkpoint:",
        f"- Compacted {len(messages)} visible message(s).",
    ]
    if first:
        lines.append("- Opening context:")
        for msg in first:
            lines.append(f"  - {msg.get('role')}: {str(msg.get('content') or '').strip()[:240]}")
    if last:
        lines.append("- Most recent context:")
        for msg in last:
            lines.append(f"  - {msg.get('role')}: {str(msg.get('content') or '').strip()[:320]}")
    return "\n".join(lines)


def _summarize_chat_with_model(
    messages: List[Dict[str, Any]],
    *,
    provider: str,
    model_name: str,
    settings: dict,
) -> tuple[str, str]:
    transcript = []
    for msg in messages[-80:]:
        role = msg.get("role") or "message"
        content = str(msg.get("content") or "").strip()
        if content:
            transcript.append(f"{role.upper()}: {content}")
    transcript_text = "\n\n".join(transcript)
    if not transcript_text:
        return "No prior conversation content.", "empty"
    try:
        import litellm
        from distr.core.workflow.planning import _litellm_model

        response = litellm.completion(
            model=_litellm_model((provider or "").strip().lower(), model_name or "", settings),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Create a compact checkpoint for an AI chat. Preserve user goals, "
                        "decisions, constraints, unresolved work, current settings, and any "
                        "facts the next agent turn needs. Do not add filler."
                    ),
                },
                {"role": "user", "content": transcript_text[:120_000]},
            ],
            max_tokens=1800,
            temperature=0.2,
        )
        summary = (response.choices[0].message.content or "").strip()
        if summary:
            return summary, "llm"
    except Exception:
        logger.warning("Chat compaction LLM summary failed; using fallback", exc_info=True)
    return _fallback_compact_summary(messages), "fallback"


def _chat_header_stats_payload(
    *,
    root_chat: Chat,
    messages: List[Dict[str, Any]],
    provider: str,
    model_name: str,
) -> Dict[str, Any]:
    additional_context = _chat_additional_context(root_chat.additional_context)
    compact_checkpoint = additional_context.get("compact_checkpoint")
    context_stats = _context_stats(
        _effective_context_messages(messages, compact_checkpoint),
        provider,
        model_name,
    )
    title_auto = _title_auto_meta(additional_context)
    return {
        "title": root_chat.title or "New Chat",
        "context_stats": context_stats,
        "compact_checkpoint": compact_checkpoint,
        "title_auto": {
            "manual": bool(title_auto.get("manual")),
            "last_refresh_message_count": int(title_auto.get("last_refresh_message_count") or 0),
            "interval": TITLE_REFRESH_MESSAGE_INTERVAL,
        },
    }


def _merge_thread_rows_with_tool_and_workflow_events(
    rows: Iterable[Any], root_chat: Chat
) -> List[Dict[str, Any]]:
    """Interleave tool cards with the chat row they belong to; avoid sorting all messages by timestamp."""
    tool_msgs = _chat_tool_event_messages(root_chat)
    tools_by_turn: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    orphan_tools: List[Dict[str, Any]] = []
    for tm in tool_msgs:
        tid = tm.get("turn_chat_id")
        if tid is None:
            orphan_tools.append(tm)
        else:
            tools_by_turn[int(tid)].append(tm)

    messages: List[Dict[str, Any]] = []
    for row in rows:
        _append_row_messages(messages, row)
        for tm in tools_by_turn.pop(row.id, []):
            messages.append(tm)

    leftover: List[Dict[str, Any]] = []
    for lst in tools_by_turn.values():
        leftover.extend(lst)
    orphan_tools.extend(leftover)
    orphan_tools.sort(key=_message_sort_key)

    workflow_msgs = _chat_workflow_event_messages(root_chat)
    workflow_msgs.sort(key=_message_sort_key)

    extras = orphan_tools + workflow_msgs
    extras.sort(key=_message_sort_key)
    _insert_extra_messages(messages, extras)

    return messages


def _restore_development_execution_response(
    messages: List[Dict[str, Any]], development: Dict[str, Any]
) -> None:
    """Display a preserved agent result when an old Initiative notice replaced it."""
    execution = development.get("execution") if isinstance(development, dict) else {}
    summary = str((execution or {}).get("summary") or "").strip()
    if not summary:
        return
    for message in reversed(messages):
        content = str(message.get("content") or "")
        if (
            message.get("role") == "assistant"
            and "pending approval" in content.lower()
            and "automation_recommendation" in content.lower()
        ):
            message["content"] = summary
            return


class SendMessageRequest(ChatRequestModel):
    """Request to send a message"""

    message: str
    agent_message: Optional[str] = None  # LLM-only text when message is a short display brief
    chat_id: Optional[int] = None  # If None, creates new chat
    speak: Optional[bool] = (
        None  # If True, frontend will play TTS after response (web chat)
    )
    provider: Optional[str] = (
        None  # Chat's LLM provider for hot-swap (prefer over DB lookup)
    )
    model_name: Optional[str] = (
        None  # Chat's LLM model for hot-swap (prefer over DB lookup)
    )
    voice_provider: Optional[str] = (
        None  # Chat's TTS voice provider (persist to chat so agent/persona match displayed voice)
    )
    voice_model: Optional[str] = (
        None  # Chat's TTS voice (e.g. Adam); persist to chat so agent says "I'm Adam" not wrong name
    )
    intake_source_message_id: Optional[str] = None
    image_path: Optional[str] = None  # Oracle chat image attachment (from POST /chat-images)
    intake_requested_outcome: Optional[str] = None
    intake_metadata: Optional[Dict[str, Any]] = None
    origin_surface: Optional[str] = None
    origin_request_id: Optional[str] = None
    input_type: Optional[str] = None


class SteerTurnRequest(ChatRequestModel):
    """Plain-English guidance for an active turn; Stop remains separate."""

    message: str


class CreateChatRequest(ChatRequestModel):
    """Request to create a new chat (used by web UI and empty-state / starting form)."""

    skin_name: Optional[str] = None
    agent_name: Optional[str] = Field(default=None, max_length=80)
    agent_role: str = Field(default="", max_length=4000)
    title: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    voice_provider: Optional[str] = None
    voice_model: Optional[str] = None
    starting_question: Optional[str] = None
    project_id: Optional[int] = None
    route_mode: str = "auto"
    execution_profile: str = "code"
    autonomy_level: str = "full"
    speak: Optional[bool] = (
        None  # If True, agent speaks the reply (TTS). Used when starting_question is set.
    )


class UpdateChatRequest(ChatRequestModel):
    """Request to update chat. All fields optional; only provided fields are updated."""

    skin_name: Optional[str] = None
    title: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    voice_provider: Optional[str] = None
    voice_model: Optional[str] = None
    project_id: Optional[int] = None
    route_mode: Optional[str] = None
    execution_profile: Optional[str] = None
    autonomy_level: Optional[str] = None


class ReadChatRequest(ChatRequestModel):
    reply_id: int = Field(ge=0)


class PrimaryAvatarRequest(ChatRequestModel):
    skin_name: str = Field(min_length=1, max_length=128)


class CompactChatRequest(ChatRequestModel):
    """Request to compact a chat into an orchestrator-backed checkpoint."""

    provider: Optional[str] = None
    model_name: Optional[str] = None
    reason: Optional[str] = None


class ForkChatRequest(ChatRequestModel):
    """Request to fork a chat after compacting it."""

    provider: Optional[str] = None
    model_name: Optional[str] = None
    title: Optional[str] = None


class ReadResponseRequest(ChatRequestModel):
    """Spoken-style rewrite of an assistant reply (Read response button)."""

    text: str
    chat_row_id: Optional[int] = None


class SpeakSpokenRequest(ChatRequestModel):
    text: str
    chat_id: Optional[int] = None  # Development thread the spoken text belongs to
    source_row_id: Optional[int] = None  # reply the spoken text summarises


class TTSGenerateRequest(ChatRequestModel):
    """Request to generate TTS audio for text"""

    text: str
    provider: Optional[str] = None
    voice: Optional[str] = None
    speed: Optional[float] = 1.0
    format: Optional[str] = "wav"  # "wav" or "mp3"
    chat_id: Optional[int] = (
        None  # If set, use this chat's voice_provider/voice_model when provider/voice not provided
    )


def create_routes(templates_dir: Path, base_path: str = "") -> APIRouter:
    """
    Create and configure API routes for chat web UI.
    The chat HTML page is served by the unified app from settings templates (same base as actions/skills/projects).
    """
    router = APIRouter()

    @router.get('/agents/primary')
    async def get_primary_agent():
        from distr.core.chat_agents import ensure_primary_agent

        return JSONResponse({'primary_agent': ensure_primary_agent(load_settings_from_db())})

    @router.patch('/agents/primary/avatar')
    async def change_primary_avatar(body: PrimaryAvatarRequest):
        from distr.core.chat_agents import ensure_primary_agent
        from distr.core.services.settings_service import update_oracle_skin

        try:
            skin = validate_chat_skin(body.skin_name)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if not ensure_primary_agent(load_settings_from_db()):
            raise HTTPException(409, 'Create an agent before choosing a primary avatar.')
        update_oracle_skin(skin)
        return JSONResponse({'primary_agent': ensure_primary_agent(load_settings_from_db())})

    @router.websocket("/ws")
    async def chat_websocket(websocket: WebSocket):
        """WebSocket for real-time chat updates. Query param chat_id=N or send {"subscribe": N}; server sends {"type": "chat_updated", "chat_id": int}."""
        origin = websocket.headers.get("origin")
        if origin and not is_allowed_local_origin(origin):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        if not websocket_has_valid_internal_token(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return
        client_ip = websocket.client.host if websocket.client else "unknown"
        if not rate_limiter.allow(
            f"chat_ws_connect:{client_ip}", limit=30, window_seconds=60
        ):
            await websocket.close(code=1013, reason="Rate limit exceeded")
            return
        await websocket.accept()
        subscribed_chat_id: Optional[int] = None
        qs = (websocket.scope.get("query_string") or b"").decode()
        if qs:
            for part in qs.split("&"):
                if "=" in part and part.split("=")[0].strip().lower() == "chat_id":
                    try:
                        subscribed_chat_id = int(part.split("=", 1)[1].strip())
                        if subscribed_chat_id < 1:
                            raise ValueError("chat_id must be positive")
                        with _chat_ws_lock:
                            _chat_ws_connections.setdefault(
                                subscribed_chat_id, set()
                            ).add(websocket)
                    except (ValueError, IndexError):
                        pass
                    break
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send_json(
                            {"type": "pong", "sent_at": msg.get("sent_at")}
                        )
                        continue
                    topic = msg.get("subscribe_topic")
                    if topic in {INCOMING_TOPIC, "chat_list"}:
                        with _chat_ws_lock:
                            _topic_ws_connections.setdefault(topic, set()).add(websocket)
                        await websocket.send_json({"type": "subscribed", "topic": topic})
                        continue
                    cid = msg.get("subscribe")
                    if cid is not None:
                        with _chat_ws_lock:
                            if (
                                subscribed_chat_id is not None
                                and subscribed_chat_id in _chat_ws_connections
                            ):
                                _chat_ws_connections[subscribed_chat_id].discard(
                                    websocket
                                )
                                if not _chat_ws_connections[subscribed_chat_id]:
                                    del _chat_ws_connections[subscribed_chat_id]
                            subscribed_chat_id = int(cid)
                            if subscribed_chat_id < 1:
                                raise ValueError("chat_id must be positive")
                            _chat_ws_connections.setdefault(
                                subscribed_chat_id, set()
                            ).add(websocket)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            with _chat_ws_lock:
                for _topic, _conns in list(_topic_ws_connections.items()):
                    _conns.discard(websocket)
                    if not _conns:
                        _topic_ws_connections.pop(_topic, None)
                if (
                    subscribed_chat_id is not None
                    and subscribed_chat_id in _chat_ws_connections
                ):
                    _chat_ws_connections[subscribed_chat_id].discard(websocket)
                    if not _chat_ws_connections[subscribed_chat_id]:
                        del _chat_ws_connections[subscribed_chat_id]

    @router.post("/internal/notify-chat-updated")
    async def notify_chat_updated(req: Request):
        """Called by the desktop app when a chat is updated (e.g. PTT/voice). Only accepts from localhost. Body: {"chat_id": int}."""
        if req.client and req.client.host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="Only localhost may notify")
        client_ip = req.client.host if req.client else "unknown"
        if not rate_limiter.allow(
            f"notify_chat_updated:{client_ip}", limit=120, window_seconds=60
        ):
            raise HTTPException(status_code=429, detail="Too many requests")
        try:
            body = await req.json()
            chat_id = body.get("chat_id")
            if chat_id is None:
                raise HTTPException(status_code=400, detail="chat_id required")
            chat_id = int(chat_id)
        except ClientDisconnect:
            logger.debug("notify_chat_updated: client disconnected before body read")
            return JSONResponse({"ok": False, "disconnected": True})
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="chat_id must be an integer")
        event: Dict[str, Any] = {"type": "chat_updated", "chat_id": chat_id}
        try:
            event.update(await asyncio.to_thread(_latest_turn_summary, chat_id))
        except Exception:
            logger.debug("notify_chat_updated: latest turn summary unavailable", exc_info=True)
        payload = json.dumps(event)
        with _chat_ws_lock:
            conns = list(_chat_ws_connections.get(chat_id, set()))
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception as e:
                logger.debug("WebSocket send error: %s", e)
        await broadcast_topic_event("chat_list", {"type": "chat_list_changed", "chat_id": chat_id})
        return JSONResponse({"ok": True})

    @router.post("/internal/notify-chat-event")
    async def notify_chat_event(req: Request):
        """Called by the desktop app for real-time events: message_added, stream_started, stream_token, stream_finished. Only localhost. Body: { event, chat_id, ... }."""
        if req.client and req.client.host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="Only localhost may notify")
        client_ip = req.client.host if req.client else "unknown"
        if not rate_limiter.allow(
            f"notify_chat_event:{client_ip}", limit=300, window_seconds=60
        ):
            raise HTTPException(status_code=429, detail="Too many requests")
        try:
            body = await req.json()
            event = body.get("event")
            chat_id = body.get("chat_id")
            if event is None or chat_id is None:
                raise HTTPException(
                    status_code=400, detail="event and chat_id required"
                )
            chat_id = int(chat_id)
        except ClientDisconnect:
            logger.debug("notify_chat_event: client disconnected before body read")
            return JSONResponse({"ok": False, "disconnected": True})
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid body")
        delivered = await broadcast_chat_event(body)
        return JSONResponse({"ok": True, "delivered": delivered})

    @router.get("/chats")
    async def get_chats(include_archived: bool = False, surface: str = "chat"):
        """Get root conversations for exactly one owning surface."""
        try:
            normalized_surface = str(surface or "chat").strip().lower()
            if normalized_surface not in {"chat", "development"}:
                raise HTTPException(status_code=422, detail="surface must be chat or development")
            with get_session() as session:
                # Get root chats (parent_id is None) that are not archived or hidden
                query = session.query(Chat).filter(Chat.parent_id == None, Chat.is_hidden == False)
                if not include_archived:
                    query = query.filter(Chat.is_archived == False)
                chats = query.order_by(Chat.modified_date.desc(), Chat.id.desc()).all()

                settings = load_settings_from_db()
                result = []
                for chat in chats:
                    owned_by_development = is_development_thread(session, chat)
                    if owned_by_development != (normalized_surface == "development"):
                        continue
                    from distr.core.workflow.development_threads import development_thread_record

                    development = development_thread_record(session, chat) if owned_by_development else {}
                    model_route = development.get("model_route") if isinstance(development.get("model_route"), dict) else {}
                    result.append(
                        {
                            "id": chat.id,
                            "title": chat.title or "New Chat",
                            **(sidebar_state(session, chat, settings) if not owned_by_development else {}),
                            "voice_name": _voice_model_to_display_name(chat.voice_provider, chat.voice_model, settings),
                            "created_date": chat.created_date.isoformat()
                            if chat.created_date
                            else None,
                            "modified_date": chat.modified_date.isoformat()
                            if chat.modified_date
                            else None,
                            "model_name": chat.model_name,
                            "provider": chat.provider,
                            "project_id": chat.project_id,
                            "route_mode": chat.route_mode or "auto",
                            "execution_profile": chat.execution_profile or "code",
                            "autonomy_level": chat.autonomy_level or "full",
                            "development": bool(development),
                            "development_workflow_id": development.get("workflow_id"),
                            "development_source_type": development.get("source_type"),
                            "development_ticket_id": development.get("ticket_id"),
                            "development_board_key": development.get("board_key"),
                            "development_board_provider": development.get("board_provider"),
                            "development_board_ticket_key": development.get("board_ticket_key"),
                            "development_board_ticket_title": development.get("board_ticket_title"),
                            "development_board_ticket_lane": development.get("board_ticket_lane"),
                            "development_execution": development.get("execution") or {},
                            "development_time_running": bool(development.get("time_running")),
                            "development_time_seconds": max(0, int(development.get("time_seconds") or 0)),
                            "pinned": bool(development.get("pinned")),
                            "permission_profile": development.get("permission_profile") or {},
                            "remote_continuation": bool(development.get("remote_continuation")),
                            "reasoning_effort": model_route.get("reasoning_effort") or "medium",
                            "service_tier": model_route.get("service_tier") or "standard",
                            "backend": model_route.get("backend") or "",
                            "archived": bool(chat.is_archived),
                        }
                    )
                session.commit()
                last_chat_id = settings.get("last_chat_id")
                if last_chat_id is not None and not isinstance(last_chat_id, int):
                    try:
                        last_chat_id = int(last_chat_id)
                    except (TypeError, ValueError):
                        last_chat_id = None
                agent_current_chat_id = settings.get("agent_current_chat_id")
                if agent_current_chat_id is not None and not isinstance(
                    agent_current_chat_id, int
                ):
                    try:
                        agent_current_chat_id = int(agent_current_chat_id)
                    except (TypeError, ValueError):
                        agent_current_chat_id = None
                return JSONResponse(
                    {
                        "chats": result,
                        "last_chat_id": last_chat_id,
                        "agent_current_chat_id": agent_current_chat_id,
                    }
                )
        except Exception as e:
            logger.error(f"Failed to load chats: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get("/chats/agent-setup")
    async def get_agent_setup():
        """Default agent setup (model + voice) from settings for empty-state callout when no chat is selected."""
        try:
            settings = load_settings_from_db()
            provider = (settings.get("conversational_llm_provider") or "Ollama").strip()
            model_name = (settings.get("conversational_llm_model") or "").strip() or "—"
            voice_provider_raw = (settings.get("tts_provider") or "Kokoro").strip()
            voice_provider_id = normalize_voice_provider(voice_provider_raw)
            _display_map = {d.id: d.name.split(" (")[0] for d in tts_registry.all_providers()}
            voice_provider = _display_map.get(voice_provider_id, voice_provider_id.title())
            from distr.core.chat import resolve_voice_model_from_global_settings

            voice_model_raw = (
                resolve_voice_model_from_global_settings(voice_provider_id, settings) or ""
            ).strip() or "—"
            voice_model = (
                _voice_model_to_display_name(voice_provider, voice_model_raw, settings)
                if voice_model_raw != "—"
                else "—"
            )
            return JSONResponse(
                {
                    "provider": provider,
                    "model_name": model_name,
                    "voice_provider": voice_provider,
                    "voice_model": voice_model,
                }
            )

        except Exception as e:
            logger.debug("Agent setup fallback: %s", e)
            return JSONResponse(
                {
                    "provider": "—",
                    "model_name": "—",
                    "voice_provider": "—",
                    "voice_model": "—",
                }
            )

    @router.get("/chats/qualification-capabilities")
    async def get_chat_qualification_capabilities():
        """Versioned handshake for channel-authentic qualification clients."""
        return JSONResponse({"intake_identity_version": 1})

    @router.get("/chats/usage-status")
    async def get_chat_usage_status(
        voice_provider: str = "",
        llm_provider: str = "",
    ):
        """Provider quota/cost summaries for chat header indicators.

        API keys are read server-side from settings and never returned.
        """
        try:
            settings = load_settings_from_db()
            voice_key = normalize_voice_provider((voice_provider or "").strip())
            llm_key = (llm_provider or "").strip().lower()

            voice_usage: Optional[Dict[str, Any]] = None
            llm_usage: Optional[Dict[str, Any]] = None

            if voice_key == "elevenlabs":
                voice_usage = await asyncio.to_thread(_elevenlabs_usage_status, settings)
            if llm_key == "openai":
                llm_usage = await asyncio.to_thread(_openai_usage_status, settings)

            return JSONResponse(
                {
                    "voice": voice_usage,
                    "llm": llm_usage,
                }
            )
        except Exception as e:
            logger.debug("Usage status failed: %s", e, exc_info=True)
            return JSONResponse({"voice": None, "llm": None}, status_code=200)

    @router.get("/chats/{chat_id}")
    async def get_chat(chat_id: int, surface: str = "chat"):
        """Get a specific chat with all its messages"""
        try:
            normalized_surface = str(surface or "chat").strip().lower()
            if normalized_surface not in {"chat", "development"}:
                raise HTTPException(status_code=422, detail="surface must be chat or development")
            with get_session() as session:
                from distr.core.chat import _thread_root_chat_id

                root_id = _thread_root_chat_id(session, chat_id)
                root_chat = session.get(Chat, root_id)
                if not root_chat:
                    raise HTTPException(status_code=404, detail="Chat not found")
                owned_by_development = is_development_thread(session, root_chat)
                if owned_by_development != (normalized_surface == "development"):
                    detail = (
                        "This thread belongs to Development, not Chat."
                        if owned_by_development
                        else "This thread belongs to Chat, not Development."
                    )
                    raise HTTPException(status_code=409, detail=detail)

                # Get all messages in this conversation thread
                # Using recursive CTE to get all descendants
                from sqlalchemy import text

                thread_query = text("""
                    WITH RECURSIVE chat_thread AS (
                        SELECT id, parent_id, title, input, response, created_date, modified_date, model_name, provider, is_hidden, additional_context
                        FROM chats
                        WHERE id = :root_id
                        UNION ALL
                        SELECT c.id, c.parent_id, c.title, c.input, c.response, c.created_date, c.modified_date, c.model_name, c.provider, c.is_hidden, c.additional_context
                        FROM chats c
                        INNER JOIN chat_thread ct ON c.parent_id = ct.id
                    )
                    SELECT * FROM chat_thread ORDER BY created_date ASC
                """)
                rows = session.execute(thread_query, {"root_id": root_id}).fetchall()

                messages = _merge_thread_rows_with_tool_and_workflow_events(rows, root_chat)
                if owned_by_development:
                    from distr.core.incoming.media import public_thread_attachments

                    media = public_thread_attachments(int(root_chat.id))
                    first_user = next((message for message in messages if message.get('role') == 'user'), None)
                    if media and first_user is not None:
                        first_user['attachments'] = media

                from distr.core.chat_turns import get_turns

                turn_state = get_turns(int(root_chat.id))
                turns_by_row = {
                    int(item.get("turn_id")): item
                    for item in (turn_state.get("turns") or [])
                    if item.get("turn_id") is not None
                }
                for message in messages:
                    if message.get("role") != "assistant" or message.get("chat_row_id") is None:
                        continue
                    turn = turns_by_row.get(int(message["chat_row_id"]))
                    if not turn or not turn.get("started_at"):
                        continue
                    message["turn_started_at"] = turn["started_at"]
                    if turn.get("completed_at"):
                        message["turn_duration_seconds"] = _duration_seconds_between(
                            turn["started_at"], turn["completed_at"]
                        )
                durable_turn_ids = {
                    int(item.get("turn_id"))
                    for item in (turn_state.get("turns") or [])
                    if item.get("turn_id") is not None
                }
                has_durable_workflow = any(
                    (event.get("metadata") or {}).get("source") == "workflow"
                    for item in (turn_state.get("turns") or [])
                    for event in (item.get("events") or [])
                )
                if durable_turn_ids:
                    messages = [
                        message
                        for message in messages
                        if not (
                            message.get("role") == "tool"
                            and _safe_int((message.get("tool_event") or {}).get("turn_chat_id"))
                            in durable_turn_ids
                        )
                        and not (message.get("role") == "workflow" and has_durable_workflow)
                    ]

                from distr.core.workflow.development_threads import development_thread_record

                development = development_thread_record(session, root_chat) if owned_by_development else {}
                if development:
                    _restore_development_execution_response(messages, development)

                settings = load_settings_from_db()
                # Use chat row first; fall back to settings so UI always has a value
                provider = (
                    (root_chat.provider or "").strip()
                    or settings.get("conversational_llm_provider")
                    or settings.get("agent_provider")
                    or "Ollama"
                )
                model_name = (
                    (root_chat.model_name or "").strip()
                    or settings.get("conversational_llm_model")
                    or settings.get("agent_model")
                    or ""
                )
                voice_provider = (
                    (root_chat.voice_provider or "").strip()
                    or settings.get("tts_provider")
                    or "kokoro"
                )
                # Use the voice model that matches the voice provider (not a random fallback chain)
                vp_id = normalize_voice_provider(voice_provider)
                if vp_id in tts_registry:
                    desc = tts_registry.get(vp_id)
                    voice_model_raw = (root_chat.voice_model or "").strip() or (settings.get(desc.settings_key) or desc.default_voice)
                else:
                    voice_model_raw = (root_chat.voice_model or "").strip() or ""
                # Persist to chat row when we used fallback so this thread has its own LLM/voice stored (normal chat behaviour)
                if not (
                    root_chat.provider
                    and root_chat.model_name
                    and root_chat.voice_provider
                    and root_chat.voice_model
                ):
                    if not (root_chat.provider or "").strip():
                        root_chat.provider = provider
                    if not (root_chat.model_name or "").strip():
                        root_chat.model_name = model_name
                    if not (root_chat.voice_provider or "").strip():
                        root_chat.voice_provider = voice_provider
                    if not (root_chat.voice_model or "").strip():
                        root_chat.voice_model = voice_model_raw
                    root_chat.modified_date = datetime.utcnow()
                    session.commit()
                voice_model_display = _voice_model_to_display_name(
                    voice_provider, voice_model_raw, settings
                )
                header_payload = _chat_header_stats_payload(
                    root_chat=root_chat,
                    messages=messages,
                    provider=provider,
                    model_name=model_name,
                )
                sidebar = sidebar_state(session, root_chat, settings)
                session.commit()
                additional_context = _chat_additional_context(root_chat.additional_context)
                compact_checkpoint = additional_context.get("compact_checkpoint")
                model_route = development.get("model_route") if isinstance(development.get("model_route"), dict) else {}
                return JSONResponse(
                    {
                        "id": root_chat.id,
                        "title": header_payload["title"],
                        **sidebar,
                        "messages": messages,
                        "provider": provider,
                        "model_name": model_name,
                        "project_id": root_chat.project_id,
                        "route_mode": root_chat.route_mode or "auto",
                        "execution_profile": root_chat.execution_profile or "code",
                        "autonomy_level": root_chat.autonomy_level or "full",
                        "development": development,
                        "development_board_key": development.get("board_key"),
                        "development_board_provider": development.get("board_provider"),
                        "development_board_ticket_key": development.get("board_ticket_key"),
                        "development_board_ticket_title": development.get("board_ticket_title"),
                        "development_board_ticket_lane": development.get("board_ticket_lane"),
                        "pinned": bool(development.get("pinned")),
                        "permission_profile": development.get("permission_profile") or {},
                        "remote_continuation": bool(development.get("remote_continuation")),
                        "reasoning_effort": model_route.get("reasoning_effort") or "medium",
                        "service_tier": model_route.get("service_tier") or "standard",
                        "backend": model_route.get("backend") or "",
                        "archived": bool(root_chat.is_archived),
                        "voice_provider": voice_provider,
                        # Keep runtime/raw voice ID for agent swapping, and provide a
                        # display label separately for the UI.
                        "voice_model": voice_model_raw,
                        "voice_model_display": voice_model_display,
                        "context_stats": header_payload["context_stats"],
                        "title_auto": header_payload["title_auto"],
                        "compact_checkpoint": compact_checkpoint,
                        "active_turn": turn_state["active_turn"],
                        "turns": turn_state["turns"],
                    }
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to load chat: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chats")
    async def create_chat(request_data: CreateChatRequest):
        """Create a new chat via central chat_service; interrupt TTS, reload agent, optionally send starting question to LLM.
        Prefer provider/model/voice_provider/voice_model from request (web UI selection) over system settings."""
        try:
            if request_data.agent_name is not None:
                from distr.core.chat_agents import validate_agent_profile

                try:
                    validate_agent_profile(request_data.agent_name, request_data.agent_role)
                except ValueError as exc:
                    raise HTTPException(422, str(exc))
            settings = load_settings_from_db()
            try:
                skin_name = validate_chat_skin(request_data.skin_name or settings.get("selected_oracle") or "oracle")
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            # Get provider with fallback priority: request -> conversational settings -> agent settings -> default
            raw_provider = (
                (request_data.provider or "").strip()
                or settings.get("conversational_llm_provider", None)
                or settings.get("agent_provider", None)
                or "ollama"
            )
            # Get model with fallback priority: request -> conversational settings -> agent settings -> empty
            model_name = (
                (request_data.model_name or "").strip()
                or settings.get("conversational_llm_model", None)
                or settings.get("agent_model", None)
                or ""
            )
            # Get voice provider with fallback priority: request -> tts settings -> voice settings -> default
            voice_provider = (
                (request_data.voice_provider or "").strip()
                or settings.get("tts_provider", None)
                or settings.get("voice_provider", None)
                or "kokoro"
            )

            # Normalize provider for validation
            from distr.core.chat import _normalize_provider, valid_llm_providers
            from distr.core.openai_s2s import apply_s2s_voice_defaults, is_openai_s2s_model

            provider = _normalize_provider(raw_provider)

            route_mode = (request_data.route_mode or "auto").strip().lower()
            if route_mode not in {"auto", "manual"}:
                raise HTTPException(status_code=422, detail="route_mode must be auto or manual")
            if request_data.project_id is not None:
                from distr.core.db.projects import Project

                with get_session() as session:
                    if session.get(Project, int(request_data.project_id)) is None:
                        raise HTTPException(status_code=422, detail="Selected project does not exist")

            # S2S: force OpenAI provider + Realtime voice on the chat row
            s2s_prov, voice_provider, _s2s_voice = apply_s2s_voice_defaults(
                model_name=model_name,
                voice_provider=voice_provider,
                voice_model=(request_data.voice_model or "").strip() or None,
            )
            if s2s_prov:
                provider = s2s_prov
                raw_provider = "openai"

            # Validate provider
            valid_providers = valid_llm_providers()
            if provider not in valid_providers:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid provider: {provider}. Must be one of {valid_providers}",
                )

            # Normalize voice provider to lowercase before validation
            if voice_provider:
                voice_provider = normalize_voice_provider(voice_provider)

            # Validate voice provider
            valid_voice_providers = [d.id for d in tts_registry.all_providers()] + [""]
            if voice_provider and voice_provider not in valid_voice_providers:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid voice provider: {voice_provider}. Must be one of {valid_voice_providers}",
                )

            # Resolve voice model for the chosen provider only.
            # This avoids cross-provider fallbacks (e.g. using a Kokoro voice when
            # the selected provider is OpenAI/ElevenLabs).
            if is_openai_s2s_model(model_name):
                voice_model = _s2s_voice
            elif voice_provider and voice_provider in tts_registry:
                desc = tts_registry.get(voice_provider)
                voice_model = (request_data.voice_model or "").strip() or (
                    settings.get(desc.settings_key) or desc.default_voice
                )
            else:
                voice_model = (request_data.voice_model or "").strip()

            title = (request_data.title or "").strip() or None
            starting_question = (request_data.starting_question or "").strip() or None
            speak = request_data.speak if request_data.speak is not None else True

            chat_id, first_message = ChatService.create_new_chat(
                skin_name=skin_name,
                llm_provider=raw_provider,
                llm_model=model_name,
                tts_provider=voice_provider,
                tts_voice=voice_model,
                title=title,
                starting_question=starting_question,
                project_id=request_data.project_id,
                route_mode=route_mode,
                execution_profile=request_data.execution_profile,
                autonomy_level=request_data.autonomy_level,
                agent_name=request_data.agent_name,
                agent_role=request_data.agent_role,
                activate=request_data.agent_name is None,
            )

            with get_session() as session:
                chat = session.get(Chat, chat_id)
                if not chat:
                    raise HTTPException(
                        status_code=500, detail="Chat created but not found"
                    )
                chat_dict = {
                    "id": chat.id,
                    **agent_identity(session, chat.id),
                    "title": chat.title or "New Chat",
                    "skin_name": chat_skin(chat),
                    "provider": chat.provider,
                    "model_name": chat.model_name,
                    "voice_provider": chat.voice_provider,
                    "voice_model": chat.voice_model,
                    "project_id": chat.project_id,
                    "route_mode": chat.route_mode or "auto",
                    "execution_profile": chat.execution_profile or "code",
                    "autonomy_level": chat.autonomy_level or "full",
                }

            # ── Persist chat selections to global settings so the next "new chat" ──
            # defaults to these choices. Persist resolved values (not only explicit
            # request fields) so provider/model and voice provider/voice stay in sync.
            try:
                from distr.core.settings import save_settings_to_db as _save_settings
                _settings = load_settings_from_db()
                _changed = False

                # LLM provider & model — never write Realtime S2S ids into globals
                from distr.core.openai_s2s import is_openai_s2s_model, strip_realtime_from_settings_models
                if not is_openai_s2s_model(model_name):
                    _settings["conversational_llm_provider"] = provider  # normalized
                    _settings["llm_provider"] = provider
                    _settings["agent_provider"] = provider
                    _changed = True
                    if model_name:
                        _settings["conversational_llm_model"] = model_name
                        _settings["llm_model"] = model_name
                        _settings["agent_model"] = model_name
                        _changed = True
                else:
                    strip_realtime_from_settings_models(_settings)
                    logger.info(
                        "Create chat: S2S model %s kept chat-scoped; globals Completions untouched",
                        model_name,
                    )

                # TTS globals: skip when chat is S2S (Realtime voice is chat-scoped)
                if voice_provider and voice_model and not is_openai_s2s_model(model_name):
                    from distr.core.services.settings_service import (
                        apply_voice_selection_to_settings,
                    )

                    if apply_voice_selection_to_settings(
                        _settings, voice_provider, voice_model
                    ):
                        _changed = True

                if _changed and request_data.agent_name is None:
                    _save_settings(_settings)
                    logger.info(
                        "Create chat: persisted selections to global settings (provider=%s, model=%s, voice_provider=%s, voice_model=%s)",
                        provider, model_name, voice_provider, voice_model,
                    )
            except Exception as e:
                logger.warning("Create chat: could not persist selections to settings: %s", e)

            try:
                from distr.core.signals import signal_manager

                if request_data.agent_name is not None:
                    # Creating an identity must not replace the primary runtime.
                    pass
                elif first_message:
                    # web_create_chat_emits_requested handler in app.py sends commands
                    # in guaranteed order: interrupt_tts -> hot_swap_llm -> process_text_input.
                    # Do NOT also emit model_hot_reload or current_chat_changed here - the latter would be sent to the agent and cancel in-flight generation.
                    signal_manager.web_create_chat_emits_requested.emit(
                        chat_id,
                        first_message,
                        speak,
                        chat_dict.get("provider"),
                        chat_dict.get("model_name"),
                        chat_dict.get("voice_provider"),
                        chat_dict.get("voice_model"),
                    )
                    logger.info(
                        "Create chat: emitted web_create_chat_emits_requested for chat_id=%s",
                        chat_id,
                    )
                else:
                    # No starting question: hot-swap agent to this chat without full process restart
                    signal_manager.web_load_chat_in_agent_requested.emit(chat_id)
                    logger.info(
                        "Create chat: emitted web_load_chat_in_agent_requested for chat_id=%s (agent will hot-swap to chat's provider/model)",
                        chat_id,
                    )
            except Exception as e:
                logger.warning(
                    "Create chat: could not emit to agent (agent may not be running): %s",
                    e,
                )

            return JSONResponse(
                {
                    **chat_dict,
                    "starting_question": first_message,
                    "message": "Chat created successfully",
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to create chat: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.patch("/chats/{chat_id}")
    async def update_chat(chat_id: int, request_data: UpdateChatRequest):
        """Update a chat. Persist title and thread settings (provider, model_name, voice)
        so the chat row is the source of truth. Also update global settings defaults so the
        next new chat pre-selects these choices."""
        try:
            response_payload: Dict[str, Any]
            previous_settings: Dict[str, Optional[str]] = {}
            settings_fields_touched = False
            with get_session() as session:
                chat = session.query(Chat).filter(Chat.id == chat_id).first()
                if not chat:
                    raise HTTPException(status_code=404, detail="Chat not found")
                _reject_development_lifecycle(session, chat)
                previous_settings = {
                    "provider": chat.provider,
                    "model_name": chat.model_name,
                    "voice_provider": chat.voice_provider,
                    "voice_model": chat.voice_model,
                }
                updated = False
                if request_data.skin_name is not None:
                    try:
                        skin_name = validate_chat_skin(request_data.skin_name)
                    except ValueError as exc:
                        raise HTTPException(422, str(exc))
                    set_chat_skin(chat, skin_name)
                    updated = True
                    settings_fields_touched = True
                if request_data.title is not None:
                    chat.title = (request_data.title or "").strip() or None
                    context = _chat_additional_context(chat.additional_context)
                    title_auto = _title_auto_meta(context)
                    title_auto["manual"] = True
                    title_auto["updated_at"] = datetime.utcnow().isoformat()
                    context["title_auto"] = title_auto
                    chat.additional_context = json.dumps(
                        context, ensure_ascii=False, default=str
                    )
                    updated = True
                if request_data.provider is not None:
                    chat.provider = (request_data.provider or "").strip() or None
                    updated = True
                    settings_fields_touched = True
                if request_data.model_name is not None:
                    chat.model_name = (request_data.model_name or "").strip() or None
                    updated = True
                    settings_fields_touched = True
                if request_data.voice_provider is not None:
                    chat.voice_provider = (
                        request_data.voice_provider or ""
                    ).strip() or None
                    updated = True
                    settings_fields_touched = True
                if request_data.voice_model is not None:
                    chat.voice_model = (request_data.voice_model or "").strip() or None
                    updated = True
                    settings_fields_touched = True
                if "project_id" in request_data.model_fields_set:
                    chat.project_id = request_data.project_id
                    updated = True
                if request_data.route_mode is not None:
                    route_mode = request_data.route_mode.strip().lower()
                    if route_mode not in {"auto", "manual"}:
                        raise HTTPException(status_code=422, detail="route_mode must be auto or manual")
                    chat.route_mode = route_mode
                    updated = True
                if request_data.execution_profile is not None:
                    chat.execution_profile = request_data.execution_profile.strip().lower() or "code"
                    updated = True
                if request_data.autonomy_level is not None:
                    chat.autonomy_level = request_data.autonomy_level.strip().lower() or "full"
                    updated = True

                # S2S: coerce provider + Realtime voice on the chat row
                from distr.core.openai_s2s import apply_s2s_voice_defaults, is_openai_s2s_model
                if is_openai_s2s_model(chat.model_name):
                    s2s_prov, vp, vm = apply_s2s_voice_defaults(
                        model_name=chat.model_name,
                        voice_provider=chat.voice_provider,
                        voice_model=chat.voice_model,
                    )
                    if s2s_prov:
                        chat.provider = s2s_prov
                        chat.voice_provider = vp
                        chat.voice_model = vm
                        updated = True
                        settings_fields_touched = True

                if updated:
                    chat.modified_date = datetime.utcnow()
                    session.commit()

                # Read ORM fields before the session closes (avoids DetachedInstanceError).
                response_payload = {
                    "id": int(chat.id),
                    "title": chat.title or "New Chat",
                    "skin_name": chat_skin(chat),
                    "provider": chat.provider,
                    "model_name": chat.model_name,
                    "voice_provider": chat.voice_provider,
                    "voice_model": chat.voice_model,
                    "project_id": chat.project_id,
                    "route_mode": chat.route_mode or "auto",
                    "execution_profile": chat.execution_profile or "code",
                    "autonomy_level": chat.autonomy_level or "full",
                }

            # ── Persist to global settings so next new chat defaults to these choices ──
            try:
                _sett = load_settings_from_db()
                _changed = False
                if request_data.provider and request_data.provider.strip():
                    from distr.core.chat import _normalize_provider, valid_llm_providers
                    from distr.core.openai_s2s import is_openai_s2s_model
                    # Provider globals only when not switching into S2S via model below
                    if not (
                        request_data.model_name
                        and is_openai_s2s_model(request_data.model_name.strip())
                    ):
                        _sett["conversational_llm_provider"] = _normalize_provider(request_data.provider)
                        _sett["llm_provider"] = _normalize_provider(request_data.provider)
                        _sett["agent_provider"] = _normalize_provider(request_data.provider)
                        _changed = True
                if request_data.model_name and request_data.model_name.strip():
                    from distr.core.openai_s2s import is_openai_s2s_model, strip_realtime_from_settings_models
                    _m = request_data.model_name.strip()
                    if is_openai_s2s_model(_m):
                        strip_realtime_from_settings_models(_sett)
                    else:
                        _sett["conversational_llm_model"] = _m
                        _sett["llm_model"] = _m
                        _sett["agent_model"] = _m
                        _changed = True

                # Voice globals: skip when chat is S2S (Realtime voice is chat-scoped)
                voice_touched = (
                    request_data.voice_provider is not None
                    or request_data.voice_model is not None
                )
                _chat_model = (response_payload.get("model_name") or "").strip()
                from distr.core.openai_s2s import is_openai_s2s_model as _is_s2s
                if voice_touched and not _is_s2s(_chat_model):
                    from distr.core.services.settings_service import (
                        apply_voice_selection_to_settings,
                    )

                    vp_eff = (response_payload.get("voice_provider") or "").strip()
                    vm_eff = (response_payload.get("voice_model") or "").strip()
                    if vp_eff and vm_eff:
                        if apply_voice_selection_to_settings(_sett, vp_eff, vm_eff):
                            _changed = True

                if _changed:
                    from distr.core.settings import save_settings_to_db as _save_settings
                    _save_settings(_sett)
                    logger.info(
                        "Update chat %s: persisted selections to global settings", chat_id
                    )
            except Exception as e:
                logger.warning("Update chat: could not persist selections to settings: %s", e)

            if settings_fields_touched and updated:
                from distr.core.agent.tool_audit import record_chat_settings_change

                current_settings = {
                    "provider": response_payload.get("provider"),
                    "model_name": response_payload.get("model_name"),
                    "voice_provider": response_payload.get("voice_provider"),
                    "voice_model": response_payload.get("voice_model"),
                }
                record_chat_settings_change(
                    chat_id,
                    previous=previous_settings,
                    current=current_settings,
                )

            return JSONResponse({**response_payload, "message": "Chat updated"})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update chat: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get("/chats/{chat_id}/header-stats")
    async def get_chat_header_stats(chat_id: int):
        """Lightweight header payload: title + context pressure (for live UI updates)."""
        try:
            with get_session() as session:
                root_chat = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root_chat:
                    raise HTTPException(status_code=404, detail="Chat not found")
                _reject_development_lifecycle(session, root_chat)
                rows = _thread_rows_for_compaction(session, chat_id)
                messages = _messages_from_rows(rows)
                settings = load_settings_from_db()
                provider = (
                    (root_chat.provider or "").strip()
                    or settings.get("conversational_llm_provider")
                    or settings.get("agent_provider")
                    or "Ollama"
                )
                model_name = (
                    (root_chat.model_name or "").strip()
                    or settings.get("conversational_llm_model")
                    or settings.get("agent_model")
                    or ""
                )
                payload = _chat_header_stats_payload(
                    root_chat=root_chat,
                    messages=messages,
                    provider=provider,
                    model_name=model_name,
                )
                return JSONResponse({"id": chat_id, **payload})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Header stats failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chats/{chat_id}/refresh-title")
    async def refresh_chat_title(chat_id: int, force: bool = False):
        """Suggest and persist a short title from recent conversation intent."""
        try:
            settings = load_settings_from_db()
            with get_session() as session:
                root_chat = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root_chat:
                    raise HTTPException(status_code=404, detail="Chat not found")
                _reject_development_lifecycle(session, root_chat)
                rows = _thread_rows_for_compaction(session, chat_id)
                messages = _messages_from_rows(sorted(rows, key=lambda row: row.id))
                provider = (
                    (root_chat.provider or "").strip()
                    or settings.get("conversational_llm_provider")
                    or "Ollama"
                )
                model_name = (
                    (root_chat.model_name or "").strip()
                    or settings.get("conversational_llm_model")
                    or settings.get("agent_model")
                    or ""
                )
                new_title = _maybe_refresh_chat_title(
                    session,
                    root_chat,
                    messages,
                    settings=settings,
                    force=bool(force),
                )
                payload = _chat_header_stats_payload(
                    root_chat=root_chat,
                    messages=messages,
                    provider=provider,
                    model_name=model_name,
                )
                return JSONResponse(
                    {
                        "id": chat_id,
                        "updated": bool(new_title),
                        "title": payload["title"],
                        "context_stats": payload["context_stats"],
                        "compact_checkpoint": payload["compact_checkpoint"],
                        "title_auto": payload["title_auto"],
                    }
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Refresh chat title failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chats/{chat_id}/compact")
    async def compact_chat(chat_id: int, request_data: CompactChatRequest):
        """Create a visible, orchestrator-backed compact checkpoint for this chat."""
        try:
            settings = load_settings_from_db()
            with get_session() as session:
                root = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root:
                    raise HTTPException(status_code=404, detail="Chat not found")
                _reject_development_lifecycle(session, root)
                rows = _thread_rows_for_compaction(session, chat_id)
                messages = _messages_from_rows(rows)
                provider = (
                    (request_data.provider or "").strip()
                    or (root.provider or "").strip()
                    or settings.get("conversational_llm_provider")
                    or "Ollama"
                )
                model_name = (
                    (request_data.model_name or "").strip()
                    or (root.model_name or "").strip()
                    or settings.get("conversational_llm_model")
                    or ""
                )
                summary, summary_source = _summarize_chat_with_model(
                    messages,
                    provider=provider,
                    model_name=model_name,
                    settings=settings,
                )
                stats = _context_stats(messages, provider, model_name)
                compact_reason = (request_data.reason or "manual").strip() or "manual"
                notice = (
                    "Context compacted. The agent will use this checkpoint for older "
                    "conversation state:\n\n"
                    + summary
                )
                checkpoint_row = Chat(
                    parent_id=root.id,
                    title=None,
                    input=None,
                    response=notice,
                    provider=root.provider,
                    model_name=root.model_name,
                    voice_provider=root.voice_provider,
                    voice_model=root.voice_model,
                    additional_context=json.dumps(
                        {
                            "chat_marker": {
                                "type": "compact",
                                "reason": compact_reason,
                                "hide_in_source_on_fork": compact_reason == "fork",
                            }
                        },
                        ensure_ascii=False,
                    ),
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow(),
                )
                session.add(checkpoint_row)
                session.flush()
                checkpoint = {
                    "active": True,
                    "summary": summary,
                    "summary_source": summary_source,
                    "provider": provider,
                    "model_name": model_name,
                    "chat_row_id": int(checkpoint_row.id),
                    "message_count": stats["message_count"],
                    "estimated_tokens": stats["estimated_tokens"],
                    "context_window": stats["context_window"],
                    "percent_used_before": stats["percent_used"],
                    "created_at": datetime.utcnow().isoformat(),
                    "reason": compact_reason,
                }
                context = _chat_additional_context(root.additional_context)
                context["compact_checkpoint"] = checkpoint
                root.additional_context = json.dumps(context, ensure_ascii=False, default=str)
                root.modified_date = datetime.utcnow()
                checkpoint_row_id = int(checkpoint_row.id)
                refreshed_messages = _messages_from_rows(sorted(rows, key=lambda row: row.id)) + [
                    {"role": "assistant", "content": notice, "chat_row_id": checkpoint_row_id}
                ]
                _maybe_refresh_chat_title(
                    session,
                    root,
                    refreshed_messages,
                    settings=settings,
                    force=True,
                )
                session.commit()

            record_chat_audit_event(
                chat_id=int(chat_id),
                chat_row_id=checkpoint_row_id,
                role="assistant",
                content=notice,
            )
            try:
                from distr.core.orchestrator import emit_event

                emit_event(
                    source="chat",
                    event_type="chat_context_compacted",
                    status="checkpoint_created",
                    summary=f"Chat #{chat_id} compacted into checkpoint row #{checkpoint_row_id}.",
                    payload={"thread_id": str(chat_id), "chat_id": chat_id, **checkpoint},
                    evidence={"summary": summary},
                )
            except Exception:
                logger.debug("Orchestrator compact event failed", exc_info=True)
            try:
                from distr.core.signals import signal_manager

                signal_manager.web_load_chat_in_agent_requested.emit(chat_id)
            except Exception:
                logger.debug("Compact hot-swap signal failed", exc_info=True)
            return JSONResponse({"ok": True, "checkpoint": checkpoint})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Compact chat failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chats/{chat_id}/fork")
    async def fork_chat(chat_id: int, request_data: ForkChatRequest):
        """Compact current chat, then create a new chat with the same settings and checkpoint context."""
        try:
            compact_result = await compact_chat(
                chat_id,
                CompactChatRequest(
                    provider=request_data.provider,
                    model_name=request_data.model_name,
                    reason="fork",
                ),
            )
            compact_payload = json.loads(compact_result.body.decode("utf-8"))
            checkpoint = compact_payload.get("checkpoint") or {}
            with get_session() as session:
                source = session.query(Chat).filter(Chat.id == chat_id).first()
                if not source:
                    raise HTTPException(status_code=404, detail="Chat not found")
                source_title = (source.title or "").strip() or f"Chat #{chat_id}"
                title = (request_data.title or "").strip() or f"{source_title} fork"
                fork = Chat(
                    parent_id=None,
                    title=title,
                    input=None,
                    response=None,
                    provider=source.provider,
                    model_name=source.model_name,
                    voice_provider=source.voice_provider,
                    voice_model=source.voice_model,
                    project_id=source.project_id,
                    route_mode=source.route_mode,
                    execution_profile=source.execution_profile,
                    autonomy_level=source.autonomy_level,
                    params=source.params,
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow(),
                )
                session.add(fork)
                session.flush()
                seed = Chat(
                    parent_id=fork.id,
                    title=None,
                    input=None,
                    response=None,
                    provider=fork.provider,
                    model_name=fork.model_name,
                    voice_provider=fork.voice_provider,
                    voice_model=fork.voice_model,
                    additional_context=json.dumps(
                        {
                            "chat_marker": {
                                "type": "fork",
                                "source_chat_id": int(chat_id),
                                "source_title": source_title,
                                "compacted": True,
                            }
                        },
                        ensure_ascii=False,
                    ),
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow(),
                )
                session.add(seed)
                session.flush()
                fork_id = int(fork.id)
                seed_id = int(seed.id)
                fork.additional_context = json.dumps(
                    {
                        "compact_checkpoint": {
                            **checkpoint,
                            "source_chat_id": int(chat_id),
                            "fork_seed": True,
                            "chat_row_id": seed_id,
                        }
                    },
                    ensure_ascii=False,
                    default=str,
                )
                try:
                    fork_params = json.loads(fork.params or "{}")
                except (TypeError, json.JSONDecodeError):
                    fork_params = {}
                if isinstance(fork_params.get("development"), dict):
                    fork_params["development"] = {
                        **fork_params["development"],
                        "forked_from_chat_id": int(chat_id),
                        "pinned": False,
                    }
                    fork.params = json.dumps(fork_params, ensure_ascii=False, default=str)
                session.commit()

            record_chat_audit_event(
                chat_id=fork_id,
                chat_row_id=seed_id,
                role="assistant",
                content=f"Fork started from compact checkpoint for chat #{chat_id}.",
            )
            try:
                from distr.core.orchestrator import emit_event

                emit_event(
                    source="chat",
                    event_type="chat_fork_created",
                    status="created",
                    summary=f"Chat #{chat_id} forked into chat #{fork_id}.",
                    payload={
                        "source_chat_id": chat_id,
                        "fork_chat_id": fork_id,
                        "checkpoint": checkpoint,
                    },
                )
            except Exception:
                logger.debug("Orchestrator fork event failed", exc_info=True)
            try:
                from distr.core.settings import load_settings_from_db, save_settings_to_db

                current = load_settings_from_db()
                current["last_chat_id"] = fork_id
                current["agent_current_chat_id"] = fork_id
                save_settings_to_db(current)
                from distr.core.signals import signal_manager

                signal_manager.web_load_chat_in_agent_requested.emit(fork_id)
            except Exception:
                logger.debug("Fork hot-swap signal failed", exc_info=True)
            return JSONResponse({"ok": True, "id": fork_id, "checkpoint": checkpoint})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Fork chat failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.delete("/chats/{chat_id}")
    async def delete_chat(chat_id: int):
        """Delete a chat and every thread-owned Development relationship."""
        try:
            with get_session() as session:
                chat = session.query(Chat).filter(Chat.id == chat_id).first()
                if not chat:
                    raise HTTPException(status_code=404, detail="Chat not found")
                from distr.core.chat_agents import primary_agent

                primary = primary_agent(session)
                if primary and primary.main_chat_id == chat_id:
                    raise HTTPException(409, "The primary agent's main conversation cannot be deleted.")
                _reject_development_lifecycle(session, chat)
                from distr.core.chat import cleanup_chat_dependencies

                cleanup_chat_dependencies(
                    session,
                    chat_id,
                    delete_owned_ticket=True,
                    delete_owned_workflows=True,
                )
                session.delete(chat)
                session.commit()
                from distr.core.chat import remove_chat_transcript_audit_events

                remove_chat_transcript_audit_events(chat_id)
                return JSONResponse({"message": "Chat deleted successfully"})
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete chat: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chats/{chat_id}/read")
    async def mark_chat_read(chat_id: int, request_data: ReadChatRequest):
        with get_session() as session:
            root = session.get(Chat, chat_id)
            if not root:
                raise HTTPException(404, "Chat not found")
            _reject_development_lifecycle(session, root)
            result = sidebar_state(session, root, load_settings_from_db(), mark_read=request_data.reply_id)
            session.commit()
        return JSONResponse(result)

    @router.post("/chats/{chat_id}/load-in-agent")
    async def load_chat_in_agent(chat_id: int):
        """Load this chat into the agent (set as current). Persist last_chat_id so the agent picks up this chat's voice on reload."""
        try:
            with get_session() as session:
                root = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root:
                    raise HTTPException(status_code=404, detail="Chat not found")
                if is_development_thread(session, root):
                    raise HTTPException(
                        status_code=409,
                        detail="Development tasks cannot be loaded into the Chat agent.",
                    )
            # Persist last_chat_id and agent_current_chat_id so: (1) agent picks up this chat's voice on reload,
            # (2) web sidebar can show which chat is "In agent" (loadChats returns agent_current_chat_id).
            try:
                from distr.core.settings import (
                    load_settings_from_db,
                    save_settings_to_db,
                )

                settings = load_settings_from_db()
                settings["last_chat_id"] = chat_id
                settings["agent_current_chat_id"] = chat_id
                save_settings_to_db(settings)
                logger.info(
                    "Load-in-agent: persisted last_chat_id and agent_current_chat_id=%s",
                    chat_id,
                )
            except Exception as e:
                logger.warning(
                    "Load-in-agent: could not persist last_chat_id/agent_current_chat_id: %s",
                    e,
                )
            try:
                from distr.core.signals import signal_manager

                # Hot-swap agent to this chat without triggering reload_agent_session
                signal_manager.web_load_chat_in_agent_requested.emit(chat_id)
                logger.info(
                    "Load-in-agent: emitted web_load_chat_in_agent_requested for chat_id=%s (agent will hot-swap to chat's provider/model)",
                    chat_id,
                )
            except Exception as e:
                logger.warning("Failed to load chat in agent: %s", e, exc_info=True)
                raise HTTPException(status_code=500, detail="Agent not available")
            return JSONResponse({"loaded": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Load-in-agent failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chat-images")
    async def upload_chat_image(file: UploadFile = File(...)):
        """Store one Oracle chat image (jpeg/png/gif/webp, <=8 MB, magic-byte checked)."""
        from distr.core.chat_images import MAX_CHAT_IMAGE_BYTES, ChatImageError, REJECT_MESSAGE, store_chat_image

        data = await file.read(MAX_CHAT_IMAGE_BYTES + 1)
        if len(data) > MAX_CHAT_IMAGE_BYTES:
            return JSONResponse({"detail": REJECT_MESSAGE}, status_code=413)
        try:
            stored = store_chat_image(data)
        except ChatImageError as e:
            return JSONResponse({"detail": str(e)}, status_code=415)
        stored["original_name"] = Path(str(file.filename or "image")).name[:255]
        return JSONResponse(stored)

    @router.get("/chat-images/{name}")
    async def get_chat_image(name: str):
        from distr.core.chat_images import resolve_chat_image

        path = resolve_chat_image(name)
        if not path:
            raise HTTPException(status_code=404, detail="Image not found")
        return FileResponse(str(path))

    @router.post("/chats/{chat_id}/send-to-agent")
    async def send_to_agent(chat_id: int, request_data: SendMessageRequest, wait: bool = False, timeout: Optional[float] = None):
        """Send the message to the agent. Persist the user message to the DB immediately so it survives refresh; then emit to agent for LLM + TTS.
        Always use this chat's provider/model from DB when not provided in request."""
        try:
            with get_session() as session:
                root = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root:
                    raise HTTPException(status_code=404, detail="Chat not found")
                if is_development_thread(session, root):
                    raise HTTPException(
                        status_code=409,
                        detail="Development tasks cannot receive Chat agent messages.",
                    )
                provider = (
                    getattr(request_data, "provider", None) or ""
                ).strip() or None
                model_name = (
                    getattr(request_data, "model_name", None) or ""
                ).strip() or None
                if not provider or not model_name:
                    provider = provider or (
                        root.provider if root.provider else "Ollama"
                    )
                    model_name = model_name or (root.model_name or "")
                # Persist displayed voice to chat so agent persona matches (e.g. "I'm Adam" when voice is Adam).
                voice_provider = (
                    getattr(request_data, "voice_provider", None) or ""
                ).strip() or None
                voice_model = (
                    getattr(request_data, "voice_model", None) or ""
                ).strip() or None
                if voice_provider is not None or voice_model is not None:
                    if voice_provider is not None:
                        root.voice_provider = voice_provider
                    if voice_model is not None:
                        root.voice_model = voice_model
                    session.commit()
                    logger.info(
                        "Send-to-agent: updated chat voice to provider=%s model=%s",
                        root.voice_provider,
                        root.voice_model,
                    )
            display_message = (getattr(request_data, "message", None) or "").strip()
            if not display_message:
                raise HTTPException(status_code=400, detail="Message is required")
            agent_message = (
                getattr(request_data, "agent_message", None) or ""
            ).strip() or display_message
            uploaded_image = None
            if request_data.image_path:
                from distr.core.chat_images import REJECT_MESSAGE, resolve_chat_image

                uploaded_image = resolve_chat_image(request_data.image_path)
                if not uploaded_image:
                    raise HTTPException(status_code=400, detail=REJECT_MESSAGE)
            speak_val = getattr(request_data, "speak", None)
            # Default True when omitted so TTS plays; only disable when explicitly false
            speak = False if speak_val in (False, "false", "False", 0, "0") else True

            # Validate provider if provided
            if provider is not None and provider.strip():
                from distr.core.chat import _normalize_provider, valid_llm_providers

                norm_provider = _normalize_provider(provider)
                valid_providers = valid_llm_providers()
                if norm_provider not in valid_providers:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid provider: {provider}. Must be one of {valid_providers}",
                    )

            # Validate voice provider if provided
            valid_voice_providers = [d.id for d in tts_registry.all_providers()] + ["", None]
            vp_normalized = normalize_voice_provider(voice_provider) if voice_provider else voice_provider
            if (
                vp_normalized is not None
                and vp_normalized.strip()
                and vp_normalized not in valid_voice_providers
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid voice provider: {voice_provider}. Must be one of {valid_voice_providers}",
                )
            from distr.gui.web.routes.api_extensions import max_thread_row_id, turn_response_fields

            after_row_id = await asyncio.to_thread(max_thread_row_id, chat_id)
            # NOTE: Do NOT load_settings_from_db/save_settings_to_db here.
            # The agent command handler already sets agent_current_chat_id when
            # processing the hot-swap.  Doing a full settings round-trip on every
            # message adds ~50-100ms of unnecessary SQLite I/O.
            logger.info(
                "Send-to-agent: chat_id=%s, speak=%s, provider=%s, model=%s, display_len=%s, agent_len=%s",
                chat_id,
                speak,
                provider,
                model_name,
                len(display_message),
                len(agent_message),
            )
            try:
                from distr.core.kanban.ticket_orchestrator_engagement import (
                    send_ticket_engagement_to_agent,
                )

                if agent_message != display_message:
                    send_ticket_engagement_to_agent(
                        chat_id,
                        display_message,
                        agent_message,
                        speak=speak,
                        provider=provider,
                        model_name=model_name,
                    )
                else:
                    from distr.core.signals import signal_manager

                    intake_context: Dict[str, Any] = {}
                    if request_data.intake_source_message_id:
                        intake_context["source_message_id"] = request_data.intake_source_message_id
                    if request_data.intake_requested_outcome:
                        intake_context["requested_outcome"] = request_data.intake_requested_outcome
                    if isinstance(request_data.intake_metadata, dict):
                        intake_context["metadata"] = request_data.intake_metadata
                    signal_options: Dict[str, Any] = {}
                    if intake_context:
                        signal_options["work_intake"] = intake_context
                    if request_data.origin_surface:
                        signal_options["origin_surface"] = request_data.origin_surface
                    if request_data.origin_request_id:
                        signal_options["origin_request_id"] = request_data.origin_request_id
                    if request_data.input_type:
                        signal_options["input_type"] = request_data.input_type
                    if uploaded_image:
                        signal_options["uploaded_image_path"] = str(uploaded_image)

                    signal_manager.web_send_to_agent_requested.emit(
                        chat_id,
                        agent_message,
                        speak,
                        provider,
                        model_name,
                        signal_options or None,
                    )
                logger.info(
                    "Send-to-agent: emitted web_send_to_agent_requested for chat_id=%s",
                    chat_id,
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning("Failed to send to agent: %s", e, exc_info=True)
                raise HTTPException(status_code=500, detail="Agent not available")
            response_payload = {
                "sent": True,
                "intake_source_message_id": request_data.intake_source_message_id,
            }
            # The agent process persists the user row asynchronously; resolve its
            # id (turn_id) and optionally block until the reply is final.
            try:
                response_payload.update(
                    await turn_response_fields(
                        chat_id,
                        turn_id=None,
                        after_row_id=after_row_id,
                        message=agent_message,
                        wait=bool(wait),
                        timeout=timeout,
                    )
                )
            except Exception:
                logger.debug("Send-to-agent: turn resolution failed", exc_info=True)
                response_payload.update({"turn_id": None, "turn_pending": True, "after_row_id": after_row_id})
            # Stateless reply handle: GET /api/chat/replies/{reply_id}?wait=N (token-gated).
            from distr.gui.web.routes.api_audit import make_reply_id

            response_payload["reply_id"] = make_reply_id(chat_id, after_row_id, agent_message)
            response_payload["reply_url"] = f"/api/chat/replies/{response_payload['reply_id']}"
            return JSONResponse(response_payload)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Send to agent failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chats/{chat_id}/turns/{turn_id}/steer")
    async def steer_chat_turn(chat_id: int, turn_id: int, request_data: SteerTurnRequest):
        """Persist guidance now and route delegated work through its live steer path."""
        guidance = (request_data.message or "").strip()
        if not guidance:
            raise HTTPException(status_code=400, detail="Steering guidance is required")
        with get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if not chat:
                raise HTTPException(status_code=404, detail="Chat not found")
            _reject_development_lifecycle(session, chat)
        from distr.core.chat_turns import get_turns, steer_turn

        state = get_turns(chat_id)
        active = state.get("active_turn")
        if not active or int(active.get("turn_id") or 0) != int(turn_id):
            raise HTTPException(status_code=409, detail="That turn is no longer active")
        event = steer_turn(chat_id, turn_id, guidance)
        if event is None:
            raise HTTPException(status_code=404, detail="Chat turn not found")

        delegated: Optional[Dict[str, Any]] = None
        # A workflow projected into this turn exposes its run id in event
        # metadata. Route through the existing harness steering contract when
        # available; otherwise the conversational provider consumes the durable
        # steer at its next tool/synthesis boundary.
        for prior in reversed(active.get("events") or []):
            metadata = prior.get("metadata") if isinstance(prior, dict) else None
            run_id = (metadata or {}).get("run_id") if isinstance(metadata, dict) else None
            if run_id is None:
                continue
            try:
                from distr.core.workflow.service import apply_run_harness_steer

                delegated = apply_run_harness_steer(
                    int(run_id), guidance, source="chat_turn"
                )
            except Exception as exc:
                delegated = {"error": str(exc), "delivered": False}
            break
        return JSONResponse({"accepted": True, "event": event, "delegated": delegated})

    @router.post("/chats/restore-speaker")
    async def restore_speaker():
        """Re-enable agent TTS after a web message sent with speaker off."""
        try:
            from distr.core.signals import signal_manager

            signal_manager.set_speaker_enabled.emit(True)
            return JSONResponse({"ok": True})
        except Exception as e:
            logger.warning("Restore speaker failed: %s", e)
            return JSONResponse({"ok": False})

    @router.post("/tts/stop")
    async def stop_tts():
        """Silence the agent when there is no chat to cancel against.

        The About window reads its copy aloud with no active chat, so the chat
        cancel route was unreachable and Stop did nothing there.
        """
        try:
            from distr.core.signals import signal_manager

            signal_manager.interrupt_tts.emit()
        except Exception as e:
            logger.debug("Stop TTS: interrupt_tts emit failed: %s", e)
        return JSONResponse({"ok": True})

    @router.post("/chats/{chat_id}/cancel")
    async def cancel_stream(chat_id: int):
        """Tell the desktop agent to interrupt TTS/generation."""
        with get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if not chat:
                raise HTTPException(status_code=404, detail="Chat not found")
            _reject_development_lifecycle(session, chat)
        try:
            from distr.core.signals import signal_manager

            signal_manager.interrupt_tts.emit()
            logger.debug("Cancel: emitted interrupt_tts for agent")
        except Exception as e:
            logger.debug(
                "Cancel: interrupt_tts emit failed (agent may not be running): %s", e
            )
        try:
            from distr.core.chat_turns import latest_active_turn_id, terminal_turn

            turn_id = latest_active_turn_id(chat_id)
            if turn_id is not None:
                terminal_turn(
                    chat_id,
                    "turn_cancelled",
                    turn_id=turn_id,
                    summary="Stopped by the user.",
                )
        except Exception:
            logger.debug("Could not persist turn cancellation", exc_info=True)
        return JSONResponse({"message": "Cancel requested"})

    @router.post("/chats/tts/generate")
    async def generate_tts(request_data: TTSGenerateRequest):
        """Generate TTS audio for the given text; returns WAV or MP3. Uses chat voice when chat_id given and provider/voice not provided."""
        try:
            from distr.core.audio.tts_handler import generate_tts_audio, wav_to_mp3
            from datetime import datetime

            provider = request_data.provider
            voice = request_data.voice
            if (provider is None or voice is None) and request_data.chat_id is not None:
                with get_session() as session:
                    chat = (
                        session.query(Chat)
                        .filter(Chat.id == request_data.chat_id)
                        .first()
                    )
                    if chat:
                        if provider is None and (chat.voice_provider or "").strip():
                            provider = (chat.voice_provider or "").strip()
                        if voice is None and (chat.voice_model or "").strip():
                            voice = (chat.voice_model or "").strip()
            out_path = await asyncio.to_thread(
                generate_tts_audio,
                request_data.text,
                provider,
                voice,
                request_data.speed or 1.0,
            )
            if not out_path or not Path(out_path).exists():
                raise HTTPException(status_code=500, detail="TTS generation failed")
            fmt = (request_data.format or "wav").strip().lower()
            if fmt == "mp3":
                timestamp_id = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                mp3_path = Path(out_path).with_suffix(".mp3")
                await asyncio.to_thread(wav_to_mp3, out_path, str(mp3_path))
                if not mp3_path.exists():
                    raise HTTPException(status_code=500, detail="MP3 conversion failed")
                return FileResponse(
                    str(mp3_path),
                    media_type="audio/mpeg",
                    filename=f"tts_{timestamp_id}.mp3",
                )
            return FileResponse(out_path, media_type="audio/wav", filename="tts.wav")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid request")
        except Exception as e:
            logger.error("TTS generate error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.post("/chats/{chat_id}/read-response", dependencies=[Depends(require_internal_token_dependency)])
    async def read_response(chat_id: int, request_data: ReadResponseRequest):
        """Rewrite an assistant reply for speech with the chat/oracle model and store it in the thread."""
        from distr.core import read_response as rr

        # Dedup: a reply that already has a spoken row returns it (no LLM call, no insert).
        if request_data.chat_row_id is not None:
            with get_session() as session:
                existing = rr.find_existing_spoken(session, chat_id, request_data.chat_row_id)
            if existing:
                return JSONResponse({"spoken_text": existing[1], "chat_row_id": existing[0], "existing": True})

        settings = load_settings_from_db()
        try:
            spoken, provider, model = await asyncio.to_thread(
                rr.generate_spoken_text, request_data.text, settings
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Nothing to read")
        except Exception as e:
            logger.warning("Read response generation failed: %s", e)
            raise HTTPException(status_code=502, detail="Could not prepare the spoken version")
        with get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if not chat:
                raise HTTPException(status_code=404, detail="Chat not found")
            # Race guard: another click may have stored one while the model ran.
            existing = rr.find_existing_spoken(session, chat_id, request_data.chat_row_id)
            if existing:
                return JSONResponse({"spoken_text": existing[1], "chat_row_id": existing[0], "provider": provider, "model": model, "existing": True})
            row_id = rr.store_spoken_message(
                session, chat_id, spoken,
                source_row_id=request_data.chat_row_id, provider=provider, model=model,
            )
        return JSONResponse({"spoken_text": spoken, "chat_row_id": row_id, "provider": provider, "model": model})

    @router.post("/chats/speak-spoken", dependencies=[Depends(require_internal_token_dependency)])
    async def speak_spoken(request_data: SpeakSpokenRequest, request: Request):
        """User-clicked playback of a spoken response via the oracle's speak_text_directly path.

        Guarded on its own (not only by the /api middleware): localhost + internal token.
        """
        from distr.core import read_response as rr
        from distr.gui.web.security import require_internal_token_request

        if not request.client or request.client.host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="Only localhost may trigger speech")
        require_internal_token_request(request)

        try:
            ok = rr.speak_spoken_text(request_data.text)
        except Exception as e:
            logger.warning("Speak spoken response failed: %s", e)
            ok = False
        note_id = None
        if ok and request_data.chat_id is not None:
            # Oracle context note: mirror into the oracle's current chat (read-only lookup,
            # never changes routing). Deduplicated per source thread + reply.
            try:
                current = ChatService.get_current_chat_id()
                with get_session() as session:
                    note_id = rr.add_oracle_context_note(
                        session, request_data.chat_id, request_data.text,
                        source_row_id=request_data.source_row_id, current_chat_id=current,
                    )
            except Exception as e:
                logger.warning("Oracle context note failed: %s", e)
        return JSONResponse({"ok": bool(ok), "oracle_note_row_id": note_id})

    @router.get("/models")
    async def get_models():
        """Default LLM for new chats = conversational LLM from Settings (Conversational area)."""
        try:
            settings = load_settings_from_db()
            provider = (settings.get("conversational_llm_provider") or "Ollama").strip()
            model = (settings.get("conversational_llm_model") or "").strip()
            return JSONResponse(
                {"provider": provider.lower() if provider else "ollama", "model": model}
            )
        except Exception as e:
            logger.error(f"Failed to get models: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    @router.get("/voice-options")
    async def get_voice_options(provider: str):
        """Get available voice/model options for a TTS provider (kokoro, openai, elevenlabs)."""
        voices = []
        if provider == "kokoro":
            voices = [
                {"id": "af", "name": "af (Female, American)"},
                {"id": "af_bella", "name": "af_bella (Bella)"},
                {"id": "af_heart", "name": "af_heart (Heart)"},
                {"id": "af_nicole", "name": "af_nicole (Female, American)"},
                {"id": "af_sarah", "name": "af_sarah (Female, American)"},
                {"id": "af_sky", "name": "af_sky (Female, American)"},
                {"id": "am_adam", "name": "am_adam (Male, American)"},
                {"id": "am_michael", "name": "am_michael (Male, American)"},
                {"id": "bf_emma", "name": "bf_emma (Female, British)"},
                {"id": "bf_isabella", "name": "bf_isabella (Female, British)"},
                {"id": "bm_george", "name": "bm_george (Male, British)"},
                {"id": "bm_lewis", "name": "bm_lewis (Male, British)"},
            ]
        elif provider == "openai":
            voices = [
                {"id": "alloy", "name": "Alloy"},
                {"id": "echo", "name": "Echo"},
                {"id": "fable", "name": "Fable"},
                {"id": "onyx", "name": "Onyx"},
                {"id": "nova", "name": "Nova"},
                {"id": "shimmer", "name": "Shimmer"},
            ]
        elif provider == "elevenlabs":
            voices = [{"id": "default", "name": "Default Voice"}]
        return JSONResponse({"voices": voices})

    return router
