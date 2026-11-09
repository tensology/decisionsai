"""Durable time-ledger export, delete, create, and restore.

Report block ids (``b413``) and native Pi/Codex session refs are not rows in
``development_time_entries``. This module never creates a chat or work item,
never changes a ticket counter, and never reads a client-supplied filesystem path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, String, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import declarative_base

from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
from distr.core.db import Chat

SCHEMA = "decisions-time-ledger-backup/v2"
# Separate from the application Base so a generic create_all cannot create this table.
LedgerMetadata = declarative_base()
MAX_BACKUP_AGE = timedelta(hours=24)
MAX_SPAN_SECONDS = 24 * 60 * 60
CREATE_SOURCES = frozenset({"historical", "manual"})
_ID_RE = re.compile(r"^(time|historical):([1-9][0-9]{0,17})$")
_BACKUP_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_PROJECTION_RE = re.compile(
    r"^(?:b[0-9]+|pi_local:.+|codex_local:.+|cursor_local:.+|claude_local:.+"
    r"|execution:[0-9]+|workflow_run:[0-9]+|automation_run:[0-9]+|schedule:[0-9]+|chat:[0-9]+)$"
)


class LedgerError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message

    def body(self) -> dict:
        return {"detail": {"code": self.code, "message": self.message}}


class HistoricalTimeEntry(LedgerMetadata):
    """A caller-supplied closed interval. It is not a thread timer and has no chat row."""

    __tablename__ = "development_historical_time_entries"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, nullable=True)
    source = Column(String, nullable=False)
    label = Column(String, nullable=False)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    started_at_original = Column(String, nullable=False)
    ended_at_original = Column(String, nullable=False)
    stored_seconds = Column(Integer, nullable=False)
    seconds_origin = Column(String, nullable=False)
    idempotency_key = Column(String, nullable=True, unique=True)
    request_fingerprint = Column(String, nullable=False)
    payload_key = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False)


_historical_table_drop = HistoricalTimeEntry.__table__.drop


def _drop_historical(bind=None, checkfirst=False, **kwargs):
    """Missing-table drop is a no-op. The table is created only by create or historical restore."""
    return _historical_table_drop(bind=bind, checkfirst=True, **kwargs)


HistoricalTimeEntry.__table__.drop = _drop_historical


def _ensure_execution_guard(db) -> None:
    """Reject a historical insert in the same SQLite statement that sees a matching execution row."""
    if not _has_historical_table(db) or not inspect(db.get_bind()).has_table("project_execution_sessions"):
        return
    db.execute(text(
        """
        CREATE TRIGGER IF NOT EXISTS historical_reject_same_execution
        BEFORE INSERT ON development_historical_time_entries
        FOR EACH ROW
        WHEN NEW.project_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM project_execution_sessions
            WHERE project_id = NEW.project_id
              AND started_at = NEW.started_at
              AND (
                    completed_at = NEW.ended_at
                    OR (completed_at IS NULL AND updated_at = NEW.ended_at)
                  )
        )
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_assigned_evidence');
        END
        """
    ))


def ensure_schema(get_session) -> None:
    """Create the historical table when missing. Does not alter development_time_entries or app metadata."""
    with get_session() as db:
        try:
            HistoricalTimeEntry.__table__.create(bind=db.get_bind(), checkfirst=True)
        except OperationalError as exc:
            if "already exists" not in str(exc).lower():
                raise
        _ensure_execution_guard(db)


def _has_historical_table(db) -> bool:
    return inspect(db.get_bind()).has_table(HistoricalTimeEntry.__tablename__)


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")


def _parse_stamp(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f")
    return parsed


def _aware(value: str, field: str) -> datetime:
    text_value = str(value or "").strip()
    if not text_value:
        raise LedgerError(400, "invalid_time", f"{field} is required")
    if text_value.endswith("Z"):
        text_value = text_value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError as exc:
        raise LedgerError(400, "invalid_time", f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise LedgerError(400, "invalid_time", f"{field} must include a timezone offset")
    return parsed.astimezone(UTC)


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _sha256(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def parse_durable_id(value: str) -> tuple[str, int]:
    text_value = str(value or "").strip()
    match = _ID_RE.fullmatch(text_value)
    if match:
        return match.group(1), int(match.group(2))
    if _PROJECTION_RE.fullmatch(text_value):
        raise LedgerError(
            409,
            "not_a_time_ledger_row",
            "Report projections and native Pi, Codex, Cursor, and Claude session refs are not deletable time-ledger rows. Use time:<id> or historical:<id>.",
        )
    raise LedgerError(400, "invalid_id", "durable_id must be time:<id> or historical:<id>")


def _time_fields(row: DevelopmentTimeEntry) -> dict:
    return {
        "id": int(row.id),
        "work_item_id": int(row.work_item_id),
        "chat_id": int(row.chat_id),
        "started_at": _stamp(row.started_at),
        "ended_at": _stamp(row.ended_at),
        "seconds": int(row.seconds or 0),
        "source": str(row.source or ""),
        "created_at": _stamp(row.created_at),
    }


def _historical_fields(row: HistoricalTimeEntry) -> dict:
    return {
        "id": int(row.id),
        "project_id": None if row.project_id is None else int(row.project_id),
        "source": str(row.source),
        "label": str(row.label),
        "started_at": _stamp(row.started_at),
        "ended_at": _stamp(row.ended_at),
        "started_at_original": str(row.started_at_original),
        "ended_at_original": str(row.ended_at_original),
        "stored_seconds": int(row.stored_seconds),
        "seconds_origin": str(row.seconds_origin),
        "idempotency_key": row.idempotency_key,
        "request_fingerprint": str(row.request_fingerprint),
        "payload_key": str(row.payload_key),
        "created_at": _stamp(row.created_at),
    }


def _attribution(project_id: int | None) -> dict:
    return {"project_id": project_id, "assigned": project_id is not None}


def _row_canonical(table: str, durable_id: str, fields: dict) -> dict:
    """Fingerprint covers the ledger row only. Joined project attribution is not part of it."""
    return {"table": table, "durable_id": durable_id, "fields": fields}


def _fingerprint_ok(packed: dict) -> bool:
    canonical = _row_canonical(packed.get("table"), packed.get("durable_id"), packed.get("fields"))
    return _sha256(canonical) == packed.get("fingerprint")


def _pack(kind: str, fields: dict, project_id: int | None) -> dict:
    durable_id = f"{kind}:{fields['id']}"
    canonical = _row_canonical(_table_name(kind), durable_id, fields)
    return {**canonical, "attribution": _attribution(project_id), "fingerprint": _sha256(canonical)}


def _table_name(kind: str) -> str:
    if kind == "time":
        return "development_time_entries"
    if kind == "historical":
        return "development_historical_time_entries"
    raise LedgerError(400, "invalid_id", "unknown ledger table")


def attributable_project_id(db, entry: DevelopmentTimeEntry) -> int | None:
    item = db.get(DevelopmentWorkItem, int(entry.work_item_id)) if entry.work_item_id else None
    if item is not None and item.project_id:
        return int(item.project_id)
    seen: set[int] = set()
    current = db.get(Chat, int(entry.chat_id)) if entry.chat_id else None
    while current is not None and int(current.id) not in seen:
        seen.add(int(current.id))
        if current.project_id:
            return int(current.project_id)
        current = db.get(Chat, current.parent_id) if current.parent_id else None
    return None


def _load(db, kind: str, row_id: int):
    if kind == "time":
        return db.get(DevelopmentTimeEntry, row_id)
    if not _has_historical_table(db):
        return None
    return db.get(HistoricalTimeEntry, row_id)


def _canonical_from_row(db, kind: str, row) -> dict:
    if kind == "time":
        return _pack(kind, _time_fields(row), attributable_project_id(db, row))
    project_id = None if row.project_id is None else int(row.project_id)
    return _pack(kind, _historical_fields(row), project_id)


def _counts(db) -> dict:
    bind = db.get_bind()
    names = inspect(bind).get_table_names()
    out = {}
    for name in ("chats", "development_work_items", "kanban_tickets", "development_time_entries"):
        if name in names:
            out[name] = int(db.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar() or 0)
    return out


def _backup_root(backup_dir: Path) -> Path:
    root = Path(backup_dir)
    if root.is_symlink():
        raise LedgerError(400, "path_rejected", "backup directory must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = root.resolve()
    if resolved != root.absolute() and root.exists() and root.is_symlink():
        raise LedgerError(400, "path_rejected", "backup directory must not be a symlink")
    os.chmod(resolved, 0o700)
    return resolved


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _backup_path(backup_dir: Path, backup_id: str) -> Path:
    if not _BACKUP_ID_RE.fullmatch(str(backup_id or "")):
        raise LedgerError(400, "path_rejected", "backup_id must be 32 lowercase hex characters")
    root = _backup_root(backup_dir)
    path = root / f"{backup_id}.json"
    if path.is_symlink():
        raise LedgerError(409, "backup_tampered", "backup id is a symlink and was not followed")
    if path.parent != root:
        raise LedgerError(400, "path_rejected", "backup path escaped the server backup directory")
    return path


def _write_manifest(path: Path, manifest: dict) -> dict:
    body = dict(manifest)
    body.pop("checksum", None)
    body["checksum"] = _sha256(body)
    tmp = path.with_suffix(".json.tmp")
    if tmp.is_symlink():
        raise LedgerError(409, "backup_tampered", "backup temporary path is a symlink")
    tmp.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    _fsync_file(tmp)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    _fsync_file(path)
    _fsync_dir(path.parent)
    return body


def _read_manifest(path: Path, backup_id: str) -> dict:
    if path.is_symlink():
        raise LedgerError(409, "backup_tampered", "backup id is a symlink and was not followed")
    if not path.is_file():
        raise LedgerError(409, "backup_missing", "no verified backup file for that backup_id")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError(409, "backup_tampered", "backup file is not readable JSON") from exc
    if not isinstance(loaded, dict) or loaded.get("schema") != SCHEMA:
        raise LedgerError(409, "backup_tampered", "backup schema is not decisions-time-ledger-backup/v2")
    if loaded.get("backup_id") != backup_id:
        raise LedgerError(409, "backup_tampered", "manifest backup_id does not match the requested id")
    checksum = loaded.get("checksum")
    check_body = dict(loaded)
    check_body.pop("checksum", None)
    if not isinstance(checksum, str) or checksum != _sha256(check_body):
        raise LedgerError(409, "backup_tampered", "backup checksum does not match its contents")
    return loaded


def _fresh(manifest: dict, now: datetime) -> None:
    try:
        created = _aware(str(manifest.get("created_at")), "created_at")
    except LedgerError as exc:
        raise LedgerError(409, "backup_tampered", "backup created_at is not a timezone-aware timestamp") from exc
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    age = now.astimezone(UTC) - created
    if age < timedelta(0) or age > MAX_BACKUP_AGE:
        raise LedgerError(409, "backup_stale", "backup is outside the 24 hour freshness window")


def export_entries(get_session, backup_dir: Path, durable_ids: list[str], *, now: datetime | None = None) -> dict:
    if not isinstance(durable_ids, list) or not durable_ids or len(durable_ids) > 100:
        raise LedgerError(400, "invalid_id", "durable_ids must be an explicit list of 1 to 100 ids")
    parsed = [parse_durable_id(item) for item in durable_ids]
    if len({item for item in durable_ids}) != len(durable_ids):
        raise LedgerError(400, "invalid_id", "durable_ids contains a duplicate")
    rows = []
    with get_session() as db:
        for kind, row_id in parsed:
            row = _load(db, kind, row_id)
            if row is None:
                raise LedgerError(404, "not_found", f"{kind}:{row_id} is not in the time ledger")
            rows.append(_canonical_from_row(db, kind, row))
    created = (now or datetime.now(UTC)).astimezone(UTC)
    backup_id = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    manifest = {
        "schema": SCHEMA,
        "backup_id": backup_id,
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"),
        "rows": rows,
    }
    path = _backup_path(backup_dir, backup_id)
    written = _write_manifest(path, manifest)
    return written


def read_backup(backup_dir: Path, backup_id: str) -> dict:
    return _read_manifest(_backup_path(backup_dir, backup_id), backup_id)


def _window(body: dict) -> tuple[datetime, datetime]:
    start = _aware(body.get("window_start"), "window_start")
    end = _aware(body.get("window_end"), "window_end")
    if end <= start:
        raise LedgerError(400, "invalid_time", "window_end must be after window_start")
    return start, end


def _inside(started: datetime, ended: datetime, window_start: datetime, window_end: datetime) -> bool:
    start = started.replace(tzinfo=UTC) if started.tzinfo is None else started.astimezone(UTC)
    end = ended.replace(tzinfo=UTC) if ended.tzinfo is None else ended.astimezone(UTC)
    return start >= window_start and end <= window_end


def _probe_competing_writes(get_session, kind: str, row_id: int, window_start: datetime, window_end: datetime) -> None:
    """Run attribution and window hooks once without the write lock.

    A second connection can commit during this read. The locked section below
    is the only decision that may delete, and it re-reads after this returns.
    """
    with get_session() as db:
        row = _load(db, kind, row_id)
        if row is None:
            return
        if kind == "time":
            attributable_project_id(db, row)
        _inside(row.started_at, row.ended_at, window_start, window_end)


def _begin_immediate(db) -> None:
    """Reserve the SQLite writer before the authoritative read. A Python lock cannot do this."""
    db.execute(text("BEGIN IMMEDIATE"))


def delete_entry(get_session, backup_dir: Path, body: dict, *, now: datetime | None = None) -> dict:
    if not isinstance(body, dict):
        raise LedgerError(400, "confirmation", "delete body must be a JSON object")
    if body.get("allow_assigned"):
        raise LedgerError(409, "assigned", "assigned rows cannot be deleted on this route")
    kind, row_id = parse_durable_id(body.get("durable_id"))
    durable_id = f"{kind}:{row_id}"
    if body.get("confirm") != "delete-time-entry":
        raise LedgerError(409, "confirmation", "confirm must be delete-time-entry")
    fingerprint = str(body.get("fingerprint") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise LedgerError(400, "fingerprint_mismatch", "fingerprint must be the 64 character sha256 from the backup row")
    window_start, window_end = _window(body)
    backup_path = _backup_path(backup_dir, str(body.get("backup_id") or ""))
    manifest = _read_manifest(backup_path, str(body.get("backup_id") or ""))
    _fresh(manifest, now or datetime.now(UTC))
    _fsync_file(backup_path)
    _fsync_dir(backup_path.parent)
    backed = {row.get("durable_id"): row for row in manifest.get("rows") or []}
    if durable_id not in backed:
        raise LedgerError(409, "backup_row_missing", "that durable id is not in the verified backup")
    _probe_competing_writes(get_session, kind, row_id, window_start, window_end)
    with get_session() as db:
        _begin_immediate(db)
        before = _counts(db)
        row = _load(db, kind, row_id)
        if row is None:
            raise LedgerError(404, "not_found", f"{durable_id} is not in the time ledger")
        db.expire_all()
        row = _load(db, kind, row_id)
        packed = _canonical_from_row(db, kind, row)
        backup_row = backed[durable_id]
        if packed["fingerprint"] != fingerprint or packed["fingerprint"] != backup_row.get("fingerprint"):
            raise LedgerError(409, "fingerprint_mismatch", "the stored row does not match the backup fingerprint")
        if packed["fields"] != backup_row.get("fields") or packed["attribution"] != backup_row.get("attribution"):
            raise LedgerError(409, "fingerprint_mismatch", "the stored row no longer matches the backup, including project attribution")
        if kind == "time":
            project_id = packed["attribution"]["project_id"]
            item = db.get(DevelopmentWorkItem, int(row.work_item_id)) if row.work_item_id else None
            if item is not None and item.time_paused is False:
                raise LedgerError(409, "live_timer", "the thread timer is still running; this route will not close or edit it")
            timer_before = None if item is None else (
                int(item.time_accumulated_seconds or 0),
                bool(item.time_paused),
                _stamp(item.time_started_at),
            )
        else:
            project_id = packed["attribution"]["project_id"]
            item = None
            timer_before = None
        if project_id is not None:
            raise LedgerError(409, "assigned", "the row has a project; assigned rows are not deleted")
        if not (row.started_at.replace(tzinfo=UTC) >= window_start and row.ended_at.replace(tzinfo=UTC) <= window_end):
            raise LedgerError(409, "outside_window", "the whole row must sit inside window_start and window_end")
        derived = int((row.ended_at - row.started_at).total_seconds()) if row.started_at and row.ended_at else None
        stored = int(row.seconds if kind == "time" else row.stored_seconds)
        db.delete(row)
        db.flush()
        if item is not None:
            db.refresh(item)
            timer_after = (
                int(item.time_accumulated_seconds or 0),
                bool(item.time_paused),
                _stamp(item.time_started_at),
            )
            if timer_after != timer_before:
                raise LedgerError(409, "counter_effect", "delete stopped because a timer counter changed")
        after = _counts(db)
        for name, count in before.items():
            if name == "development_time_entries":
                continue
            if after.get(name) != count:
                raise LedgerError(409, "counter_effect", f"delete stopped because {name} changed")
        if kind == "time" and after.get("development_time_entries") != before.get("development_time_entries", 0) - 1:
            raise LedgerError(409, "counter_effect", "delete stopped because the ledger count was not exactly one row")
    return {
        "deleted": True,
        "durable_id": durable_id,
        "fingerprint": fingerprint,
        "backup_id": manifest["backup_id"],
        "backup_checksum": manifest["checksum"],
        "confirm": "delete-time-entry",
        "assigned": False,
        "project_id": project_id,
        "stored_seconds": stored,
        "derived_seconds": derived,
        "effects": {
            "chats_removed": 0,
            "work_items_removed": 0,
            "ticket_rows_removed": 0,
            "timer_counter_changed": False,
            "live_segment_changed": False,
        },
    }


def _label(value) -> str:
    label = " ".join(str(value or "").split())
    if not label or len(label) > 200:
        raise LedgerError(400, "invalid_source", "label is required and must be 1 to 200 characters")
    return label


def _create_request(body: dict) -> dict:
    if not isinstance(body, dict):
        raise LedgerError(400, "invalid_time", "create body must be a JSON object")
    started = _aware(body.get("started_at"), "started_at")
    ended = _aware(body.get("ended_at"), "ended_at")
    if ended <= started:
        raise LedgerError(400, "invalid_time", "ended_at must be after started_at")
    raw_seconds = (ended - started).total_seconds()
    if raw_seconds < 1 or raw_seconds > MAX_SPAN_SECONDS:
        raise LedgerError(400, "invalid_time", "the interval must be from 1 second through 24 hours, without a fractional second past 24 hours")
    derived = int(raw_seconds)
    source = str(body.get("source") or "")
    if source not in CREATE_SOURCES:
        raise LedgerError(400, "invalid_source", "source must be historical or manual")
    label = _label(body.get("label"))
    project_id = body.get("project_id", None)
    if project_id is not None and (not isinstance(project_id, int) or isinstance(project_id, bool) or project_id <= 0):
        raise LedgerError(400, "invalid_project", "project_id must be null or an existing project id")
    if "seconds" in body and body.get("seconds") is not None:
        seconds = body.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0 or seconds > MAX_SPAN_SECONDS:
            raise LedgerError(400, "invalid_time", "seconds must be an integer from 0 through 86400")
        origin = "stored"
        stored = seconds
    else:
        origin = "derived"
        stored = derived
    key = body.get("idempotency_key", None)
    if key is not None:
        key = str(key)
        if not _KEY_RE.fullmatch(key):
            raise LedgerError(400, "idempotency_conflict", "idempotency_key must be 1 to 80 characters of letters, numbers, or . _ : -")
    fingerprint_body = {
        "started_at": str(body.get("started_at")).strip(),
        "ended_at": str(body.get("ended_at")).strip(),
        "project_id": project_id,
        "source": source,
        "label": label,
        "seconds": None if origin == "derived" else stored,
    }
    return {
        "started": _naive_utc(started),
        "ended": _naive_utc(ended),
        "started_original": str(body.get("started_at")).strip(),
        "ended_original": str(body.get("ended_at")).strip(),
        "project_id": project_id,
        "source": source,
        "label": label,
        "stored_seconds": stored,
        "derived_seconds": derived,
        "seconds_origin": origin,
        "idempotency_key": key,
        "request_fingerprint": _sha256(fingerprint_body),
        "payload_key": _payload_key(project_id, source, label, _naive_utc(started), _naive_utc(ended), stored),
    }


def _payload_key(project_id, source, label, started: datetime, ended: datetime, stored_seconds: int) -> str:
    return _sha256({
        "project_id": project_id,
        "source": source,
        "label": label,
        "started_at": _stamp(started),
        "ended_at": _stamp(ended),
        "stored_seconds": int(stored_seconds),
    })


def _execution_interval_exists(db, project_id: int | None, started: datetime, ended: datetime) -> bool:
    """True when Decisions already stored an execution session for this project and exact interval.

    This does not read Pi, Codex, Cursor, or Claude session files.
    """
    if project_id is None:
        return False
    try:
        from distr.core.db.kanban import ProjectExecutionSession
    except Exception:
        return False
    table = getattr(ProjectExecutionSession, "__tablename__", "project_execution_sessions")
    if not inspect(db.get_bind()).has_table(table):
        return False
    rows = db.query(ProjectExecutionSession).filter(ProjectExecutionSession.project_id == project_id).all()
    for row in rows:
        row_start = getattr(row, "started_at", None)
        row_end = getattr(row, "completed_at", None) or getattr(row, "updated_at", None)
        if row_start == started and row_end == ended:
            return True
    return False


def _public_historical(row: HistoricalTimeEntry, *, idempotent: bool) -> dict:
    derived = int((row.ended_at - row.started_at).total_seconds())
    return {
        "created": True,
        "idempotent": idempotent,
        "durable_id": f"historical:{int(row.id)}",
        "project_id": None if row.project_id is None else int(row.project_id),
        "source": row.source,
        "label": row.label,
        "started_at": row.started_at_original,
        "ended_at": row.ended_at_original,
        "started_at_utc": _stamp(row.started_at),
        "ended_at_utc": _stamp(row.ended_at),
        "stored_seconds": int(row.stored_seconds),
        "derived_seconds": derived,
        "seconds_origin": row.seconds_origin,
    }


def _matching_historical(db, parsed):
    if not _has_historical_table(db):
        return None
    if parsed["idempotency_key"]:
        existing = (
            db.query(HistoricalTimeEntry)
            .filter(HistoricalTimeEntry.idempotency_key == parsed["idempotency_key"])
            .one_or_none()
        )
        if existing is not None:
            if existing.request_fingerprint != parsed["request_fingerprint"]:
                raise LedgerError(409, "idempotency_conflict", "idempotency_key was already used for a different entry")
            return existing
    return (
        db.query(HistoricalTimeEntry)
        .filter(HistoricalTimeEntry.payload_key == parsed["payload_key"])
        .one_or_none()
    )


def create_entry(get_session, body: dict, *, now: datetime | None = None) -> dict:
    parsed = _create_request(body)
    with get_session() as db:
        if parsed["project_id"] is not None and db.get(Project, parsed["project_id"]) is None:
            raise LedgerError(400, "invalid_project", "project_id does not match a project")
    ensure_schema(get_session)
    raced = False
    with get_session() as db:
        existing = _matching_historical(db, parsed)
        if existing is not None:
            return _public_historical(existing, idempotent=True)
        if _execution_interval_exists(db, parsed["project_id"], parsed["started"], parsed["ended"]):
            raise LedgerError(
                409,
                "duplicate_assigned_evidence",
                "a stored execution session already has this project and exact interval; it was not changed",
            )
        timer_rows = (
            db.query(DevelopmentTimeEntry)
            .filter(DevelopmentTimeEntry.started_at == parsed["started"], DevelopmentTimeEntry.ended_at == parsed["ended"])
            .all()
        )
        for entry in timer_rows:
            if attributable_project_id(db, entry) == parsed["project_id"]:
                raise LedgerError(
                    409,
                    "duplicate_assigned_evidence",
                    "a time-ledger row already has this project and exact interval; it was not changed",
                )
        row = HistoricalTimeEntry(
            project_id=parsed["project_id"],
            source=parsed["source"],
            label=parsed["label"],
            started_at=parsed["started"],
            ended_at=parsed["ended"],
            started_at_original=parsed["started_original"],
            ended_at_original=parsed["ended_original"],
            stored_seconds=parsed["stored_seconds"],
            seconds_origin=parsed["seconds_origin"],
            idempotency_key=parsed["idempotency_key"],
            request_fingerprint=parsed["request_fingerprint"],
            payload_key=parsed["payload_key"],
            created_at=_naive_utc(now or datetime.now(UTC)),
        )
        db.add(row)
        try:
            _ensure_execution_guard(db)
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            if "duplicate_assigned_evidence" in str(exc):
                raise LedgerError(
                    409,
                    "duplicate_assigned_evidence",
                    "a stored execution session already has this project and exact interval; it was not changed",
                ) from exc
            raced = True
        else:
            return _public_historical(row, idempotent=False)
    if raced:
        with get_session() as db:
            existing = _matching_historical(db, parsed)
            if existing is not None:
                return _public_historical(existing, idempotent=True)
        raise LedgerError(409, "duplicate", "an identical historical entry already exists")
    raise LedgerError(409, "duplicate", "an identical historical entry already exists")


def _insert_time(db, fields: dict) -> None:
    db.add(DevelopmentTimeEntry(
        id=int(fields["id"]),
        work_item_id=int(fields["work_item_id"]),
        chat_id=int(fields["chat_id"]),
        started_at=_parse_stamp(fields["started_at"]),
        ended_at=_parse_stamp(fields["ended_at"]),
        seconds=int(fields["seconds"]),
        source=str(fields["source"]),
        created_at=_parse_stamp(fields["created_at"]) if fields.get("created_at") else _naive_utc(datetime.now(UTC)),
    ))


def _insert_historical(db, fields: dict) -> None:
    db.add(HistoricalTimeEntry(
        id=int(fields["id"]),
        project_id=fields.get("project_id"),
        source=str(fields["source"]),
        label=str(fields["label"]),
        started_at=_parse_stamp(fields["started_at"]),
        ended_at=_parse_stamp(fields["ended_at"]),
        started_at_original=str(fields["started_at_original"]),
        ended_at_original=str(fields["ended_at_original"]),
        stored_seconds=int(fields["stored_seconds"]),
        seconds_origin=str(fields["seconds_origin"]),
        idempotency_key=fields.get("idempotency_key"),
        request_fingerprint=str(fields["request_fingerprint"]),
        payload_key=str(fields.get("payload_key") or ""),
        created_at=_parse_stamp(fields["created_at"]),
    ))


def restore_backup(get_session, backup_dir: Path, body: dict) -> dict:
    if not isinstance(body, dict) or body.get("confirm") != "restore-time-ledger":
        raise LedgerError(409, "confirmation", "confirm must be restore-time-ledger")
    manifest = read_backup(backup_dir, str(body.get("backup_id") or ""))
    rows = manifest.get("rows")
    if not isinstance(rows, list) or not rows:
        raise LedgerError(409, "backup_tampered", "backup does not contain any rows")
    needs_historical = any(row.get("table") == "development_historical_time_entries" for row in rows)
    if needs_historical:
        ensure_schema(get_session)
    restored, already = [], []
    with get_session() as db:
        db.autoflush = False
        before = _counts(db)
        try:
            for packed in rows:
                kind = "time" if packed.get("table") == "development_time_entries" else None
                if packed.get("table") == "development_historical_time_entries":
                    kind = "historical"
                if kind is None or not isinstance(packed.get("fields"), dict):
                    raise LedgerError(409, "backup_tampered", "backup row is not a time-ledger record")
                if not _fingerprint_ok(packed):
                    raise LedgerError(409, "backup_tampered", "a backup row fingerprint does not match its fields")
                row_id = int(packed["fields"]["id"])
                current = _load(db, kind, row_id)
                if current is not None:
                    current_pack = _canonical_from_row(db, kind, current)
                    if current_pack["fingerprint"] != packed["fingerprint"]:
                        raise LedgerError(409, "collision", f"{packed.get('durable_id')} already exists with different contents; nothing was restored")
                    already.append(packed.get("durable_id"))
                    continue
                if kind == "time":
                    _insert_time(db, packed["fields"])
                else:
                    _insert_historical(db, packed["fields"])
                restored.append(packed.get("durable_id"))
            db.flush()
        except IntegrityError as exc:
            raise LedgerError(409, "collision", "restore collided with an existing ledger key; nothing was restored") from exc
        attribution_notes = []
        for packed in rows:
            kind = "historical" if packed["table"] == "development_historical_time_entries" else "time"
            current = _load(db, kind, int(packed["fields"]["id"]))
            current_pack = _canonical_from_row(db, kind, current)
            if current_pack["fingerprint"] != packed["fingerprint"] or current_pack["fields"] != packed["fields"]:
                raise LedgerError(409, "collision", "restored row did not round-trip to the backup fingerprint")
            if current_pack["attribution"] != packed.get("attribution"):
                attribution_notes.append({
                    "durable_id": packed.get("durable_id"),
                    "backup_attribution": packed.get("attribution"),
                    "current_attribution": current_pack["attribution"],
                    "parent_changed": False,
                    "warning": "The parent project attribution differs from the backup. The ledger row was restored. The parent was not changed.",
                })
        after = _counts(db)
        for name in ("chats", "development_work_items", "kanban_tickets"):
            if name in before and after.get(name) != before[name]:
                raise LedgerError(409, "counter_effect", f"restore stopped because {name} changed")
    return {
        "restored": restored,
        "already_present": already,
        "backup_id": manifest["backup_id"],
        "checksum": manifest["checksum"],
        "confirm": "restore-time-ledger",
        "attribution_notes": attribution_notes,
    }


def historical_blocks(lo: datetime, hi: datetime, get_session, block_fn) -> list[dict]:
    with get_session() as db:
        if not _has_historical_table(db):
            return []
        rows = (
            db.query(HistoricalTimeEntry)
            .filter(HistoricalTimeEntry.started_at < hi, HistoricalTimeEntry.ended_at > lo)
            .all()
        )
        blocks = []
        for row in rows:
            if row.started_at is None or row.ended_at is None or row.ended_at <= row.started_at:
                continue
            if (row.ended_at - row.started_at).total_seconds() > MAX_SPAN_SECONDS:
                continue
            blocks.append(block_fn(
                "historical_time",
                "decisions",
                row.label,
                row.started_at,
                row.ended_at,
                None if row.project_id is None else int(row.project_id),
                row.label,
                f"historical:{int(row.id)}",
            ))
        return blocks


def _publish_historical_source(report: dict) -> None:
    """The installed collator lists a fixed source catalogue. Add this one from the blocks it already kept."""
    blocks = [block for block in (report.get("blocks") or []) if block.get("source") == "historical_time"]
    seconds = sum(int(block.get("seconds") or 0) for block in blocks)
    sources = [row for row in (report.get("sources") or []) if row.get("source") != "historical_time"]
    sources.append({
        "source": "historical_time",
        "label": "Historical ledger entries",
        "records": len(blocks),
        "blocks": len(blocks),
        "seconds": seconds,
        "state": "data" if blocks else "empty",
    })
    report["sources"] = sources


def attach_reader() -> None:
    """Teach the period collator about historical rows without editing month.py."""
    import distr.core.reports.month as month

    current = month.collect_database_blocks
    if getattr(current, "_time_ledger_wrapped", False):
        return
    original = current
    original_collate = month.collate

    def wrapped(lo, hi, get_session=None):
        blocks, counts = original(lo, hi, get_session)
        if get_session is None:
            from distr.core.reports import service
            get_session = service.get_session
        extra = historical_blocks(lo, hi, get_session, month._block)
        blocks.extend(extra)
        counts["historical_time"] = counts.get("historical_time", 0) + len(extra)
        return blocks, counts

    def wrapped_collate(*args, **kwargs):
        report = original_collate(*args, **kwargs)
        if isinstance(report, dict):
            _publish_historical_source(report)
        return report

    wrapped._time_ledger_wrapped = True
    wrapped._time_ledger_original = original
    wrapped_collate._time_ledger_original = original_collate
    month.collect_database_blocks = wrapped
    month.collate = wrapped_collate


def detach_reader() -> None:
    import distr.core.reports.month as month

    original = getattr(month.collect_database_blocks, "_time_ledger_original", None)
    if original is not None:
        month.collect_database_blocks = original
    original_collate = getattr(month.collate, "_time_ledger_original", None)
    if original_collate is not None:
        month.collate = original_collate


def ledger_digest(get_session) -> str:
    """Checksum of every durable ledger row. Report projections are not included."""
    with get_session() as db:
        rows = [_canonical_from_row(db, "time", row) for row in db.query(DevelopmentTimeEntry).order_by(DevelopmentTimeEntry.id).all()]
        if _has_historical_table(db):
            rows.extend(
                _canonical_from_row(db, "historical", row)
                for row in db.query(HistoricalTimeEntry).order_by(HistoricalTimeEntry.id).all()
            )
    return _sha256({"rows": rows})


def default_backup_dir() -> Path:
    from distr.core.paths import DB_DIR
    return Path(DB_DIR) / "time_ledger_backups"
