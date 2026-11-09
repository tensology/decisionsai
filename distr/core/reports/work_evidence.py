"""Redacted read of recorded completions for explicit ids inside one window.

Ticket titles, prompts, payloads, and provider file text are not outcomes.
A row with no recorded completion stays unknown.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from distr.core.reports.projection_store import ProjectionStoreError
from distr.core.reports.time_window import window_bounds

_ID_RE = re.compile(r"^(execution|automation|workflow|time|chat|pi_local|codex_local):([A-Za-z0-9_-]{1,128})$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_COMPLETIONS = frozenset({
    "tool_completed",
    "cli_completed",
    "execution_session_completed",
    "workflow_run_completed",
})
_MAX_IDS = 50


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _sha(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _overlaps(start: datetime | None, end: datetime | None, lo: datetime, hi: datetime) -> bool:
    if start is None:
        return False
    finish = end or start
    return start < hi and finish > lo


def _item(ref: str, *, project_id, provenance: str, started, ended, outcome, source_sha256: str, session_root: str | None = None) -> dict:
    unknown = outcome is None
    return {
        "ref": ref,
        "project_id": project_id,
        "project_provenance": provenance,
        "session_root": session_root,
        "timestamp_bounds": {"started_at": _stamp(started), "ended_at": _stamp(ended)},
        "outcome": outcome,
        "unknown": unknown,
        "source_sha256": source_sha256,
        "read_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
    }


def _completion(event_type: str, status: str | None, event_id: int, tool_name: str | None = None) -> dict | None:
    if event_type not in _COMPLETIONS and not (event_type == "tool_result" and status == "completed"):
        return None
    if event_type in _COMPLETIONS and status not in {None, "completed", "complete", "success"}:
        return None
    outcome = {"kind": "recorded_completion", "event_type": event_type, "status": status or "completed", "event_id": event_id}
    if tool_name and _TOOL_RE.fullmatch(tool_name):
        outcome["tool_name"] = tool_name
    return outcome


def _execution(db, ref: str, ident: str, lo: datetime, hi: datetime) -> dict:
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    row = db.get(ProjectExecutionSession, int(ident))
    if row is None or not _overlaps(row.started_at, row.completed_at or row.updated_at, lo, hi):
        return _item(ref, project_id=None, provenance="unknown", started=None, ended=None, outcome=None, source_sha256=_sha({"ref": ref, "missing": True}))
    events = (
        db.query(ProjectExecutionEvent.id, ProjectExecutionEvent.event_type, ProjectExecutionEvent.status, ProjectExecutionEvent.created_at)
        .filter(ProjectExecutionEvent.session_id == row.id, ProjectExecutionEvent.created_at < hi, ProjectExecutionEvent.created_at >= lo)
        .order_by(ProjectExecutionEvent.id.asc())
        .all()
    )
    outcome = None
    for event_id, event_type, status, _created in events:
        outcome = _completion(str(event_type or ""), status, int(event_id))
        if outcome:
            break
    project_id = int(row.project_id) if row.project_id else None
    canonical = {
        "id": int(row.id), "project_id": project_id, "status": row.status,
        "started_at": _stamp(row.started_at), "completed_at": _stamp(row.completed_at),
        "events": [(int(event_id), str(event_type or ""), status) for event_id, event_type, status, _created in events],
    }
    return _item(ref, project_id=project_id, provenance="stored_on_row" if project_id else "unknown",
                 started=row.started_at, ended=row.completed_at or row.updated_at, outcome=outcome, source_sha256=_sha(canonical))


def _automation(db, ref: str, ident: str, lo: datetime, hi: datetime) -> dict:
    from distr.core.db.automation import Automation, AutomationRun
    row = db.get(AutomationRun, int(ident))
    if row is None or not _overlaps(row.started_at, row.completed_at, lo, hi):
        return _item(ref, project_id=None, provenance="unknown", started=None, ended=None, outcome=None, source_sha256=_sha({"ref": ref, "missing": True}))
    definition = db.get(Automation, int(row.automation_id)) if row.automation_id else None
    project_id = int(definition.project_id) if definition is not None and definition.project_id else None
    outcome = None
    canonical = {"id": int(row.id), "project_id": project_id, "status": row.status, "started_at": _stamp(row.started_at), "completed_at": _stamp(row.completed_at)}
    return _item(
        ref, project_id=project_id, provenance="stored_on_row" if project_id else "unknown",
        started=row.started_at, ended=row.completed_at, outcome=outcome, source_sha256=_sha(canonical),
    )


def _workflow(db, ref: str, ident: str, lo: datetime, hi: datetime) -> dict:
    from distr.core.db import Chat
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    row = db.get(AutoWorkflowRun, int(ident))
    if row is None or not _overlaps(row.started_at, row.completed_at, lo, hi):
        return _item(ref, project_id=None, provenance="unknown", started=None, ended=None, outcome=None, source_sha256=_sha({"ref": ref, "missing": True}))
    definition = db.get(AutoWorkflow, int(row.workflow_id)) if row.workflow_id else None
    project_id, provenance = None, "unknown"
    if definition is not None and definition.chat_id:
        chat = db.get(Chat, int(definition.chat_id))
        if chat is not None and chat.project_id:
            project_id, provenance = int(chat.project_id), "parent_chat"
    events = (
        db.query(OrchestratorEvent.id, OrchestratorEvent.event_type, OrchestratorEvent.status)
        .filter(OrchestratorEvent.run_id == row.id, OrchestratorEvent.created_at < hi, OrchestratorEvent.created_at >= lo)
        .order_by(OrchestratorEvent.id.asc())
        .all()
    )
    outcome = None
    for event_id, event_type, status in events:
        outcome = _completion(str(event_type or ""), status, int(event_id))
        if outcome:
            break
    canonical = {"id": int(row.id), "project_id": project_id, "status": row.status, "events": [(int(a), str(b or ""), c) for a, b, c in events]}
    return _item(ref, project_id=project_id, provenance=provenance, started=row.started_at, ended=row.completed_at, outcome=outcome, source_sha256=_sha(canonical))


def _timer(db, ref: str, ident: str, lo: datetime, hi: datetime) -> dict:
    from distr.core.db import Chat
    from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
    row = db.get(DevelopmentTimeEntry, int(ident))
    if row is None or not _overlaps(row.started_at, row.ended_at, lo, hi):
        return _item(ref, project_id=None, provenance="unknown", started=None, ended=None, outcome=None, source_sha256=_sha({"ref": ref, "missing": True}))
    item = db.get(DevelopmentWorkItem, int(row.work_item_id)) if row.work_item_id else None
    project_id, provenance = None, "unknown"
    if item is not None and item.project_id:
        project_id, provenance = int(item.project_id), "work_item"
    else:
        seen: set[int] = set()
        current = db.get(Chat, int(row.chat_id)) if row.chat_id else None
        first = True
        while current is not None and int(current.id) not in seen:
            seen.add(int(current.id))
            if current.project_id:
                project_id, provenance = int(current.project_id), "chat" if first else "parent_chat"
                break
            first = False
            current = db.get(Chat, current.parent_id) if current.parent_id else None
    # A chat completion somewhere in the window is not proof that this timer interval was worked.
    canonical = {"id": int(row.id), "project_id": project_id, "source": row.source, "started_at": _stamp(row.started_at), "ended_at": _stamp(row.ended_at)}
    return _item(ref, project_id=project_id, provenance=provenance, started=row.started_at, ended=row.ended_at, outcome=None, source_sha256=_sha(canonical))


def _provider_session(ref: str, ident: str, lo: datetime, hi: datetime, *, root, kind: str, get_session) -> dict:
    from distr.core.reports.month import _TS_RE, _parse_ts, _session_activity, _session_files
    from distr.core.reports.projection_store import _cwd_from_bytes, _hash_file, _resolved_provider, _unique_folder_project
    if root is None or not Path(root).is_dir():
        return _item(ref, project_id=None, provenance="unknown", started=None, ended=None, outcome=None, source_sha256=_sha({"ref": ref, "missing": True}))
    found = None
    for path in _session_files(root, lo, kind=kind):
        meta, _stamps = _session_activity(path, kind=kind)
        if str(meta.get("id") or path.stem) == ident:
            found = path
            break
    if found is None:
        return _item(ref, project_id=None, provenance="unknown", started=None, ended=None, outcome=None, source_sha256=_sha({"ref": ref, "missing": True}))
    resolved = _resolved_provider(found, root)
    digest, _length = _hash_file(resolved)
    source = "codex_local" if kind == "codex" else "pi_local"
    marks = []
    head = b""
    with resolved.open("rb") as handle:
        first = True
        for line in handle:
            if first:
                head = line[:65536]
                first = False
            if len(line) > 65536:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            role = message.get("role") or obj.get("role") or obj.get("type")
            if role not in {"tool_result", "tool_completed"}:
                continue
            match = _TS_RE.search(line[:400].decode("utf-8", "replace"))
            stamp = _parse_ts(match.group(1)) if match else None
            if stamp is None or not _overlaps(stamp, stamp, lo, hi):
                continue
            name = message.get("name") or message.get("tool_name") or obj.get("tool_name")
            marks.append((stamp, name if isinstance(name, str) and _TOOL_RE.fullmatch(name) else None))
    session_root = _cwd_from_bytes(head, source) or None
    project_id, provenance = _unique_folder_project(get_session, session_root or "")
    outcome = None
    if marks:
        outcome = {"kind": "recorded_completion", "event_type": "tool_result", "status": "completed", "event_id": len(marks)}
        if marks[0][1]:
            outcome["tool_name"] = marks[0][1]
    started = marks[0][0] if marks else None
    ended = marks[-1][0] if marks else None
    return _item(
        ref, project_id=project_id, provenance=provenance, started=started, ended=ended,
        outcome=outcome, source_sha256=digest, session_root=session_root,
    )


def read_work_evidence(get_session, *, start, end, ids: list[str], pi_root=None, codex_root=None) -> dict:
    if not isinstance(ids, list) or not ids or len(ids) > _MAX_IDS:
        raise ProjectionStoreError(400, "invalid_id", "ids must list 1 to 50 explicit refs")
    parsed = []
    for ref in ids:
        if not isinstance(ref, str) or "/" in ref or ".." in ref or not _ID_RE.fullmatch(ref):
            raise ProjectionStoreError(400, "invalid_id", "id is not an explicit source ref")
        parsed.append(ref)
    home = Path.home()
    if pi_root is None:
        pi_root = home / ".pi" / "agent" / "sessions"
    if codex_root is None:
        codex_root = home / ".codex" / "sessions"
    lo, hi = window_bounds(start, end)
    items = []
    with get_session() as db:
        for ref in parsed:
            kind, ident = ref.split(":", 1)
            if kind == "execution":
                items.append(_execution(db, ref, ident, lo, hi))
            elif kind == "automation":
                items.append(_automation(db, ref, ident, lo, hi))
            elif kind == "workflow":
                items.append(_workflow(db, ref, ident, lo, hi))
            elif kind in {"time", "chat"}:
                items.append(_timer(db, ref, ident, lo, hi))
            elif kind == "pi_local":
                items.append(_provider_session(ref, ident, lo, hi, root=pi_root, kind="pi", get_session=get_session))
            else:
                items.append(_provider_session(ref, ident, lo, hi, root=codex_root, kind="codex", get_session=get_session))
    return {"start": start.isoformat(), "end": end.isoformat(), "count": len(items), "items": items}
