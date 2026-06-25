"""Structured runtime lifecycle logging for app restart/shutdown triage."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

LOG_DIR = Path.home() / ".decisions" / "logs"
RUN_DIR = Path.home() / ".decisions" / "run"
LIFECYCLE_LOG = LOG_DIR / "runtime_lifecycle.jsonl"
EXIT_INTENT = RUN_DIR / "exit_intent.json"


def _ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _sanitize(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(v) for v in value]
    return repr(value)


def append_runtime_event(event: str, **fields: Any) -> None:
    """Append one structured lifecycle event."""
    _ensure_dirs()
    payload = {
        "ts": _now_iso(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "event": event,
        **{key: _sanitize(value) for key, value in fields.items()},
    }
    with LIFECYCLE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def write_exit_intent(reason: str, *, source: str, expected_restart: bool = False, **fields: Any) -> None:
    """Persist the current app's planned shutdown reason for the next launch."""
    _ensure_dirs()
    payload = {
        "ts": _now_iso(),
        "pid": os.getpid(),
        "reason": reason,
        "source": source,
        "expected_restart": bool(expected_restart),
        **{key: _sanitize(value) for key, value in fields.items()},
    }
    EXIT_INTENT.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    append_runtime_event("exit_intent", **payload)


def read_exit_intent() -> dict[str, Any] | None:
    try:
        raw = EXIT_INTENT.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def clear_exit_intent() -> dict[str, Any] | None:
    payload = read_exit_intent()
    try:
        EXIT_INTENT.unlink(missing_ok=True)
    except Exception:
        pass
    return payload
