"""User-facing Initiative notice text (not task briefs / injected instructions)."""

from __future__ import annotations

import re
from typing import Any

_NOTICE_OPEN = re.compile(
    r"(?i)^\s*(i\s|i'|i’|i've|i’ve|i'd|i’d|want me|quick check|heads.?up|noticed)"
)
_STRIP_PREFIX = re.compile(
    r"(?i)^\s*(suggestion:|pending approval[^:]*:|\[auto\]|proactive\s*[—\-:]*)\s*"
)
_BRACKET_TAG = re.compile(r"^\[[^\]]+\]\s*")


def looks_like_notice(text: str) -> bool:
    return bool(_NOTICE_OPEN.match(str(text or "").strip()))


def format_initiative_notice(
    *,
    description: str = "",
    draft: str = "",
    telegram_message: str = "",
    payload: dict[str, Any] | None = None,
) -> str:
    """Turn Initiative payload into something you would say to a person.

    Initiative must open as a notice ("I noticed X — want help?"), never as if
    answering a chat injection or reading out an instruction brief.
    """
    payload = payload if isinstance(payload, dict) else {}
    if str(payload.get("source") or "").strip().lower() == "proactive":
        name = str(payload.get("task_name") or "check").strip() or "check"
        return (
            f"I have a scheduled check ready ({name}). "
            "Want me to go through it with you?"
        )

    # Prefer draft when it already sounds like a notice; else description; else telegram.
    candidates = [draft, description, telegram_message]
    raw = ""
    for cand in candidates:
        text = str(cand or "").strip()
        if not text:
            continue
        if looks_like_notice(text):
            raw = text
            break
        if not raw:
            raw = text
    if not raw:
        return ""

    clean = _STRIP_PREFIX.sub("", raw).strip()
    clean = _BRACKET_TAG.sub("", clean).strip()
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
    if looks_like_notice(clean):
        return clean

    # Imperative / task-brief → wrap as an opening notice.
    body = clean.rstrip(".")
    return f"I noticed something I can help with: {body}. Want me to take it from here?"


if __name__ == "__main__":
    assert looks_like_notice("I noticed a stuck ticket on the board.")
    assert not looks_like_notice("Check the backlog and promote tickets")
    assert "scheduled check" in format_initiative_notice(
        description="[Morning] Read inbox and triage",
        payload={"source": "proactive", "task_name": "Morning Brief"},
    )
    out = format_initiative_notice(description="Check the backlog and promote tickets")
    assert out.startswith("I noticed"), out
    assert "Want me" in out
    already = format_initiative_notice(description="I noticed the QA lane is blocked. Want help?")
    assert already.startswith("I noticed the QA lane")
    print("initiative notice ok")
