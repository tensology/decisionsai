"""Resolve "read it / read that" against the conversation before the clipboard.

On 15 Nov Paul asked "Can you see what I said to Uncle Roger?", then "I just
want you to read it." The fast-action detector matched "read it" and read the
clipboard aloud. When the recent conversation names a thread, message, agent
or other referent and the request does not mention the clipboard or a
selection, the clipboard fast action is deferred to the model, which is told
what "it" most likely refers to.
"""

from __future__ import annotations

import re
from typing import Iterable

_ANAPHORIC_READ_RE = re.compile(
    r"\bread\s+(?:it|that|this|them|those)\b|\bread\s+(?:it|that)\s+(?:out|to\s+me|aloud)\b", re.IGNORECASE
)
_CLIPBOARD_WORDS_RE = re.compile(
    r"\b(?:clipboard|copied|copy|paste|pasted|selected|selection|highlighted|on\s+(?:my\s+)?screen)\b", re.IGNORECASE
)
_REFERENT_RE = re.compile(
    r"\b(?:what\s+(?:i|he|she|they|you|we)\s+(?:said|wrote|told|asked|sent)|i\s+(?:said|wrote|told|asked|sent)|"
    r"thread|conversation|chat\s+with|chat|message|messages|reply|email|mail|ticket|report|note|document|"
    r"doc|file|agent|transcript)\b",
    re.IGNORECASE,
)
_SAID_TO_RE = re.compile(r"\b(?:said|say|wrote|write|told|tell|asked|sent|talking|spoke)\s+(?:to|with)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,2})")
_NAME_RE = re.compile(r"\b([A-Z][a-z][\w'-]*(?:\s+[A-Z][a-z][\w'-]*){0,2})\b")


def _known_agent_names() -> list[str]:
    try:
        from distr.core.db import ChatAgent, get_session

        with get_session() as session:
            return [str(row.name) for row in session.query(ChatAgent).all() if row.name]
    except Exception:
        return []


def find_referent(recent_user_texts: Iterable[str], agent_names: Iterable[str] | None = None) -> str:
    """Best guess at what "it" refers to in the recent conversation, or ""."""
    names = list(agent_names) if agent_names is not None else _known_agent_names()
    for text in reversed([str(t or "") for t in recent_user_texts if str(t or "").strip()]):
        for name in names:
            if name and re.search(r"\b" + re.escape(name) + r"\b", text, re.IGNORECASE):
                return f"the conversation with {name}"
        said_to = _SAID_TO_RE.search(text)
        if said_to:
            return f"the conversation with {said_to.group(1)}"
        if _REFERENT_RE.search(text) and not _CLIPBOARD_WORDS_RE.search(text):
            match = _REFERENT_RE.search(text)
            return f"the {match.group(0).lower()} mentioned earlier"
    return ""


def should_defer_clipboard_read(text: str, recent_user_texts: Iterable[str], agent_names: Iterable[str] | None = None) -> str:
    """Return the referent when an anaphoric read must go to the model, else ""."""
    clean = str(text or "")
    if not _ANAPHORIC_READ_RE.search(clean) or _CLIPBOARD_WORDS_RE.search(clean):
        return ""
    recent = [t for t in recent_user_texts if str(t or "").strip()][-3:]
    return find_referent(recent, agent_names)


def referent_hint(referent: str) -> str:
    return (
        f"[CONTEXT] \"it\" here most likely means {referent}, not the clipboard. Read that with the "
        "conversation/thread tools (read_conversation, open_thread or development_control get_thread) and "
        "answer from it. Only use the clipboard if the user says so."
    )
