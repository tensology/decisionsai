"""Pick up / handoff protocol — filesystem continuity across harnesses."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    ACTIVE_FILE,
    HANDOFF_FILE,
    LEDGER_FILE,
    PICKUP_FILE,
    companion_memory_file,
    companion_root,
)

_PICKUP_RE = re.compile(r"\bpick\s*up\b", re.IGNORECASE)
_HANDOFF_RE = re.compile(r"\bhandoff\b", re.IGNORECASE)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_pickup_keyword(text: str) -> bool:
    return bool(_PICKUP_RE.search(text or ""))


def is_handoff_keyword(text: str) -> bool:
    return bool(_HANDOFF_RE.search(text or ""))


def _handoff_path(entity_type: str, entity_id: int | str) -> Path:
    return companion_memory_file(entity_type, entity_id, HANDOFF_FILE)  # type: ignore[arg-type]


def _active_path(entity_type: str, entity_id: int | str) -> Path:
    return companion_memory_file(entity_type, entity_id, ACTIVE_FILE)  # type: ignore[arg-type]


def _ledger_path(entity_type: str, entity_id: int | str) -> Path:
    return companion_memory_file(entity_type, entity_id, LEDGER_FILE)  # type: ignore[arg-type]


def read_text_file(path: Path, max_chars: int = 8000) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def read_handoff_preview(entity_type: str, entity_id: int | str, *, max_chars: int = 500) -> str:
    text = read_text_file(_handoff_path(entity_type, entity_id), max_chars=max_chars)
    if not text or "_No handoff recorded yet_" in text:
        return ""
    return " ".join(text.split())[:max_chars]


def append_ledger(
    entity_type: str,
    entity_id: int | str,
    *,
    event_type: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> None:
    path = _ledger_path(entity_type, entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _utc_now_iso(),
        "event_type": event_type,
        "message": (message or "").strip()[:2000],
        **(extra or {}),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_handoff(
    entity_type: str,
    entity_id: int | str,
    *,
    body: str,
    source: str = "handoff",
    extra: dict[str, Any] | None = None,
) -> str:
    """Write handoff.md and append ledger. Returns written path."""
    path = _handoff_path(entity_type, entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (body or "").strip()
    if not content:
        content = "_Empty handoff._"
    stamped = f"# Handoff\n\n{content}\n\n_updated: {_utc_now_iso()}_\n"
    path.write_text(stamped, encoding="utf-8")
    append_ledger(entity_type, entity_id, event_type=source, message=content[:500], extra=extra)
    _sync_projection_after_handoff(entity_type, entity_id, extra=extra)
    return str(path)


def _sync_projection_after_handoff(
    entity_type: str,
    entity_id: int | str,
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """Mirror handoff into active project repo when linked."""
    project_id = None
    if entity_type == "projects":
        project_id = int(entity_id)
    elif extra and extra.get("project_id"):
        project_id = int(extra["project_id"])
    elif entity_type == "tickets":
        decisions = load_decisions_json(entity_type, entity_id)
        linked = decisions.get("linked_project_id")
        if linked:
            project_id = int(linked)
    if not project_id:
        return
    try:
        from .sync import sync_projection_for_project

        sync_projection_for_project(project_id)
    except Exception:
        pass


def write_active(entity_type: str, entity_id: int | str, body: str) -> str:
    path = _active_path(entity_type, entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (body or "").strip() or "_No active notes._"
    stamped = f"# Active state\n\n{content}\n\n_updated: {_utc_now_iso()}_\n"
    path.write_text(stamped, encoding="utf-8")
    return str(path)


def build_pickup_brief(
    *,
    entity_type: str,
    entity_id: int | str,
    decisions: dict[str, Any] | None = None,
    title: str = "",
) -> str:
    """Assemble read-only continuity brief for pick up codeword."""
    lines = ["# Pick up brief", ""]
    if title:
        lines.append(f"## Focus: {title}")
        lines.append("")
    if decisions:
        lines.append("## Linked entities")
        for key in ("project_id", "board_id", "workflow_id", "run_id", "ticket_id", "step_id"):
            if decisions.get(key):
                lines.append(f"- {key}: {decisions[key]}")
        lines.append("")
    handoff = read_text_file(_handoff_path(entity_type, entity_id))
    active = read_text_file(_active_path(entity_type, entity_id))
    if handoff:
        lines.extend(["## Handoff", "", handoff, ""])
    if active:
        lines.extend(["## Active", "", active, ""])
    lines.append("_Read-only pickup. Do not change files unless the user also says to proceed._")
    return "\n".join(lines)


def perform_handoff(
    entity_type: str,
    entity_id: int | str,
    *,
    summary: str,
    source: str = "handoff",
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    path = write_handoff(entity_type, entity_id, body=summary, source=source, extra=extra)
    return {"handoff_path": path, "summary": summary[:500]}


def read_ledger_tail(entity_type: str, entity_id: int | str, *, limit: int = 20) -> list[dict[str, Any]]:
    path = _ledger_path(entity_type, entity_id)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            out.append({"message": line})
    return out


def load_decisions_json(entity_type: str, entity_id: int | str) -> dict[str, Any]:
    path = companion_root(entity_type, entity_id) / "decisions.json"  # type: ignore[arg-type]
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_pickup_brief(entity_type: str, entity_id: int | str, brief: str) -> str:
    path = companion_memory_file(entity_type, entity_id, PICKUP_FILE)  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(brief if brief.endswith("\n") else brief + "\n", encoding="utf-8")
    return str(path)
