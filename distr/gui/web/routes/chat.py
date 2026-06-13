"""
API routes/endpoints for Chat web UI
"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from starlette.requests import ClientDisconnect, Request
from pydantic import BaseModel, ConfigDict
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Iterable
from collections import defaultdict
import logging
import json
import re
import asyncio
import threading
from datetime import datetime

# WebSocket connection manager: chat_id -> set of WebSocket instances subscribed to that chat
_chat_ws_connections: Dict[int, Set[WebSocket]] = {}
_chat_ws_lock = threading.Lock()

from distr.core.db import get_session, Chat
from distr.core.chat import ChatService, record_chat_audit_event
from distr.core.llm_factory import normalize_provider as _normalize_provider
from distr.core.settings import load_settings_from_db
from distr.core.agent.service_factory import resolve_voice_to_display_name
from distr.core.agent.constants import normalize_voice_provider
from distr.core.agent.services.tts.registry import tts_registry
from distr.core.chat_title_auto import (
    TITLE_REFRESH_MESSAGE_INTERVAL,
    maybe_refresh_chat_title as _maybe_refresh_chat_title,
    _title_auto_meta,
)
from distr.gui.web.security import (
    is_allowed_local_origin,
    websocket_has_valid_internal_token,
    rate_limiter,
)

logger = logging.getLogger(__name__)


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
                },
            }
        )
    return messages


def _message_sort_key(message: Dict[str, Any]) -> str:
    ts = message.get("timestamp")
    if ts is None:
        return ""
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


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
        "timestamp": ts,
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
        messages.append(
            {
                "role": "user",
                "content": row.input,
                "timestamp": row.created_date if row.created_date else None,
                "chat_row_id": row.id,
            }
        )
    if row.response:
        messages.append(
            {
                "role": "assistant",
                "content": row.response,
                "timestamp": row.modified_date if row.modified_date else None,
                "chat_row_id": row.id,
            }
        )


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
    messages.extend(workflow_msgs)

    return messages


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


class CreateChatRequest(ChatRequestModel):
    """Request to create a new chat (used by web UI and empty-state / starting form)."""

    title: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    voice_provider: Optional[str] = None
    voice_model: Optional[str] = None
    starting_question: Optional[str] = None
    speak: Optional[bool] = (
        None  # If True, agent speaks the reply (TTS). Used when starting_question is set.
    )


class UpdateChatRequest(ChatRequestModel):
    """Request to update chat. All fields optional; only provided fields are updated."""

    title: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    voice_provider: Optional[str] = None
    voice_model: Optional[str] = None


class CompactChatRequest(ChatRequestModel):
    """Request to compact a chat into a Hermes-backed checkpoint."""

    provider: Optional[str] = None
    model_name: Optional[str] = None
    reason: Optional[str] = None


class ForkChatRequest(ChatRequestModel):
    """Request to fork a chat after compacting it."""

    provider: Optional[str] = None
    model_name: Optional[str] = None
    title: Optional[str] = None


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
        payload = json.dumps({"type": "chat_updated", "chat_id": chat_id})
        with _chat_ws_lock:
            conns = list(_chat_ws_connections.get(chat_id, set()))
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception as e:
                logger.debug("WebSocket send error: %s", e)
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
        payload = json.dumps(body)
        with _chat_ws_lock:
            conns = list(_chat_ws_connections.get(chat_id, set()))
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception as e:
                logger.debug("WebSocket send error: %s", e)
        return JSONResponse({"ok": True})

    @router.get("/chats")
    async def get_chats():
        """Get all chat conversations (root chats only)"""
        try:
            with get_session() as session:
                # Get root chats (parent_id is None) that are not archived or hidden
                chats = (
                    session.query(Chat)
                    .filter(
                        Chat.parent_id == None,
                        Chat.is_archived == False,
                        Chat.is_hidden == False,
                    )
                    .order_by(Chat.modified_date.desc())
                    .all()
                )

                result = []
                for chat in chats:
                    result.append(
                        {
                            "id": chat.id,
                            "title": chat.title or "New Chat",
                            "created_date": chat.created_date.isoformat()
                            if chat.created_date
                            else None,
                            "modified_date": chat.modified_date.isoformat()
                            if chat.modified_date
                            else None,
                            "model_name": chat.model_name,
                            "provider": chat.provider,
                        }
                    )
                settings = load_settings_from_db()
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
            voice_model_raw = (
                settings.get("kokoro_voice")
                or settings.get("openai_voice")
                or settings.get("elevenlabs_voice")
                or ""
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

    @router.get("/chats/{chat_id}")
    async def get_chat(chat_id: int):
        """Get a specific chat with all its messages"""
        try:
            with get_session() as session:
                # Get root chat
                root_chat = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root_chat:
                    raise HTTPException(status_code=404, detail="Chat not found")

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
                rows = session.execute(thread_query, {"root_id": chat_id}).fetchall()

                messages = _merge_thread_rows_with_tool_and_workflow_events(rows, root_chat)

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
                additional_context = _chat_additional_context(root_chat.additional_context)
                compact_checkpoint = additional_context.get("compact_checkpoint")

                return JSONResponse(
                    {
                        "id": root_chat.id,
                        "title": header_payload["title"],
                        "messages": messages,
                        "provider": provider,
                        "model_name": model_name,
                        "voice_provider": voice_provider,
                        # Keep runtime/raw voice ID for agent swapping, and provide a
                        # display label separately for the UI.
                        "voice_model": voice_model_raw,
                        "voice_model_display": voice_model_display,
                        "context_stats": header_payload["context_stats"],
                        "title_auto": header_payload["title_auto"],
                        "compact_checkpoint": compact_checkpoint,
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
            settings = load_settings_from_db()
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
            from distr.core.chat import _normalize_provider

            provider = _normalize_provider(raw_provider)

            # Validate provider
            valid_providers = [
                "Ollama",
                "OpenAI",
                "Anthropic",
                "Groq",
                "OpenRouter",
                "KiloCode",
                "Google Gemini",
            ]
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
            if voice_provider and voice_provider in tts_registry:
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
                llm_provider=raw_provider,
                llm_model=model_name,
                tts_provider=voice_provider,
                tts_voice=voice_model,
                title=title,
                starting_question=starting_question,
            )

            with get_session() as session:
                chat = session.get(Chat, chat_id)
                if not chat:
                    raise HTTPException(
                        status_code=500, detail="Chat created but not found"
                    )
                chat_dict = {
                    "id": chat.id,
                    "title": chat.title or "New Chat",
                    "provider": chat.provider,
                    "model_name": chat.model_name,
                    "voice_provider": chat.voice_provider,
                    "voice_model": chat.voice_model,
                }

            # ── Persist chat selections to global settings so the next "new chat" ──
            # defaults to these choices. Persist resolved values (not only explicit
            # request fields) so provider/model and voice provider/voice stay in sync.
            try:
                from distr.core.settings import save_settings_to_db as _save_settings
                _settings = load_settings_from_db()
                _changed = False

                # LLM provider & model
                _settings["conversational_llm_provider"] = provider  # normalized
                _settings["llm_provider"] = provider
                _settings["agent_provider"] = provider
                _changed = True
                if model_name:
                    _settings["conversational_llm_model"] = model_name
                    _settings["llm_model"] = model_name
                    _settings["agent_model"] = model_name
                    _changed = True

                # TTS: same as Settings → General / save_voice_selection — voice_provider id,
                # tts_provider = descriptor display name, tts_voice + per-provider *_voice columns.
                if voice_provider and voice_model:
                    from distr.core.services.settings_service import (
                        apply_voice_selection_to_settings,
                    )

                    if apply_voice_selection_to_settings(
                        _settings, voice_provider, voice_model
                    ):
                        _changed = True

                if _changed:
                    _save_settings(_settings)
                    logger.info(
                        "Create chat: persisted selections to global settings (provider=%s, model=%s, voice_provider=%s, voice_model=%s)",
                        provider, model_name, voice_provider, voice_model,
                    )
            except Exception as e:
                logger.warning("Create chat: could not persist selections to settings: %s", e)

            try:
                from distr.core.signals import signal_manager

                if first_message:
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
            with get_session() as session:
                chat = session.query(Chat).filter(Chat.id == chat_id).first()
                if not chat:
                    raise HTTPException(status_code=404, detail="Chat not found")
                updated = False
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
                if request_data.model_name is not None:
                    chat.model_name = (request_data.model_name or "").strip() or None
                    updated = True
                if request_data.voice_provider is not None:
                    chat.voice_provider = (
                        request_data.voice_provider or ""
                    ).strip() or None
                    updated = True
                if request_data.voice_model is not None:
                    chat.voice_model = (request_data.voice_model or "").strip() or None
                    updated = True
                if updated:
                    chat.modified_date = datetime.utcnow()
                    session.commit()

                # Read ORM fields before the session closes (avoids DetachedInstanceError).
                response_payload = {
                    "id": int(chat.id),
                    "title": chat.title or "New Chat",
                    "provider": chat.provider,
                    "model_name": chat.model_name,
                    "voice_provider": chat.voice_provider,
                    "voice_model": chat.voice_model,
                }

            # ── Persist to global settings so next new chat defaults to these choices ──
            try:
                _sett = load_settings_from_db()
                _changed = False
                if request_data.provider and request_data.provider.strip():
                    from distr.core.chat import _normalize_provider
                    _sett["conversational_llm_provider"] = _normalize_provider(request_data.provider)
                    _sett["llm_provider"] = _normalize_provider(request_data.provider)
                    _sett["agent_provider"] = _normalize_provider(request_data.provider)
                    _changed = True
                if request_data.model_name and request_data.model_name.strip():
                    _sett["conversational_llm_model"] = request_data.model_name.strip()
                    _sett["llm_model"] = request_data.model_name.strip()
                    _sett["agent_model"] = request_data.model_name.strip()
                    _changed = True

                # Voice globals: same shape as create-chat / save_voice_selection (tts_provider name,
                # tts_voice, *_voice columns). Only when this PATCH touches voice fields.
                voice_touched = (
                    request_data.voice_provider is not None
                    or request_data.voice_model is not None
                )
                if voice_touched:
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
                rows = _thread_rows_for_compaction(session, chat_id)
                messages = _messages_from_rows(rows)
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
        """Create a visible, Hermes-backed compact checkpoint for this chat."""
        try:
            settings = load_settings_from_db()
            with get_session() as session:
                root = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root:
                    raise HTTPException(status_code=404, detail="Chat not found")
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
                refreshed_messages = _messages_from_rows(rows) + [
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
                logger.debug("Hermes compact event failed", exc_info=True)
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
                logger.debug("Hermes fork event failed", exc_info=True)
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
        """Delete a chat (cascades to all children)"""
        try:
            with get_session() as session:
                chat = session.query(Chat).filter(Chat.id == chat_id).first()
                if not chat:
                    raise HTTPException(status_code=404, detail="Chat not found")
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

    @router.post("/chats/{chat_id}/load-in-agent")
    async def load_chat_in_agent(chat_id: int):
        """Load this chat into the agent (set as current). Persist last_chat_id so the agent picks up this chat's voice on reload."""
        try:
            with get_session() as session:
                root = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root:
                    raise HTTPException(status_code=404, detail="Chat not found")
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

    @router.post("/chats/{chat_id}/send-to-agent")
    async def send_to_agent(chat_id: int, request_data: SendMessageRequest):
        """Send the message to the agent. Persist the user message to the DB immediately so it survives refresh; then emit to agent for LLM + TTS.
        Always use this chat's provider/model from DB when not provided in request."""
        try:
            with get_session() as session:
                root = session.query(Chat).filter(Chat.id == chat_id).first()
                if not root:
                    raise HTTPException(status_code=404, detail="Chat not found")
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
            speak_val = getattr(request_data, "speak", None)
            # Default True when omitted so TTS plays; only disable when explicitly false
            speak = False if speak_val in (False, "false", "False", 0, "0") else True

            # Validate provider if provided
            if provider is not None and provider.strip():
                from distr.core.chat import _normalize_provider

                norm_provider = _normalize_provider(provider)
                valid_providers = [
                    "Ollama",
                    "OpenAI",
                    "Anthropic",
                    "Groq",
                    "OpenRouter",
                    "KiloCode",
                    "Google Gemini",
                ]
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

                    signal_manager.web_send_to_agent_requested.emit(
                        chat_id,
                        agent_message,
                        speak,
                        provider,
                        model_name,
                        None,
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
            return JSONResponse({"sent": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Send to agent failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

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

    @router.post("/chats/{chat_id}/cancel")
    async def cancel_stream(chat_id: int):
        """Tell the desktop agent to interrupt TTS/generation."""
        try:
            from distr.core.signals import signal_manager

            signal_manager.interrupt_tts.emit()
            logger.debug("Cancel: emitted interrupt_tts for agent")
        except Exception as e:
            logger.debug(
                "Cancel: interrupt_tts emit failed (agent may not be running): %s", e
            )
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
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Invalid request")
        except Exception as e:
            logger.error("TTS generate error: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

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
