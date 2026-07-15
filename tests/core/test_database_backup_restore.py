import json
import sqlite3

import pytest

from distr.core.database_backup import (
    DatabaseBackupError,
    create_database_backup,
    restore_database_backup,
    validate_database_backup,
)
from distr.core.database_runtime_lock import acquire_runtime_database_lock


def _database(path, value="original"):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO settings(value) VALUES (?)", (value,))


def _value(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM settings").fetchone()[0]


def test_verified_backup_and_atomic_restore_round_trip(tmp_path):
    live = tmp_path / "settings.db"
    backup = tmp_path / "release-backup.db"
    _database(live)

    created = create_database_backup(backup, database_path=live)
    assert created["integrity"] == "ok"
    manifest = json.loads((tmp_path / "release-backup.db.manifest.json").read_text())
    assert manifest["sha256"] == created["sha256"]

    with sqlite3.connect(live) as connection:
        connection.execute("UPDATE settings SET value='mutated'")
    restored = restore_database_backup(backup, database_path=live)

    assert _value(live) == "original"
    assert restored["integrity"] == "ok"
    assert restored["rollback_backup"]
    assert _value(restored["rollback_backup"]) == "mutated"


def test_restore_rejects_invalid_sqlite_without_touching_live_database(tmp_path):
    live = tmp_path / "settings.db"
    invalid = tmp_path / "invalid.db"
    _database(live)
    invalid.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(DatabaseBackupError, match="Invalid SQLite"):
        restore_database_backup(invalid, database_path=live)
    assert _value(live) == "original"


def test_manifest_checksum_detects_tampered_backup(tmp_path):
    live = tmp_path / "settings.db"
    backup = tmp_path / "backup.db"
    _database(live)
    create_database_backup(backup, database_path=live)
    with backup.open("ab") as handle:
        handle.write(b"tamper")

    with pytest.raises(DatabaseBackupError, match="checksum"):
        validate_database_backup(backup)


def test_restore_rejects_database_in_use(tmp_path):
    live = tmp_path / "settings.db"
    backup = tmp_path / "backup.db"
    _database(live)
    create_database_backup(backup, database_path=live)
    handle = acquire_runtime_database_lock(live)
    try:
        with pytest.raises(DatabaseBackupError, match="in use"):
            restore_database_backup(backup, database_path=live)
    finally:
        if handle is not None:
            handle.close()
    assert _value(live) == "original"
