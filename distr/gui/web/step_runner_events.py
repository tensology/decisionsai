"""
Shared state for Step Runner UI updates.

When the agent records a tool execution or step runner data changes,
the counter increments so the web UI can poll and refresh.
"""
import threading

_step_runner_update_counter = 0
_pending_single_step = None
_lock = threading.Lock()


def increment_step_runner_updated() -> None:
    """Call when Step Runner data changes (audit step, session update, etc.)."""
    global _step_runner_update_counter
    with _lock:
        _step_runner_update_counter += 1


def get_step_runner_update_counter() -> int:
    """Return current counter for web UI polling."""
    with _lock:
        return _step_runner_update_counter


def set_pending_single_step(payload: dict) -> None:
    """Set pending single-step execution state."""
    global _pending_single_step
    with _lock:
        _pending_single_step = payload


def get_pending_single_step() -> dict:
    """Get pending single-step execution state."""
    with _lock:
        return dict(_pending_single_step) if _pending_single_step else {}


def clear_pending_single_step() -> None:
    """Clear pending single-step execution state."""
    global _pending_single_step
    with _lock:
        _pending_single_step = None
