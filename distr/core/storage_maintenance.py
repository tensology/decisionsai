"""Conservative storage maintenance for long-running DecisionsAI installs."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from distr.core.paths import DB_DIR

logger = logging.getLogger(__name__)

MAINTENANCE_STATE_FILE = ".storage-maintenance.json"
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_COMPACT_AFTER_DAYS = 7
DEFAULT_PRUNE_AFTER_DAYS = 30
DEFAULT_ORPHAN_MEDIA_AFTER_DAYS = 30

_PROJECT_PROGRESS_TYPES = ("message_update", "tool_execution_update", "heartbeat")
_ORCHESTRATOR_PROGRESS_TYPES = ("worker_progress",)
_TERMINAL_STATUSES = ("completed", "failed", "cancelled", "canceled", "stopped")


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int((os.environ.get(name) or str(default)).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def storage_policy() -> dict[str, int]:
    """Return the active conservative retention policy."""
    return {
        "interval_seconds": _positive_env_int(
            "DECISIONSAI_STORAGE_MAINTENANCE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS
        ),
        "compact_after_days": _positive_env_int(
            "DECISIONSAI_PROGRESS_COMPACT_AFTER_DAYS", DEFAULT_COMPACT_AFTER_DAYS
        ),
        "prune_after_days": _positive_env_int(
            "DECISIONSAI_PROGRESS_PRUNE_AFTER_DAYS", DEFAULT_PRUNE_AFTER_DAYS
        ),
        "orphan_media_after_days": _positive_env_int(
            "DECISIONSAI_ORPHAN_MEDIA_AFTER_DAYS", DEFAULT_ORPHAN_MEDIA_AFTER_DAYS
        ),
    }


def _state_path(db_root: Path) -> Path:
    return db_root / MAINTENANCE_STATE_FILE


def read_storage_maintenance_state(db_root: str | Path = DB_DIR) -> dict[str, Any]:
    try:
        value = json.loads(_state_path(Path(db_root)).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(db_root: Path, value: dict[str, Any]) -> None:
    target = _state_path(db_root)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def _compact_event_storage(
    connection: sqlite3.Connection,
    *,
    compact_cutoff: str,
    prune_cutoff: str,
) -> dict[str, int]:
    report = {
        "project_events_compacted": 0,
        "project_events_pruned": 0,
        "orchestrator_events_compacted": 0,
    }
    compact_marker = json.dumps({"compacted": True, "reason": "retention"}, separators=(",", ":"))

    if _table_exists(connection, "project_execution_events"):
        types = _PROJECT_PROGRESS_TYPES
        cursor = connection.execute(
            f"""
            UPDATE project_execution_events
            SET payload = ?, message = substr(coalesce(message, ''), 1, 800)
            WHERE created_at < ?
              AND event_type IN ({_placeholders(types)})
              AND (length(coalesce(payload, '')) > 80 OR length(coalesce(message, '')) > 800)
            """,
            (compact_marker, compact_cutoff, *types),
        )
        report["project_events_compacted"] = max(0, cursor.rowcount)

        if _table_exists(connection, "project_execution_sessions"):
            prune_types = ("message_update", "tool_execution_update", "heartbeat")
            cursor = connection.execute(
                f"""
                DELETE FROM project_execution_events
                WHERE created_at < ?
                  AND event_type IN ({_placeholders(prune_types)})
                  AND session_id IN (
                      SELECT id FROM project_execution_sessions
                      WHERE lower(coalesce(status, '')) IN ({_placeholders(_TERMINAL_STATUSES)})
                  )
                """,
                (prune_cutoff, *prune_types, *_TERMINAL_STATUSES),
            )
            report["project_events_pruned"] = max(0, cursor.rowcount)

    if _table_exists(connection, "orchestrator_events"):
        types = _ORCHESTRATOR_PROGRESS_TYPES
        cursor = connection.execute(
            f"""
            UPDATE orchestrator_events
            SET payload = ?, evidence = NULL, summary = substr(coalesce(summary, ''), 1, 800)
            WHERE created_at < ?
              AND event_type IN ({_placeholders(types)})
              AND (
                  length(coalesce(payload, '')) > 80
                  OR length(coalesce(evidence, '')) > 0
                  OR length(coalesce(summary, '')) > 800
              )
            """,
            (compact_marker, compact_cutoff, *types),
        )
        report["orchestrator_events_compacted"] = max(0, cursor.rowcount)

    return report


_MEDIA_PATH_FIELDS = frozenset({
    "path", "file_path", "media_local_path", "client_media_local_path",
    "local_path", "stored_path", "transcript_path",
})
_MEDIA_REFERENCE_CONTAINERS = frozenset({
    "incoming_attachments", "incoming_proposal_handoff", "incoming_reply_context",
    "incoming_source_context", "proposal", "snapshot", "attachments", "files",
    "media", "metadata", "evidence", "context_evidence", "source_context",
})


def _media_reference_paths(value: Any, *, depth: int = 0):
    """Walk attachment structures, never message bodies or arbitrary source text."""
    if depth > 16:
        raise ValueError("Media reference nesting exceeds the supported layout")
    if isinstance(value, list):
        for item in value:
            yield from _media_reference_paths(item, depth=depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in _MEDIA_PATH_FIELDS and isinstance(item, str):
                yield item
            elif key in _MEDIA_REFERENCE_CONTAINERS:
                yield from _media_reference_paths(item, depth=depth + 1)


def _referenced_whatsapp_media(connection: sqlite3.Connection, media_root: Path) -> set[Path]:
    """Collect retained source, proposal and thread files for both cleanup paths.

    Any unreadable reference query aborts the scan before deletion. A reference
    surviving after its WhatsApp row is removed still owns its original bytes.
    """
    media_root = media_root.resolve()
    referenced: set[Path] = set()

    def remember(stored):
        raw = str(stored or "").strip()
        if not raw or "://" in raw or raw.startswith("data:"):
            return
        candidate = Path(raw.replace("\\", "/")).expanduser()
        if not candidate.is_absolute():
            candidate = media_root.parent / candidate if candidate.parts and candidate.parts[0] == media_root.name else media_root / candidate.name
        resolved = candidate.resolve()
        if not resolved.is_relative_to(media_root) or resolved == media_root:
            # Older clients recorded absolute paths before moving DB_DIR.
            # Match only one basename in the managed cache, never its directory.
            candidate = media_root / candidate.name
            resolved = candidate.resolve()
        if resolved != media_root and resolved.is_relative_to(media_root):
            referenced.add(resolved)

    for table, field in (("whatsapp_messages", "media_local_path"), ("kanban_ticket_files", "file_path")):
        if _table_exists(connection, table):
            for (value,) in connection.execute(f"SELECT {field} FROM {table} WHERE {field} IS NOT NULL"):
                remember(value)

    json_queries = []
    if _table_exists(connection, "whatsapp_messages"):
        columns = {row[1] for row in connection.execute("PRAGMA table_info(whatsapp_messages)")}
        if "raw_data" in columns:
            json_queries.append("SELECT raw_data FROM whatsapp_messages WHERE raw_data IS NOT NULL AND raw_data != ''")
        if "client_media_local_path" in columns:
            for (value,) in connection.execute("SELECT client_media_local_path FROM whatsapp_messages WHERE client_media_local_path IS NOT NULL"):
                remember(value)
    for table, field in (("chats", "params"), ("incoming_proposal_snapshots", "snapshot"), ("incoming_proposal_restorations", "snapshot")):
        if _table_exists(connection, table):
            json_queries.append(f"SELECT {field} FROM {table} WHERE {field} IS NOT NULL AND {field} != ''")
    for query in json_queries:
        for (encoded,) in connection.execute(query):
            try:
                value = json.loads(encoded)
            except (ValueError, TypeError) as exc:
                raise ValueError("Stored media references contain unreadable JSON") from exc
            for stored in _media_reference_paths(value):
                remember(stored)

    # Companion transcripts and integrity/MIME records belong to the original.
    names = {path.name for path in referenced}
    sidecars = {name + suffix for name in names for suffix in (".mime", ".json", ".metadata.json", ".record.json", ".transcript.txt")}
    for candidate in media_root.iterdir():
        transcript = re.fullmatch(r"(.+)\.[a-f0-9]{16}\.transcript\.txt", candidate.name)
        if candidate.name in sidecars or (transcript and transcript[1] in names):
            referenced.add(candidate.resolve())
    return referenced


def _prune_orphan_whatsapp_media(
    connection: sqlite3.Connection,
    *,
    db_root: Path,
    cutoff_epoch: float,
) -> dict[str, Any]:
    media_root = (db_root / "whatsapp_media").resolve()
    report = {"orphan_media_pruned": 0, "orphan_media_bytes_reclaimed": 0, "orphan_media_pruned_files": []}
    if not media_root.is_dir():
        return report
    try:
        referenced = _referenced_whatsapp_media(connection, media_root)
    except (sqlite3.DatabaseError, OSError, ValueError):
        logger.warning("Skipping orphan media cleanup: retained file references could not be verified", exc_info=True)
        return {**report, "orphan_media_scan_incomplete": True}
    for candidate in media_root.iterdir():
        try:
            resolved = candidate.resolve()
            stat = candidate.stat()
        except OSError:
            continue
        if not candidate.is_file() or resolved in referenced or stat.st_mtime >= cutoff_epoch:
            continue
        try:
            candidate.unlink()
        except OSError:
            continue
        report["orphan_media_pruned"] += 1
        report["orphan_media_bytes_reclaimed"] += stat.st_size
        # Preserve enough evidence to explain future deletions without copying
        # source content or allowing an unbounded maintenance-state file.
        if len(report["orphan_media_pruned_files"]) < 1000:
            report["orphan_media_pruned_files"].append({"name": candidate.name, "bytes": stat.st_size, "reason": "unreferenced"})
    return report


def checkpoint_database(db_path: str | Path, *, truncate: bool = False) -> dict[str, int]:
    """Checkpoint SQLite WAL without treating a busy reader as an error."""
    path = Path(db_path)
    if not path.is_file():
        return {"busy": 0, "wal_frames": 0, "checkpointed_frames": 0}
    connection = sqlite3.connect(str(path), timeout=2.0)
    try:
        mode = "TRUNCATE" if truncate else "PASSIVE"
        row = connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone() or (0, 0, 0)
        return {
            "busy": int(row[0] or 0),
            "wal_frames": int(row[1] or 0),
            "checkpointed_frames": int(row[2] or 0),
        }
    finally:
        connection.close()


def run_storage_maintenance(
    *,
    db_root: str | Path = DB_DIR,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run bounded maintenance once per configured interval."""
    root = Path(db_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    policy = storage_policy()
    current = now or datetime.now(timezone.utc)
    previous = read_storage_maintenance_state(root)
    last_epoch = float(previous.get("completed_at_epoch") or 0)
    if not force and current.timestamp() - last_epoch < policy["interval_seconds"]:
        return {"status": "skipped", "reason": "interval", "policy": policy, "last": previous}

    db_path = root / "settings.db"
    report: dict[str, Any] = {"status": "ok", "policy": policy}
    if not db_path.is_file():
        report.update({"status": "skipped", "reason": "database_missing"})
        return report

    compact_cutoff = (current - timedelta(days=policy["compact_after_days"])).strftime("%Y-%m-%d %H:%M:%S")
    prune_cutoff = (current - timedelta(days=policy["prune_after_days"])).strftime("%Y-%m-%d %H:%M:%S")
    media_cutoff = current.timestamp() - (policy["orphan_media_after_days"] * 86400)
    connection = sqlite3.connect(str(db_path), timeout=5.0)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        report.update(
            _compact_event_storage(
                connection,
                compact_cutoff=compact_cutoff,
                prune_cutoff=prune_cutoff,
            )
        )
        report.update(
            _prune_orphan_whatsapp_media(
                connection,
                db_root=root,
                cutoff_epoch=media_cutoff,
            )
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    report["checkpoint"] = checkpoint_database(db_path)
    report["completed_at"] = current.isoformat()
    report["completed_at_epoch"] = current.timestamp()
    _write_state(root, report)
    logger.info("Storage maintenance completed: %s", report)
    return report
