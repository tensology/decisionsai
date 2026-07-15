"""Verified SQLite backup and atomic restore for DecisionsAI user data.

This module deliberately lives outside ``distr.core.db`` so importing it does
not initialize or migrate the database that an operator is trying to recover.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from distr.core.paths import DB_DIR
from distr.core.database_runtime_lock import (
    DatabaseInUseError,
    exclusive_database_maintenance_lock,
)


DEFAULT_DATABASE_PATH = Path(DB_DIR) / "settings.db"


class DatabaseBackupError(RuntimeError):
    """A backup cannot be trusted or safely restored."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_path(path: Path) -> Path:
    return path.with_name(path.name + ".manifest.json")


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def validate_database_backup(
    path: str | Path,
    *,
    required_tables: Iterable[str] = ("settings",),
    verify_manifest: bool = True,
) -> dict:
    """Validate file identity, SQLite integrity, schema presence, and checksum."""
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise DatabaseBackupError(f"Database backup does not exist or is empty: {candidate}")
    try:
        with sqlite3.connect(str(candidate)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise DatabaseBackupError(
                    f"SQLite integrity check failed: {integrity[0] if integrity else 'no result'}"
                )
            tables = _table_names(connection)
    except sqlite3.DatabaseError as exc:
        raise DatabaseBackupError(f"Invalid SQLite backup: {exc}") from exc

    missing = sorted(set(required_tables) - tables)
    if missing:
        raise DatabaseBackupError(f"Backup is missing required tables: {', '.join(missing)}")

    checksum = _sha256(candidate)
    manifest_path = _manifest_path(candidate)
    if verify_manifest and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatabaseBackupError(f"Invalid backup manifest: {exc}") from exc
        if manifest.get("sha256") != checksum:
            raise DatabaseBackupError("Backup checksum does not match its manifest")

    return {
        "path": str(candidate),
        "sha256": checksum,
        "size_bytes": candidate.stat().st_size,
        "tables": sorted(tables),
        "integrity": "ok",
    }


def _sqlite_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as source_db:
        with sqlite3.connect(str(destination)) as destination_db:
            source_db.backup(destination_db)


def create_database_backup(
    destination: str | Path,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    required_tables: Iterable[str] = ("settings",),
) -> dict:
    """Create a transactionally consistent SQLite backup plus checksum manifest."""
    source = Path(database_path).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if not source.is_file():
        raise DatabaseBackupError(f"Database does not exist: {source}")
    if source == target:
        raise DatabaseBackupError("Backup destination must differ from the live database")

    temporary = target.with_name(f".{target.name}.tmp-{uuid4().hex}")
    try:
        _sqlite_copy(source, temporary)
        evidence = validate_database_backup(
            temporary, required_tables=required_tables, verify_manifest=False
        )
        os.replace(temporary, target)
        evidence["path"] = str(target)
        manifest = {
            "format": "decisionsai-sqlite-backup-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_name": source.name,
            "sha256": evidence["sha256"],
            "size_bytes": evidence["size_bytes"],
            "tables": evidence["tables"],
        }
        manifest_path = _manifest_path(target)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        evidence["manifest"] = str(manifest_path)
        return evidence
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def restore_database_backup(
    source: str | Path,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    required_tables: Iterable[str] = ("settings",),
) -> dict:
    """Validate and atomically restore a backup, retaining a rollback copy."""
    backup = Path(source).expanduser().resolve()
    target = Path(database_path).expanduser().resolve()
    evidence = validate_database_backup(backup, required_tables=required_tables)
    if backup == target:
        raise DatabaseBackupError("Restore source must differ from the live database")

    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rollback = target.with_name(f"{target.stem}.pre-restore-{stamp}{target.suffix}")
    temporary = target.with_name(f".{target.name}.restore-{uuid4().hex}")

    try:
        with exclusive_database_maintenance_lock(target):
            if target.exists():
                _sqlite_copy(target, rollback)
                validate_database_backup(
                    rollback, required_tables=required_tables, verify_manifest=False
                )
            _sqlite_copy(backup, temporary)
            validate_database_backup(
                temporary, required_tables=required_tables, verify_manifest=False
            )
            os.replace(temporary, target)
            for suffix in ("-wal", "-shm"):
                target.with_name(target.name + suffix).unlink(missing_ok=True)
    except DatabaseInUseError as exc:
        temporary.unlink(missing_ok=True)
        raise DatabaseBackupError(str(exc)) from exc
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    restored = validate_database_backup(
        target, required_tables=required_tables, verify_manifest=False
    )
    return {
        **restored,
        "source_backup": evidence["path"],
        "rollback_backup": str(rollback) if rollback.exists() else None,
    }
