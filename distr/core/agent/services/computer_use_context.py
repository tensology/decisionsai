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


def clear_context() -> None:
    """Clear all stored computer-use context."""
    with _context_lock:
        _context_state["last_observation"] = None
        _context_state["last_candidate_target"] = None
        _context_state["last_action"] = None
        _set_updated_at_unlocked()
