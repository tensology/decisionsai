"""
Shared computer-use context memory.

This module stores the latest observation/target/action context so the
agent and delegated flows can reason across steps without re-discovering
everything from scratch on every turn.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_context_lock = Lock()
_context_state: dict[str, Any] = {
    "last_observation": None,
    "last_candidate_target": None,
    "last_action": None,
    "updated_at": None,
}


def _set_updated_at_unlocked() -> None:
    _context_state["updated_at"] = _utc_now_iso()


def record_observation(source: str, details: dict[str, Any]) -> None:
    """Store latest perception context (screenshot/tree/etc.)."""
    with _context_lock:
        _context_state["last_observation"] = {
            "source": source,
            "details": deepcopy(details),
            "recorded_at": _utc_now_iso(),
        }
        _set_updated_at_unlocked()


def record_candidate_target(
    *,
    source: str,
    x: int | None = None,
    y: int | None = None,
    screen: int | None = None,
    description: str = "",
    confidence: float | None = None,
    status: str = "found",
) -> None:
    """Store latest target candidate returned by vision/accessibility."""
    with _context_lock:
        _context_state["last_candidate_target"] = {
            "source": source,
            "status": status,
            "x": x,
            "y": y,
            "screen": screen,
            "description": description,
            "confidence": confidence,
            "recorded_at": _utc_now_iso(),
        }
        _set_updated_at_unlocked()


def record_action(action: str, status: str, details: dict[str, Any]) -> None:
    """Store latest physical action execution status."""
    with _context_lock:
        _context_state["last_action"] = {
            "action": action,
            "status": status,
            "details": deepcopy(details),
            "recorded_at": _utc_now_iso(),
        }
        _set_updated_at_unlocked()


def get_context_snapshot() -> dict[str, Any]:
    """Return a deep copy of the latest shared computer-use context."""
    with _context_lock:
        return deepcopy(_context_state)


def format_context_snapshot_for_agent(snapshot: dict[str, Any]) -> str:
    """Turn snapshot dict into readable text for chat, TTS, and LLM tool results.

    Avoids dumping raw JSON to the user while preserving coordinates and status.
    """
    lines: list[str] = []

    tgt = snapshot.get("last_candidate_target")
    if isinstance(tgt, dict) and any(tgt.get(k) is not None for k in ("x", "y", "description")):
        desc = (tgt.get("description") or "target").strip() or "target"
        x, y = tgt.get("x"), tgt.get("y")
        scr = tgt.get("screen")
        conf = tgt.get("confidence")
        bit = f"Last UI target: {desc}"
        if x is not None and y is not None:
            bit += f" at ({x}, {y})"
        if scr is not None:
            bit += f" on screen {scr}"
        if conf is not None:
            bit += (
                f" (confidence {conf:.2f})"
                if isinstance(conf, (float, int))
                else f" (confidence {conf})"
            )
        st = (tgt.get("status") or "").strip()
        if st:
            bit += f" — status: {st}"
        lines.append(bit)

    act = snapshot.get("last_action")
    if isinstance(act, dict) and act.get("action"):
        details = act.get("details")
        detail_brief = ""
        if isinstance(details, dict) and details:
            detail_brief = str(details)
            if len(detail_brief) > 300:
                detail_brief = detail_brief[:300] + "…"
        action_name = act.get("action") or "unknown"
        st = (act.get("status") or "").strip()
        line = f"Last action: {action_name}"
        if st:
            line += f" — {st}"
        if detail_brief:
            line += f". Detail: {detail_brief}"
        lines.append(line)

    obs = snapshot.get("last_observation")
    if isinstance(obs, dict):
        src = obs.get("source") or "observation"
        lines.append(f"Last observation source: {src}")
        details = obs.get("details")
        if details is not None:
            brief = details if isinstance(details, str) else str(details)
            if len(brief) > 500:
                brief = brief[:500] + "…"
            lines.append(f"Observation detail: {brief}")

    updated = snapshot.get("updated_at")
    if updated:
        lines.append(f"Context updated: {updated}")

    if not lines:
        return "No computer-use context has been recorded yet."
    return "\n".join(lines)


def clear_context() -> None:
    """Clear all stored computer-use context."""
    with _context_lock:
        _context_state["last_observation"] = None
        _context_state["last_candidate_target"] = None
        _context_state["last_action"] = None
        _context_state["updated_at"] = None
