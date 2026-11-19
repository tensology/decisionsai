"""Cross-chat memory: capture stated preferences and decisions, and show them to the agent.

When the owner states a lasting preference ("I always want…", "from now on…",
"remember that…") or a decision ("we decided…", "let's go with…"), the sentence
is appended to USER.md (preferences) or MEMORY.md (decisions). Every chat's
system prompt then carries a short "What you remember" block, and the agent is
told to say when an answer relies on it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_PREFERENCE_RE = re.compile(
    r"\b(?:remember(?:\s+that)?|from\s+now\s+on|going\s+forward|in\s+future|in\s+the\s+future|"
    r"i\s+(?:always|never|prefer|would\s+rather|'d\s+rather|like\s+(?:it\s+)?when|hate\s+it\s+when|"
    r"don'?t\s+(?:ever\s+)?want|want\s+you\s+to\s+(?:always|never)|always\s+want|only\s+want)|"
    r"(?:please\s+)?(?:always|never)\s+(?:use|give|call|send|reply|answer|write|say|ask|show|put|include))\b",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\b(?:we(?:'ve|\s+have)?\s+decided|i(?:'ve|\s+have)?\s+decided|decision\s*:|let'?s\s+go\s+with|"
    r"we(?:'re|\s+are)\s+going\s+with|we(?:'ll|\s+will)\s+use|the\s+plan\s+is\s+to|agreed\s*:)\b",
    re.IGNORECASE,
)
# Never persist credentials or identifiers.
_SENSITIVE_RE = re.compile(
    r"(password|passcode|api[\s_-]?key|secret|token|pin\b|otp|iban|card\s+number|\b\d{6,}\b|"
    r"[\w.+-]+@[\w-]+\.[\w.]+|sk-[a-z0-9]{8,})",
    re.IGNORECASE,
)
_LEAD_IN_RE = re.compile(
    r"^(?:ok(?:ay)?|so|also|and|but|right|quick\s+one|note|fyi|something\s+for\s+you\s+to\s+remember)[\s,:;.-]+",
    re.IGNORECASE,
)
MAX_ENTRY_CHARS = 280
MAX_PROMPT_ENTRIES = 12
MAX_PROMPT_CHARS = 1800


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
    return [p.strip() for p in parts if p and p.strip()]


def _clean(sentence: str) -> str:
    s = sentence.strip().strip('"').strip()
    for _ in range(3):
        s2 = _LEAD_IN_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    s = re.sub(r"^(?:please\s+)?remember(?:\s+that)?[\s,:]*", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^that\s+", "", s, flags=re.IGNORECASE).strip()
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s[:MAX_ENTRY_CHARS]


def extract_memories(text: str) -> list[tuple[str, str]]:
    """(kind, sentence) pairs worth remembering; kind is 'user' or 'memory'."""
    out: list[tuple[str, str]] = []
    raw = str(text or "")
    if not raw.strip() or len(raw) > 4000:
        return out
    # "remember: X" / "something to remember: X" spans the colon into the next sentence.
    joined = re.sub(r"(remember|note)\s*:\s*", r"\1 that ", raw, flags=re.IGNORECASE)
    for sentence in _sentences(joined):
        if sentence.endswith("?") or len(sentence) < 12:
            continue
        if _SENSITIVE_RE.search(sentence):
            continue
        kind = "memory" if _DECISION_RE.search(sentence) else ("user" if _PREFERENCE_RE.search(sentence) else "")
        if not kind:
            continue
        cleaned = _clean(sentence)
        if len(cleaned) >= 12:
            out.append((kind, cleaned))
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


def _entries(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    sections = [s.strip() for s in re.split(r"^\s*---\s*$", raw, flags=re.MULTILINE)]
    out = []
    for sec in sections:
        lines = [l for l in sec.splitlines() if l.strip() and not l.lstrip().startswith("#")
                 and not l.strip().startswith("(No ")]
        if lines:
            out.append(" ".join(l.strip() for l in lines))
    return out


def _similar(a: str, b: str) -> bool:
    na, nb = set(_norm(a).split()), set(_norm(b).split())
    if not na or not nb:
        return False
    return len(na & nb) / max(1, min(len(na), len(nb))) >= 0.8


def capture(text: str, *, chat_id: int | None = None, root: Path | None = None) -> list[str]:
    """Append new preferences/decisions from one owner message. Returns what was saved."""
    found = extract_memories(text)
    if not found:
        return []
    try:
        from distr.core.memory.files import append_markdown_section, ensure_memory_files, memory_paths

        ensure_memory_files(root=root)
        paths = memory_paths(root)
    except Exception:
        logger.debug("Memory capture unavailable", exc_info=True)
        return []
    saved: list[str] = []
    stamp = datetime.now().strftime("%Y-%m-%d")
    for kind, sentence in found:
        existing = _entries(paths["user" if kind == "user" else "memory"])
        if any(_similar(sentence, e) for e in existing):
            continue
        suffix = f" _(said {stamp}{f', chat {chat_id}' if chat_id else ''})_"
        try:
            append_markdown_section(kind, f"- {sentence}{suffix}", root=root)
            saved.append(sentence)
            logger.info("Memory captured (%s): %s", "preference" if kind == "user" else "decision", sentence[:120])
        except Exception:
            logger.warning("Memory capture write failed", exc_info=True)
    return saved


def remembered_entries(root: Path | None = None) -> list[str]:
    try:
        from distr.core.memory.files import memory_paths

        paths = memory_paths(root)
    except Exception:
        return []
    entries = _entries(paths["user"]) + _entries(paths["memory"])
    return entries[-MAX_PROMPT_ENTRIES:]


def memory_prompt_block(entries: Iterable[str] | None = None) -> str:
    items = list(entries if entries is not None else remembered_entries())
    if not items:
        return ""
    lines: list[str] = []
    total = 0
    for item in reversed(items):  # newest first within the budget
        line = "- " + item.lstrip("- ").strip()
        if total + len(line) > MAX_PROMPT_CHARS:
            break
        lines.append(line)
        total += len(line)
    lines.reverse()
    return (
        "## What you remember from earlier chats\n"
        "The owner told you these in previous conversations. Apply them without being asked. "
        "When your answer relies on one, say so in a few words (for example \"You told me earlier you want …\"), "
        "so the owner can see which memory you used. If one is out of date, follow the owner's newer instruction.\n"
        + "\n".join(lines)
    )
