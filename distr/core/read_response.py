"""'Read response' feature: spoken-style rewrite of an assistant reply.

Uses the CHAT/ORACLE conversational model (settings.conversational_llm_*),
never the thread's development model. The spoken version is stored in the
same thread as an assistant row marked ``additional_context.spoken = true``
so the oracle and agents keep it in context.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SPOKEN_SYSTEM_PROMPT = (
    "You turn a written assistant reply into a short version to be read aloud. "
    "Rules: write plain spoken sentences only. No markdown, no headings, no bullet "
    "or numbered lists, no tables, no code, no emojis. Never read out file paths, "
    "URLs, commands, code identifiers or long numbers; refer to them in words "
    "(for example 'the settings file' or 'a link in the reply'). Summarise any "
    "table or list in a sentence or two describing what it shows. Keep the key "
    "facts, decisions and next steps. Aim for under 120 words. Reply with the "
    "spoken text only."
)

MAX_INPUT_CHARS = 12000


def resolve_oracle_model(settings: Dict[str, Any]) -> Tuple[str, str]:
    """Same precedence the oracle's AgentSession uses when no chat override applies:
    conversational_llm_* then legacy agent_*."""
    from distr.core.llm_factory import normalize_provider

    provider = (settings.get("conversational_llm_provider") or settings.get("agent_provider") or "Ollama")
    model = (settings.get("conversational_llm_model") or settings.get("agent_model") or "").strip()
    return normalize_provider(provider), model


def build_messages(text: str):
    return [
        {"role": "system", "content": SPOKEN_SYSTEM_PROMPT},
        {"role": "user", "content": "Written reply:\n\n" + (text or "")[:MAX_INPUT_CHARS]},
    ]


def _default_complete(provider: str, model: str, messages, settings) -> str:
    from distr.core.llm_factory import create_stream

    return "".join(create_stream(provider, model, messages, settings))


def sanitize_spoken(text: str) -> str:
    """Belt-and-braces cleanup in case the model leaks markdown/URLs/paths."""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"(?<!\w)(?:~|\.{1,2})?/(?:[\w.\-]+/)+[\w.\-]*", "a file path", t)
    t = re.sub(r"^\s*(?:#+|[-*+]|\d+[.)]|\|)\s*", "", t, flags=re.M)
    t = t.replace("|", " ").replace("**", "").replace("__", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def generate_spoken_text(text: str, settings: Dict[str, Any], complete: Optional[Callable] = None) -> Tuple[str, str, str]:
    source = (text or "").strip()
    if not source:
        raise ValueError("Nothing to read")
    provider, model = resolve_oracle_model(settings)
    raw = (complete or _default_complete)(provider, model, build_messages(source), settings)
    spoken = sanitize_spoken(raw)
    if not spoken:
        raise RuntimeError("Model returned no spoken text")
    return spoken, provider, model


def store_spoken_message(session, chat_id: int, spoken: str, *, source_row_id: Optional[int], provider: str, model: str, extra_context: Optional[Dict[str, Any]] = None) -> int:
    """Insert the spoken version as an assistant-only row at the end of the thread."""
    from distr.core.db import Chat
    from distr.core.chat_manager import _thread_root_id

    root_id = _thread_root_id(session, int(chat_id))
    root = session.get(Chat, root_id)
    if root is None:
        raise LookupError("Chat not found")
    now = datetime.now(timezone.utc)
    row = Chat(
        parent_id=root_id,
        title="Spoken response",
        input=None,
        response=spoken,
        additional_context=json.dumps({
            "spoken": True,
            "spoken_source_row_id": source_row_id,
            "spoken_provider": provider,
            "spoken_model": model,
            **(extra_context or {}),
        }),
        provider=root.provider,
        model_name=root.model_name,
        voice_provider=root.voice_provider,
        voice_model=root.voice_model,
        created_date=now,
        modified_date=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return int(row.id)


def speak_spoken_text(text: str) -> bool:
    """Approved for this button only: the oracle's existing direct-TTS path."""
    from distr.core.signals import speak_text_directly_event_queue

    t = (text or "").strip()
    if not t:
        return False
    speak_text_directly_event_queue(t)
    return True


ORACLE_NOTE_PREFIX = "Spoken aloud to the user from Development thread "


def _thread_spoken_rows(session, chat_id: int):
    """Yield (row, context) for spoken rows in the thread containing chat_id."""
    from distr.core.db import Chat
    from distr.core.chat_manager import _thread_root_id

    root_id = _thread_root_id(session, int(chat_id))
    rows = (
        session.query(Chat)
        .filter(Chat.parent_id == root_id, Chat.additional_context.like('%"spoken": true%'))
        .order_by(Chat.id.asc())
        .all()
    )
    for row in rows:
        try:
            ctx = json.loads(row.additional_context or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(ctx, dict) and ctx.get("spoken") is True:
            yield row, ctx


def find_existing_spoken(session, chat_id: int, source_row_id: Optional[int]) -> Optional[Tuple[int, str]]:
    """Existing (non-note) spoken row for this source reply, if any."""
    if source_row_id is None:
        return None
    for row, ctx in _thread_spoken_rows(session, chat_id):
        if ctx.get("oracle_note"):
            continue
        if str(ctx.get("spoken_source_row_id")) == str(source_row_id) and (row.response or "").strip():
            return int(row.id), row.response
    return None


def oracle_note_text(thread_id: int, title: str, spoken: str) -> str:
    label = (title or "").strip().replace("\n", " ")[:80]
    return f"{ORACLE_NOTE_PREFIX}{int(thread_id)} ('{label}'): {(spoken or '').strip()}"


def add_oracle_context_note(session, thread_id: int, spoken: str, *, source_row_id: Optional[int], current_chat_id: Optional[int]) -> Optional[int]:
    """Mirror a spoken Development-thread response into the oracle's current chat so a
    follow-up voice question has it in context. Skips when there is no current chat,
    when it is the same thread, or when an identical note already exists.
    Never changes the oracle's current chat."""
    from distr.core.db import Chat
    from distr.core.chat_manager import _thread_root_id

    text = (spoken or "").strip()
    if not text or current_chat_id is None:
        return None
    thread_root = _thread_root_id(session, int(thread_id))
    oracle_root = _thread_root_id(session, int(current_chat_id))
    if thread_root == oracle_root:
        return None
    for row, ctx in _thread_spoken_rows(session, oracle_root):
        if not ctx.get("oracle_note") or str(ctx.get("source_thread_id")) != str(thread_root):
            continue
        same_source = source_row_id is not None and str(ctx.get("spoken_source_row_id")) == str(source_row_id)
        if same_source or (row.response or "").endswith(text):
            return None
    root = session.get(Chat, thread_root)
    note = oracle_note_text(thread_root, getattr(root, "title", "") or "", text)
    return store_spoken_message(
        session, oracle_root, note,
        source_row_id=source_row_id, provider="", model="",
        extra_context={"oracle_note": True, "source_thread_id": thread_root},
    )
