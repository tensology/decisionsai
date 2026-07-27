from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from distr.core.storage_maintenance import run_storage_maintenance


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE project_execution_sessions (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE project_execution_events (
            id INTEGER PRIMARY KEY, session_id INTEGER, event_type TEXT,
            status TEXT, message TEXT, payload TEXT, created_at TEXT
        );
        CREATE TABLE orchestrator_events (
            id INTEGER PRIMARY KEY, event_type TEXT, summary TEXT,
            payload TEXT, evidence TEXT, created_at TEXT
        );
        CREATE TABLE whatsapp_messages (id INTEGER PRIMARY KEY, media_local_path TEXT);
        CREATE TABLE kanban_ticket_files (id INTEGER PRIMARY KEY, file_path TEXT);
        """
    )
    return connection


def test_maintenance_compacts_progress_but_preserves_terminal_evidence(tmp_path: Path) -> None:
    db = tmp_path / "settings.db"
    connection = _database(db)
    connection.execute("INSERT INTO project_execution_sessions VALUES (1, 'completed')")
    connection.execute(
        "INSERT INTO project_execution_events VALUES (1, 1, 'message_update', '', ?, ?, '2026-01-01 00:00:00')",
        ("progress " * 200, "payload " * 200),
    )
    connection.execute(
        "INSERT INTO project_execution_events VALUES (2, 1, 'session_completed', 'completed', 'final proof', 'evidence', '2026-01-01 00:00:01')"
    )
    connection.execute(
        "INSERT INTO orchestrator_events VALUES (1, 'worker_progress', ?, ?, ?, '2026-07-01 00:00:00')",
        ("summary " * 200, "payload " * 200, "evidence " * 200),
    )
    connection.execute(
        "INSERT INTO orchestrator_events VALUES (2, 'worker_completed', 'final', 'result', 'proof', '2026-01-01 00:00:01')"
    )
    connection.commit()
    connection.close()

    result = run_storage_maintenance(
        db_root=tmp_path,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
        force=True,
    )

    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM project_execution_events WHERE id=1").fetchone()[0] == 0
    assert connection.execute("SELECT message FROM project_execution_events WHERE id=2").fetchone()[0] == "final proof"
    compacted = connection.execute("SELECT summary, payload, evidence FROM orchestrator_events WHERE id=1").fetchone()
    assert len(compacted[0]) <= 800
    assert compacted[1] == '{"compacted":true,"reason":"retention"}'
    assert compacted[2] is None
    assert connection.execute("SELECT summary FROM orchestrator_events WHERE id=2").fetchone()[0] == "final"
    connection.close()
    assert result["project_events_pruned"] == 1
    assert result["orchestrator_events_compacted"] == 1


def test_maintenance_only_deletes_old_unreferenced_whatsapp_media(tmp_path: Path) -> None:
    db = tmp_path / "settings.db"
    media = tmp_path / "whatsapp_media"
    media.mkdir()
    kept = media / "kept.ogg"
    orphan = media / "orphan.ogg"
    recent = media / "recent.ogg"
    for file in (kept, orphan, recent):
        file.write_bytes(b"voice")
    old_epoch = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(kept, (old_epoch, old_epoch))
    os.utime(orphan, (old_epoch, old_epoch))

    connection = _database(db)
    connection.execute(
        "INSERT INTO whatsapp_messages VALUES (1, ?)", ("whatsapp_media/kept.ogg",)
    )
    connection.commit()
    connection.close()

    result = run_storage_maintenance(
        db_root=tmp_path,
        now=datetime(2026, 7, 27, tzinfo=timezone.utc),
        force=True,
    )

    assert kept.exists()
    assert recent.exists()
    assert not orphan.exists()
    assert result["orphan_media_pruned"] == 1


def test_maintenance_respects_interval(tmp_path: Path) -> None:
    db = tmp_path / "settings.db"
    _database(db).close()
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    assert run_storage_maintenance(db_root=tmp_path, now=now, force=True)["status"] == "ok"
    result = run_storage_maintenance(db_root=tmp_path, now=now)
    assert result["status"] == "skipped"
    assert result["reason"] == "interval"


def test_web_startup_runs_storage_and_memory_maintenance_off_event_loop() -> None:
    server = (
        Path(__file__).resolve().parents[2] / "distr/gui/web/server.py"
    ).read_text(encoding="utf-8")

    assert "await asyncio.to_thread(run_weekly_machine_activity_compaction)" in server
    assert "await asyncio.to_thread(run_storage_maintenance)" in server
