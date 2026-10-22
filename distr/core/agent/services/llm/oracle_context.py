"""Oracle turn context helpers (pure, DB-light, unit-testable).

- RAG block header/filtering so retrieved snippets are never mistaken for the
  project name, and routing always sees the original user text.
- Current project / Development thread derivation from the oracle_note rows the
  web server mirrors into the Oracle chat (``source_thread_id``).
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

RAG_HEADER = "[Background snippets from memory store — not the project name]"
LEGACY_RAG_HEADERS = (
    "[Retrieved project context — MemPalace]",
    "[Retrieved project context]",
)
USER_REQUEST_MARKER = "\n\nUser request:\n"
CONTEXT_LINE_PREFIX = "[Oracle context] Current project: "

_SKIP_WINGS = ("seed_corpus_pack",)
_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "it",
    "i", "me", "my", "you", "your", "we", "what", "which", "that", "this", "just",
    "please", "can", "could", "would", "do", "did", "am", "are", "was", "be", "from",
    "about", "now", "right", "hey", "here", "there", "ask", "asked", "send", "tell",
    "how", "why", "when", "where", "who", "have", "has", "had", "it's", "i'm",
}


def strip_rag_block(content: Any) -> Any:
    """Return the original user text when ``content`` carries an injected RAG block."""
    if not isinstance(content, str):
        return content
    if content.startswith((RAG_HEADER,) + LEGACY_RAG_HEADERS) and USER_REQUEST_MARKER in content:
        return content.rsplit(USER_REQUEST_MARKER, 1)[1]
    return content


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9_'-]{2,}", (text or "").lower()) if w not in _STOP}


def filter_rag_snippets(query: str, ctx: str, *, min_overlap: int = 2, min_ratio: float = 0.25) -> str:
    """Drop seed_corpus_pack chunks and chunks weakly related to ``query``.

    Chunks are the ``\\n---\\n``-joined ``[wing/room] text`` blocks from
    MemPalace ``format_search_as_context``. A chunk is kept when it shares at
    least ``min_overlap`` content words with the query, or at least
    ``min_ratio`` of the query's content words (for short queries).
    """
    q = _words(query)
    if not q or not ctx:
        return ""
    kept: list[str] = []
    for chunk in str(ctx).split("\n---\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"\[([^\]/]+)/", chunk)
        if m and m.group(1).strip() in _SKIP_WINGS:
            continue
        overlap = len(q & _words(chunk))
        if overlap >= min_overlap or (overlap and overlap / len(q) >= min_ratio and len(q) <= 3):
            kept.append(chunk)
    return "\n---\n".join(kept)


# ---- current Development thread -------------------------------------------------

_lock = threading.Lock()
_current: dict[str, Any] = {}


def set_current_development_thread(info: Optional[dict]) -> None:
    with _lock:
        _current.clear()
        if info:
            _current.update(info)


def get_current_development_thread() -> dict:
    with _lock:
        return dict(_current)


def derive_current_development_thread(session, oracle_chat_id: int) -> Optional[dict]:
    """Latest Development thread mirrored into the Oracle chat (via oracle_note rows).

    Returns ``{"thread_id", "title", "project_id", "project_name"}`` or None.
    """
    from sqlalchemy import text as _t

    rows = session.execute(
        _t(
            "SELECT additional_context FROM chats WHERE parent_id = :p "
            "AND additional_context LIKE '%oracle_note%' ORDER BY id DESC LIMIT 20"
        ),
        {"p": int(oracle_chat_id)},
    ).fetchall()
    thread_id = None
    for (raw,) in rows:
        try:
            ctx = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(ctx, dict) and ctx.get("oracle_note") and str(ctx.get("source_thread_id") or "").isdigit():
            thread_id = int(ctx["source_thread_id"])
            break
    if thread_id is None:
        return None
    row = session.execute(
        _t(
            "SELECT c.id, c.title, c.project_id, p.name FROM chats c "
            "LEFT JOIN projects p ON p.id = c.project_id WHERE c.id = :i"
        ),
        {"i": thread_id},
    ).fetchone()
    if not row:
        return None
    return {
        "thread_id": int(row[0]),
        "title": str(row[1] or "").strip(),
        "project_id": int(row[2]) if row[2] is not None else None,
        "project_name": str(row[3] or "").strip(),
    }


def format_context_line(info: Optional[dict]) -> str:
    if not info:
        return ""
    name = info.get("project_name") or "unknown"
    pid = info.get("project_id")
    pid_s = f" (id {pid})" if pid is not None else ""
    title = (info.get("title") or "").replace("\n", " ").replace('"', "'")[:80]
    return (
        f'{CONTEXT_LINE_PREFIX}{name}{pid_s}; current Development thread: '
        f'{info["thread_id"]} "{title}"'
    )
