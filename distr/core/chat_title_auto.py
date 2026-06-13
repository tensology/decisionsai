"""Lightweight automatic chat title refresh from recent conversation."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from distr.core.db import Chat, get_session
from distr.core.settings import load_settings_from_db

logger = logging.getLogger(__name__)

TITLE_REFRESH_MESSAGE_INTERVAL = 5
TITLE_CONTEXT_MESSAGE_COUNT = 5


def _chat_additional_context(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _title_auto_meta(additional_context: Dict[str, Any]) -> Dict[str, Any]:
    raw = additional_context.get("title_auto")
    return raw if isinstance(raw, dict) else {}


def _conversation_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in messages if m.get("role") in {"user", "assistant"}]


def _thread_rows(session, chat_id: int):
    thread_query = text(
        """
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
        """
    )
    return session.execute(thread_query, {"root_id": chat_id}).fetchall()


def _append_row_messages(messages: List[Dict[str, Any]], row: Any) -> None:
    if getattr(row, "is_hidden", False):
        return
    if row.input:
        messages.append({"role": "user", "content": row.input})
    if row.response:
        messages.append({"role": "assistant", "content": row.response})


def _messages_from_rows(rows) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for row in rows:
        _append_row_messages(messages, row)
    return messages


def _load_chat_messages(session, chat_id: int) -> List[Dict[str, Any]]:
    rows = _thread_rows(session, chat_id)
    return _messages_from_rows(rows)


def _fallback_chat_title(messages: List[Dict[str, Any]]) -> str:
    for msg in reversed(_conversation_messages(messages)):
        if msg.get("role") != "user":
            continue
        text_value = str(msg.get("content") or "").strip()
        if not text_value:
            continue
        first_line = text_value.split("\n", 1)[0].strip()
        if len(first_line) > 60:
            return first_line[:57].rstrip() + "..."
        return first_line
    return "New Chat"


def _lightweight_title_models(settings: dict) -> List[tuple[str, str]]:
    """Prefer tiny local models; only use cloud mini models when configured."""
    models: List[tuple[str, str]] = []
    ollama_url = (settings.get("ollama_url") or "http://localhost:11434/").strip()
    if ollama_url:
        models.append(("ollama", "qwen3:0.6b"))
        models.append(("ollama", "llama3.2"))
    if settings.get("groq_enabled") and (settings.get("groq_key") or "").strip():
        models.append(("groq", "llama-3.1-8b-instant"))
    if settings.get("openai_enabled") and (settings.get("openai_key") or "").strip():
        models.append(("openai", "gpt-4o-mini"))
    return models


def _clean_title(raw: str) -> str:
    title = (raw or "").strip().strip("\"'")
    for prefix in ("Title:", "title:", "Chat title:", "Short title:"):
        if title.lower().startswith(prefix.lower()):
            title = title[len(prefix) :].strip()
    title = " ".join(title.split())
    title = title.split("\n", 1)[0].strip()
    if title.endswith("."):
        title = title[:-1].strip()
    return title[:80]


def suggest_chat_title(messages: List[Dict[str, Any]], *, settings: dict) -> str:
    """Suggest a short sentiment/topic title from the last few turns."""
    recent = _conversation_messages(messages)[-TITLE_CONTEXT_MESSAGE_COUNT:]
    transcript = []
    for msg in recent:
        role = msg.get("role") or "message"
        content = str(msg.get("content") or "").strip()
        if content:
            transcript.append(f"{role.upper()}: {content[:400]}")
    transcript_text = "\n".join(transcript)
    if not transcript_text:
        return "New Chat"

    try:
        import litellm
        from distr.core.workflow.planning import _litellm_model

        for provider, model_name in _lightweight_title_models(settings):
            try:
                response = litellm.completion(
                    model=_litellm_model(provider, model_name, settings),
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Write a very short chat title (3-6 words) that captures the mood "
                                "and topic of this exchange — like a brief chapter heading. "
                                "No quotes, no trailing punctuation, no filler."
                            ),
                        },
                        {"role": "user", "content": transcript_text},
                    ],
                    max_tokens=24,
                    temperature=0.2,
                    timeout=8,
                )
                title = _clean_title((response.choices[0].message.content or "").strip())
                if title:
                    return title
            except Exception:
                logger.debug(
                    "Lightweight title model failed (%s/%s)",
                    provider,
                    model_name,
                    exc_info=True,
                )
                continue
    except Exception:
        logger.warning("Chat title suggestion failed; using fallback", exc_info=True)
    return _fallback_chat_title(messages)


def maybe_refresh_chat_title(
    session,
    root_chat: Chat,
    messages: List[Dict[str, Any]],
    *,
    settings: Optional[dict] = None,
    force: bool = False,
) -> Optional[str]:
    """Update the chat title when enough new turns have accumulated."""
    settings = settings or load_settings_from_db()
    additional_context = _chat_additional_context(root_chat.additional_context)
    title_auto = _title_auto_meta(additional_context)
    if title_auto.get("manual"):
        return None

    conversation = _conversation_messages(messages)
    message_count = len(conversation)
    last_refresh = int(title_auto.get("last_refresh_message_count") or 0)
    if not force and message_count - last_refresh < TITLE_REFRESH_MESSAGE_INTERVAL:
        return None
    if message_count < 2 and not force:
        return None

    new_title = suggest_chat_title(messages, settings=settings)
    if not new_title:
        return None

    root_chat.title = new_title
    title_auto = {
        "manual": False,
        "last_refresh_message_count": message_count,
        "updated_at": datetime.utcnow().isoformat(),
    }
    additional_context["title_auto"] = title_auto
    root_chat.additional_context = json.dumps(additional_context, ensure_ascii=False, default=str)
    root_chat.modified_date = datetime.utcnow()
    session.commit()
    return new_title


def maybe_refresh_chat_title_for_chat_id(chat_id: int, *, force: bool = False) -> Optional[str]:
    """Load a chat and refresh its title if due."""
    settings = load_settings_from_db()
    with get_session() as session:
        root_chat = session.query(Chat).filter(Chat.id == chat_id).first()
        if not root_chat:
            return None
        messages = _load_chat_messages(session, chat_id)
        return maybe_refresh_chat_title(
            session,
            root_chat,
            messages,
            settings=settings,
            force=force,
        )


def schedule_chat_title_refresh(chat_id: int, *, force: bool = False, emit_update: bool = False) -> None:
    """Fire-and-forget title refresh so voice turns stay responsive."""

    def _run() -> None:
        try:
            new_title = maybe_refresh_chat_title_for_chat_id(chat_id, force=force)
            if new_title and emit_update:
                try:
                    from distr.core.signals import signal_manager

                    signal_manager.emit_chat_updated(chat_id)
                except Exception:
                    logger.debug("Could not emit chat_updated after title refresh", exc_info=True)
        except Exception:
            logger.debug("Background chat title refresh failed for chat %s", chat_id, exc_info=True)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"chat-title-{chat_id}",
    ).start()
