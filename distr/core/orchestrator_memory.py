"""Durable Hermes memory and quiet machine activity helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from distr.core.db import Base, engine, get_session
from distr.core.db.orchestrator import OrchestratorMachineActivity, OrchestratorMaintenanceState, OrchestratorUserMemory
from distr.core.db.time import utc_from_timestamp_naive, utc_now_naive


WEEK_SECONDS = 7 * 24 * 60 * 60
DEFAULT_ACTIVITY_RETENTION_SECONDS = WEEK_SECONDS

_MEMORY_PREFIX_PATTERNS = (
    re.compile(r"^\s*please\s+remember\s+that\s+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*remember\s+that\s+(.+)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\s*note\s+that\s+(.+)$", re.IGNORECASE | re.DOTALL),
)
_PREFERENCE_PATTERNS = (
    re.compile(r"\bi\s+prefer\s+(.+?)(?:[.!?]|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bi(?:'d| would)\s+prefer\s+(.+?)(?:[.!?]|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bi\s+want\s+you\s+to\s+(.+?)(?:[.!?]|$)", re.IGNORECASE | re.DOTALL),
)
_GUARDRAIL_PATTERNS = (
    re.compile(
        r"\bdon't\s+(hard\s+code|pester|ask\s+the\s+same|send\s+files|send\s+screenshots|notify)\b(.+?)(?:[.!?]|$)",
        re.IGNORECASE | re.DOTALL,
    ),
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password)=)[^&\s\"']+|(bearer\s+)[a-z0-9._\-+/=]{12,}|(sk-[a-z0-9_\-]{12,})"
)


def ensure_orchestrator_memory_tables() -> None:
    """Create Hermes memory tables if the application has not bootstrapped them yet."""
    try:
        Base.metadata.create_all(
            engine,
            tables=[
                OrchestratorUserMemory.__table__,
                OrchestratorMachineActivity.__table__,
                OrchestratorMaintenanceState.__table__,
            ],
        )
    except Exception:
        return


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _now_dt(at: float | int | datetime | None = None) -> datetime:
    if isinstance(at, datetime):
        return at.replace(tzinfo=None)
    if at is not None:
        try:
            return utc_from_timestamp_naive(float(at))
        except Exception:
            pass
    return utc_now_naive()


def _ts(at: float | int | datetime | None = None) -> float:
    if isinstance(at, datetime):
        return at.timestamp()
    if at is not None:
        try:
            return float(at)
        except Exception:
            pass
    return time.time()


def _one_line(value: Any, max_len: int = 400) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _normalize_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _redact(value: Any) -> Any:
    try:
        from distr.core.orchestrator import redact_handoff_payload

        return redact_handoff_payload(value)
    except Exception:
        if isinstance(value, str):
            return _SECRET_VALUE_RE.sub(lambda match: (match.group(1) or match.group(2) or "") + "[redacted]", value)
        if isinstance(value, dict):
            return {str(key): _redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_redact(item) for item in value]
        return value


def _load_tags(raw: str | None) -> set[str]:
    tags = _json_loads(raw, [])
    if not isinstance(tags, list):
        return set()
    return {str(item).strip().lower() for item in tags if str(item).strip()}


def _dump_tags(tags: set[str] | list[str] | tuple[str, ...] | None) -> str:
    clean = {str(item).strip().lower() for item in (tags or []) if str(item).strip()}
    return json.dumps(sorted(clean), ensure_ascii=False)


def _serialize_memory(row: OrchestratorUserMemory) -> dict[str, Any]:
    return {
        "id": row.id,
        "memory_uid": row.memory_uid,
        "content": row.content or "",
        "category": row.category or "",
        "tags": sorted(_load_tags(row.tags)),
        "visibility": row.visibility or "private",
        "scope": row.scope or "global",
        "scope_id": row.scope_id,
        "project_id": row.project_id,
        "source_type": row.source_type or "",
        "source_id": row.source_id or "",
        "source_chat_id": row.source_chat_id,
        "confidence": float(row.confidence or 0),
        "evidence_count": int(row.evidence_count or 0),
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def record_user_memory(
    content: str,
    *,
    category: str = "preference",
    source_type: str = "",
    source_id: str = "",
    source_chat_id: int | None = None,
    project_id: int | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    visibility: str = "private",
    scope: str = "global",
    scope_id: int | None = None,
    confidence: float = 0.6,
    manually_added: bool = False,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """Create or update a durable user memory and emit one non-notifying Hermes event."""
    clean = _one_line(content, 1000)
    normalized = _normalize_text(clean)
    if not normalized:
        return None
    category_clean = (category or "preference").strip().lower().replace(" ", "_")
    scope_clean = (scope or "global").strip().lower() or "global"
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    tag_set = {str(item).strip().lower() for item in (tags or []) if str(item).strip()}
    memory_uid: str | None = None

    ensure_orchestrator_memory_tables()
    with get_session() as session:
        row = (
            session.query(OrchestratorUserMemory)
            .filter(OrchestratorUserMemory.content_hash == content_hash)
            .filter(OrchestratorUserMemory.category == category_clean)
            .filter(OrchestratorUserMemory.scope == scope_clean)
            .filter(OrchestratorUserMemory.scope_id == scope_id)
            .first()
        )
        if row:
            row.evidence_count = int(row.evidence_count or 0) + 1
            row.confidence = max(float(row.confidence or 0), float(confidence or 0))
            row.tags = _dump_tags(_load_tags(row.tags) | tag_set)
            row.visibility = visibility or row.visibility or "private"
            row.project_id = project_id if project_id is not None else row.project_id
            row.source_type = source_type or row.source_type or ""
            row.source_id = source_id or row.source_id or ""
            row.source_chat_id = source_chat_id if source_chat_id is not None else row.source_chat_id
            row.payload_json = _json_dumps(_redact(payload or _json_loads(row.payload_json, {})))
            row.updated_at = utc_now_naive()
        else:
            row = OrchestratorUserMemory(
                memory_uid=uuid.uuid4().hex,
                content=clean,
                normalized_content=normalized,
                content_hash=content_hash,
                category=category_clean,
                tags=_dump_tags(tag_set),
                visibility=visibility or "private",
                scope=scope_clean,
                scope_id=scope_id,
                project_id=project_id,
                source_type=source_type or "",
                source_id=source_id or "",
                source_chat_id=source_chat_id,
                confidence=float(confidence or 0.6),
                evidence_count=1,
                payload_json=_json_dumps(_redact(payload or {})),
                enabled=1,
                manually_added=1 if manually_added else 0,
            )
            session.add(row)
        session.commit()
        memory_uid = row.memory_uid

    _emit_memory_event(
        memory_uid=memory_uid,
        content=clean,
        category=category_clean,
        source_type=source_type,
        source_id=source_id,
        source_chat_id=source_chat_id,
        project_id=project_id,
    )
    return memory_uid


def _emit_memory_event(
    *,
    memory_uid: str | None,
    content: str,
    category: str,
    source_type: str,
    source_id: str,
    source_chat_id: int | None,
    project_id: int | None,
) -> None:
    if not memory_uid:
        return
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source="orchestrator_memory",
            event_type="user_memory_written",
            status="saved",
            project_id=project_id,
            summary=f"Memory saved: {content}",
            payload={
                "surface": "orchestrator_memory",
                "subtype": "user_memory_saved",
                "correlation_id": f"memory:{memory_uid}",
                "memory_uid": memory_uid,
                "category": category,
                "source_type": source_type or "",
                "source_id": source_id or "",
                "source_chat_id": source_chat_id,
                "is_workflow_attached": False,
            },
        )
    except Exception:
        return


def _memory_content_from_statement(statement: str) -> str:
    text = _one_line(statement, 500).strip(" .!?")
    lower = text.lower()
    if lower.startswith("i prefer "):
        return "Prefers " + text[9:].strip()
    if lower.startswith("i would prefer "):
        return "Prefers " + text[15:].strip()
    if lower.startswith("i'd prefer "):
        return "Prefers " + text[11:].strip()
    if lower.startswith("i want you to "):
        return "Wants the agent to " + text[14:].strip()
    return text[:1].upper() + text[1:] if text else ""


def _infer_memory_category(content: str) -> str:
    lower = str(content or "").lower()
    if any(word in lower for word in ("voice", "text", "telegram", "ask", "notify", "pester", "file", "screenshot")):
        return "communication_preference"
    return "user_preference"


def extract_memory_candidates_from_text(text: str) -> list[dict[str, Any]]:
    """Extract obvious user preference/style statements without an LLM dependency."""
    clean = _one_line(text, 1500)
    if not clean:
        return []
    candidates: list[dict[str, Any]] = []

    for pattern in _MEMORY_PREFIX_PATTERNS:
        match = pattern.match(clean)
        if not match:
            continue
        statement = _memory_content_from_statement(match.group(1))
        if statement:
            candidates.append({
                "content": statement,
                "category": _infer_memory_category(statement),
                "tags": ["explicit", "conversation"],
                "confidence": 0.78,
            })

    for pattern in _PREFERENCE_PATTERNS:
        for match in pattern.finditer(clean):
            phrase = _memory_content_from_statement("I prefer " + match.group(1) if "prefer" in pattern.pattern else match.group(0))
            if phrase:
                candidates.append({
                    "content": phrase,
                    "category": _infer_memory_category(phrase),
                    "tags": ["preference"],
                    "confidence": 0.7,
                })

    for pattern in _GUARDRAIL_PATTERNS:
        for match in pattern.finditer(clean):
            action = _one_line(" ".join(part for part in match.groups() if part), 300).strip(" .!?")
            if action:
                candidates.append({
                    "content": f"Does not want the agent to {action}",
                    "category": "engagement_guardrail",
                    "tags": ["guardrail", "communication"],
                    "confidence": 0.72,
                })

    if re.search(
        r"\bdon'?t\s+(?:want|send).{0,80}(?:daily|morning|day)\s+plan",
        clean,
        re.IGNORECASE,
    ):
        candidates.append({
            "content": "Does not want scheduled daily or morning plans sent proactively.",
            "category": "engagement_guardrail",
            "tags": ["guardrail", "daily_plan", "automation"],
            "confidence": 0.88,
        })

    if re.search(r"\bstop\s+sending\s+(?:idle\s+)?voice\s+notes?\b", clean, re.IGNORECASE):
        candidates.append({
            "content": "Does not want idle or stale-session notifications sent as voice notes.",
            "category": "engagement_guardrail",
            "tags": ["guardrail", "communication", "telegram", "voice"],
            "confidence": 0.82,
        })

    if re.search(r"\brather\s+send\s+(?:a\s+)?voice\s+note", clean, re.IGNORECASE):
        candidates.append({
            "content": "Prefers voice notes by default unless explicitly asking for text.",
            "category": "communication_preference",
            "tags": ["voice", "telegram"],
            "confidence": 0.85,
        })

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in candidates:
        content = _one_line(item.get("content") or "", 500).strip(" .")
        norm = _normalize_text(content)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        item["content"] = content + "."
        unique.append(item)
    return unique[:5]


def extract_and_record_user_memories_from_text(
    text: str,
    *,
    source_type: str = "chat",
    source_id: str = "",
    source_chat_id: int | None = None,
    project_id: int | None = None,
) -> list[str]:
    memory_ids: list[str] = []
    for candidate in extract_memory_candidates_from_text(text):
        memory_id = record_user_memory(
            str(candidate.get("content") or ""),
            category=str(candidate.get("category") or "user_preference"),
            source_type=source_type,
            source_id=source_id,
            source_chat_id=source_chat_id,
            project_id=project_id,
            tags=list(candidate.get("tags") or []),
            confidence=float(candidate.get("confidence") or 0.6),
        )
        if memory_id:
            memory_ids.append(memory_id)
    return memory_ids


def list_user_memories(
    *,
    category: str | None = None,
    limit: int = 100,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    ensure_orchestrator_memory_tables()
    with get_session() as session:
        query = session.query(OrchestratorUserMemory)
        if category:
            query = query.filter(OrchestratorUserMemory.category == category.strip().lower().replace(" ", "_"))
        if not include_disabled:
            query = query.filter(OrchestratorUserMemory.enabled == 1)
        rows = (
            query.order_by(OrchestratorUserMemory.updated_at.desc(), OrchestratorUserMemory.id.desc())
            .limit(max(1, min(int(limit or 100), 500)))
            .all()
        )
        return [_serialize_memory(row) for row in rows]


def set_user_memory_enabled(memory_uid: str, enabled: bool) -> bool:
    """Enable or disable a memory without deleting its evidence trail."""
    uid = str(memory_uid or "").strip()
    if not uid:
        return False
    ensure_orchestrator_memory_tables()
    with get_session() as session:
        row = session.query(OrchestratorUserMemory).filter(OrchestratorUserMemory.memory_uid == uid).first()
        if not row:
            return False
        row.enabled = 1 if enabled else 0
        row.updated_at = utc_now_naive()
        session.commit()
    return True


def _serialize_activity(row: OrchestratorMachineActivity) -> dict[str, Any]:
    return {
        "id": row.id,
        "activity_uid": row.activity_uid,
        "surface": row.surface or "",
        "app_name": row.app_name or "",
        "window_title": row.window_title or "",
        "workspace_path": row.workspace_path or "",
        "project_id": row.project_id,
        "summary": row.summary or "",
        "metadata": _json_loads(row.metadata_json, {}),
        "metadata_json": row.metadata_json or "",
        "evidence_count": int(row.evidence_count or 0),
        "compacted": bool(row.compacted),
        "captured_at": row.captured_at.isoformat() if row.captured_at else "",
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else "",
    }


def record_machine_activity(
    *,
    surface: str,
    app_name: str = "",
    window_title: str = "",
    workspace_path: str = "",
    project_id: int | None = None,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
    at: float | int | datetime | None = None,
    record_presence: bool = True,
) -> str | None:
    """Record a quiet local activity sample without notifying the user."""
    surface_clean = (surface or "desktop").strip().lower() or "desktop"
    app_clean = _one_line(app_name, 120)
    title_clean = _one_line(window_title, 260)
    workspace_clean = str(workspace_path or "").strip()
    summary_clean = _one_line(summary or title_clean or app_clean or surface_clean, 500)
    captured = _now_dt(at)
    redacted_metadata = _redact(metadata or {})
    metadata_json = _json_dumps(redacted_metadata)
    content_hash = _hash({
        "surface": surface_clean,
        "app_name": app_clean,
        "window_title": title_clean,
        "workspace_path": workspace_clean,
        "project_id": project_id,
        "summary": summary_clean,
        "metadata": redacted_metadata,
    })
    activity_uid: str | None = None

    ensure_orchestrator_memory_tables()
    with get_session() as session:
        row = (
            session.query(OrchestratorMachineActivity)
            .filter(OrchestratorMachineActivity.content_hash == content_hash)
            .filter(OrchestratorMachineActivity.compacted == 0)
            .first()
        )
        is_new = row is None
        if row:
            row.evidence_count = int(row.evidence_count or 0) + 1
            row.last_seen_at = captured
            row.metadata_json = metadata_json
            row.updated_at = utc_now_naive()
        else:
            row = OrchestratorMachineActivity(
                activity_uid=uuid.uuid4().hex,
                surface=surface_clean,
                app_name=app_clean,
                window_title=title_clean,
                workspace_path=workspace_clean,
                project_id=project_id,
                summary=summary_clean,
                metadata_json=metadata_json,
                content_hash=content_hash,
                evidence_count=1,
                compacted=0,
                captured_at=captured,
                last_seen_at=captured,
            )
            session.add(row)
        session.commit()
        activity_uid = row.activity_uid

    if record_presence:
        try:
            from distr.core.notification_routing import record_surface_activity

            record_surface_activity(
                surface_clean,
                at=_ts(at),
                metadata={
                    "app_name": app_clean,
                    "window_title": title_clean,
                    "workspace_path": workspace_clean,
                    "project_id": project_id,
                },
            )
        except Exception:
            pass
    if is_new:
        _emit_machine_activity_event(
            activity_uid=activity_uid,
            surface=surface_clean,
            project_id=project_id,
            summary=summary_clean,
            workspace_path=workspace_clean,
        )
    return activity_uid


def _emit_machine_activity_event(
    *,
    activity_uid: str | None,
    surface: str,
    project_id: int | None,
    summary: str,
    workspace_path: str,
) -> None:
    if not activity_uid:
        return
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source="machine_activity",
            event_type="machine_activity_recorded",
            status="observed",
            project_id=project_id,
            summary=summary,
            payload={
                "surface": surface,
                "subtype": "machine_activity_sample",
                "correlation_id": f"activity:{activity_uid}",
                "activity_uid": activity_uid,
                "workspace_path": workspace_path,
                "is_workflow_attached": False,
            },
        )
    except Exception:
        return


def list_machine_activity(
    *,
    surface: str | None = None,
    limit: int = 100,
    include_compacted: bool = True,
) -> list[dict[str, Any]]:
    ensure_orchestrator_memory_tables()
    with get_session() as session:
        query = session.query(OrchestratorMachineActivity)
        if surface:
            query = query.filter(OrchestratorMachineActivity.surface == surface.strip().lower())
        if not include_compacted:
            query = query.filter(OrchestratorMachineActivity.compacted == 0)
        rows = (
            query.order_by(OrchestratorMachineActivity.last_seen_at.desc(), OrchestratorMachineActivity.id.desc())
            .limit(max(1, min(int(limit or 100), 1000)))
            .all()
        )
        return [_serialize_activity(row) for row in rows]


def compact_machine_activity(
    *,
    now: float | int | datetime | None = None,
    older_than_s: float = DEFAULT_ACTIVITY_RETENTION_SECONDS,
) -> dict[str, Any]:
    """Compact old detailed activity rows into daily summaries."""
    cutoff = _now_dt(_ts(now) - float(older_than_s or DEFAULT_ACTIVITY_RETENTION_SECONDS))
    ensure_orchestrator_memory_tables()
    compacted_rows = 0
    summary_rows = 0
    with get_session() as session:
        rows = (
            session.query(OrchestratorMachineActivity)
            .filter(OrchestratorMachineActivity.compacted == 0)
            .filter(OrchestratorMachineActivity.last_seen_at < cutoff)
            .order_by(OrchestratorMachineActivity.last_seen_at.asc(), OrchestratorMachineActivity.id.asc())
            .all()
        )
        groups: dict[tuple[Any, ...], list[OrchestratorMachineActivity]] = defaultdict(list)
        for row in rows:
            day = (row.captured_at or row.last_seen_at or utc_now_naive()).date().isoformat()
            groups[(day, row.surface or "", row.app_name or "", row.workspace_path or "", row.project_id)].append(row)

        for (day, surface, app_name, workspace_path, project_id), group in groups.items():
            if not group:
                continue
            evidence_count = sum(max(1, int(row.evidence_count or 1)) for row in group)
            sample_titles = [
                title
                for title in (_one_line(row.window_title or row.summary or "", 140) for row in group[:10])
                if title
            ]
            summary = (
                f"{app_name or surface or 'Machine'} activity summary for {day}: "
                f"{evidence_count} activity sample{'s' if evidence_count != 1 else ''}."
            )
            summary_payload = {
                "day": day,
                "surface": surface,
                "app_name": app_name,
                "workspace_path": workspace_path,
                "sample_titles": sample_titles,
                "compacted_from_count": len(group),
            }
            content_hash = _hash({"compacted": True, **summary_payload})
            existing = (
                session.query(OrchestratorMachineActivity)
                .filter(OrchestratorMachineActivity.content_hash == content_hash)
                .filter(OrchestratorMachineActivity.compacted == 1)
                .first()
            )
            if existing:
                existing.evidence_count = int(existing.evidence_count or 0) + evidence_count
                existing.metadata_json = _json_dumps(summary_payload)
                existing.last_seen_at = max((row.last_seen_at for row in group if row.last_seen_at), default=existing.last_seen_at)
                existing.updated_at = utc_now_naive()
            else:
                existing = OrchestratorMachineActivity(
                    activity_uid="compact:" + uuid.uuid4().hex,
                    surface=surface or "desktop",
                    app_name=app_name or "",
                    window_title="",
                    workspace_path=workspace_path or "",
                    project_id=project_id,
                    summary=summary,
                    metadata_json=_json_dumps(summary_payload),
                    content_hash=content_hash,
                    evidence_count=evidence_count,
                    compacted=1,
                    captured_at=min((row.captured_at for row in group if row.captured_at), default=utc_now_naive()),
                    last_seen_at=max((row.last_seen_at for row in group if row.last_seen_at), default=utc_now_naive()),
                )
                session.add(existing)
                summary_rows += 1
            for row in group:
                session.delete(row)
            compacted_rows += len(group)
        session.commit()
    return {"ran": True, "compacted_rows": compacted_rows, "summary_rows": summary_rows}


def run_weekly_machine_activity_compaction(
    *,
    now: float | int | datetime | None = None,
    interval_s: float = WEEK_SECONDS,
    older_than_s: float = DEFAULT_ACTIVITY_RETENTION_SECONDS,
) -> dict[str, Any]:
    """Run machine activity compaction at most once per interval."""
    now_ts = _ts(now)
    ensure_orchestrator_memory_tables()
    with get_session() as session:
        state = (
            session.query(OrchestratorMaintenanceState)
            .filter(OrchestratorMaintenanceState.key == "machine_activity_weekly_compaction")
            .first()
        )
        payload = _json_loads(state.value_json if state else None, {})
        last_run_at = float(payload.get("last_run_at") or 0) if isinstance(payload, dict) else 0.0
        if last_run_at and now_ts - last_run_at < float(interval_s or WEEK_SECONDS):
            return {
                "ran": False,
                "last_run_at": last_run_at,
                "next_allowed_at": last_run_at + float(interval_s or WEEK_SECONDS),
            }

    result = compact_machine_activity(now=now_ts, older_than_s=older_than_s)

    with get_session() as session:
        state = (
            session.query(OrchestratorMaintenanceState)
            .filter(OrchestratorMaintenanceState.key == "machine_activity_weekly_compaction")
            .first()
        )
        value = {"last_run_at": now_ts, "result": result}
        if state:
            state.value_json = _json_dumps(value)
            state.updated_at = utc_now_naive()
        else:
            session.add(OrchestratorMaintenanceState(key="machine_activity_weekly_compaction", value_json=_json_dumps(value)))
        session.commit()
    return {"ran": True, **result}


def build_memory_context(
    limit: int = 30,
    *,
    project_id: int | None = None,
    board_id: int | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
) -> str:
    memories = list_user_memories(limit=limit * 3, include_disabled=False)
    if not memories:
        return ""

    def _matches(memory: dict[str, Any]) -> bool:
        scope = (memory.get("scope") or "global").strip().lower()
        scope_id = memory.get("scope_id")
        if scope == "global" or not scope:
            return True
        if scope == "project" and project_id and scope_id == project_id:
            return True
        if scope == "board" and board_id and scope_id == board_id:
            return True
        if scope == "workflow" and workflow_id and scope_id == workflow_id:
            return True
        if scope == "run" and run_id and scope_id == run_id:
            return True
        mem_project_id = memory.get("project_id")
        if mem_project_id and project_id and mem_project_id == project_id:
            return True
        return False

    filtered = [m for m in memories if _matches(m)]
    if not filtered and (project_id or board_id or workflow_id):
        filtered = [m for m in memories if (m.get("scope") or "global") == "global"]
    if not filtered:
        filtered = memories

    lines = ["- orchestrator_user_memory:"]
    for memory in filtered[: max(1, min(int(limit or 30), 100))]:
        lines.append(f"  - {memory['category']}: {memory['content']}")
    return "\n".join(lines)


def project_name_from_path(path: str) -> str:
    try:
        return Path(path).expanduser().resolve().name
    except Exception:
        return Path(str(path or "")).name
