"""Bounded rolling log of what the operator was doing.

Feeds the assessor so it can see *change over time* ("you've had the same ticket
open for 40 minutes", "you moved from Warp to Photoshop") rather than a single
frozen snapshot. Deliberately small and short-lived: this is working memory, not
a record. It is capped by count and by age, and an unchanged situation is not
re-logged, so an idle desk costs nothing.

Thread-safe. Never raises into a caller.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

MAX_ENTRIES = 240
MAX_AGE_S = 6 * 3600.0
_LOCK = threading.Lock()
_ENTRIES: list[dict[str, Any]] = []


def _line_of(situation: Any) -> str:
    try:
        return str(situation.get("line") or "").strip()
    except Exception:
        return ""


def _prune(now: float) -> None:
    cutoff = now - MAX_AGE_S
    while _ENTRIES and float(_ENTRIES[0].get("at") or 0.0) < cutoff:
        _ENTRIES.pop(0)
    if len(_ENTRIES) > MAX_ENTRIES:
        del _ENTRIES[: len(_ENTRIES) - MAX_ENTRIES]


def record(situation: Any, *, now: float | None = None) -> bool:
    """Append a situation. Returns True when it was new (not a repeat)."""
    if not isinstance(situation, dict):
        return False
    line = _line_of(situation)
    if not line:
        return False
    now = time.time() if now is None else now
    try:
        entry = {
            "at": float(now),
            "line": line,
            "screens": [
                {"screen": s.get("screen"), "app": str(s.get("app") or ""), "title": str(s.get("title") or "")}
                for s in (situation.get("screens") or [])
                if isinstance(s, dict) and s.get("app")
            ],
            "app": str(situation.get("app") or ""),
            "title": str(situation.get("title") or ""),
        }
    except Exception:
        logger.debug("situation_log: could not build entry", exc_info=True)
        return False
    with _LOCK:
        _prune(now)
        if _ENTRIES and _ENTRIES[-1].get("line") == line:
            return False  # unchanged: idle desk costs nothing
        _ENTRIES.append(entry)
        _prune(now)
    return True


def recent(*, window_s: float = 1800.0, limit: int = 20, now: float | None = None) -> list[dict[str, Any]]:
    """Most recent entries within ``window_s``, oldest first, at most ``limit``."""
    now = time.time() if now is None else now
    cutoff = now - max(0.0, float(window_s))
    with _LOCK:
        _prune(now)
        rows = [dict(e) for e in _ENTRIES if float(e.get("at") or 0.0) >= cutoff]
    try:
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        limit = 20
    return rows[-limit:] if limit else []


def summarise(*, window_s: float = 1800.0, limit: int = 12, now: float | None = None) -> str:
    """Compact digest for an assessor prompt. Empty when nothing was recorded."""
    rows = recent(window_s=window_s, limit=limit, now=now)
    if not rows:
        return ""
    now = time.time() if now is None else now
    parts: list[str] = []
    for entry in rows:
        ago = int(max(0.0, now - float(entry.get("at") or now)))
        apps = [s.get("app") for s in (entry.get("screens") or []) if s.get("app")]
        where = ", ".join(dict.fromkeys(apps)) if apps else str(entry.get("app") or "")
        label = where or "desktop"
        title = str(entry.get("title") or "").strip()
        suffix = f' — "{title[:60]}"' if title else ""
        parts.append(f"-{ago}s {label}{suffix}")
    return "\n".join(parts)


def clear() -> None:
    with _LOCK:
        _ENTRIES.clear()


def _reset_for_tests() -> None:
    clear()
