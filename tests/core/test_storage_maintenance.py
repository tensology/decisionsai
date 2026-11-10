from __future__ import annotations

import os
import json
import subprocess
import sys

import pytest
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
    assert result["orphan_media_pruned_files"] == [{"name": "orphan.ogg", "bytes": 5, "reason": "unreferenced"}]


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


@pytest.mark.parametrize('reference_kind', ['thread', 'handoff', 'snapshot', 'restoration', 'raw', 'client', 'nested_raw', 'ticket', 'legacy'])
def test_proposal_and_thread_media_survive_source_row_loss_and_cleanup(tmp_path, reference_kind):
    media = tmp_path / 'whatsapp_media'
    media.mkdir()
    kept, orphan = media / 'kept.ogg', media / 'unreferenced.ogg'
    sidecar = media / 'kept.ogg.0123456789abcdef.transcript.txt'
    for path in [kept, orphan, sidecar]:
        path.write_bytes(b'original media or transcript')
        os.utime(path, (1, 1))
    connection = _database(tmp_path / 'settings.db')
    connection.executescript("""
        ALTER TABLE whatsapp_messages ADD COLUMN raw_data TEXT;
        CREATE TABLE chats (id INTEGER PRIMARY KEY, params TEXT);
        CREATE TABLE incoming_proposal_snapshots (key TEXT, snapshot TEXT);
        CREATE TABLE incoming_proposal_restorations (key TEXT, snapshot TEXT);
    """)
    record = {'path': str(kept), 'sha256': '0' * 64}
    if reference_kind == 'thread':
        connection.execute('INSERT INTO chats VALUES (1, ?)', (json.dumps({'incoming_attachments':[record]}),))
    elif reference_kind == 'handoff':
        connection.execute('INSERT INTO chats VALUES (1, ?)', (json.dumps({'incoming_proposal_handoff':{'proposal':{'attachments':[record]}}}),))
    elif reference_kind in {'snapshot', 'restoration'}:
        table = 'incoming_proposal_snapshots' if reference_kind == 'snapshot' else 'incoming_proposal_restorations'
        connection.execute(f'INSERT INTO {table} VALUES (?, ?)', ('source', json.dumps({'attachments':[record]})))
    elif reference_kind in {'raw', 'client'}:
        key = 'media_local_path' if reference_kind == 'raw' else 'client_media_local_path'
        connection.execute('INSERT INTO whatsapp_messages VALUES (1, NULL, ?)', (json.dumps({key:str(kept)}),))
    elif reference_kind == 'nested_raw':
        connection.execute('INSERT INTO whatsapp_messages VALUES (1, NULL, ?)', (json.dumps({'media':{'local_path':str(kept)}}),))
    elif reference_kind == 'ticket':
        connection.execute('INSERT INTO kanban_ticket_files VALUES (1, ?)', (str(kept),))
    else:
        connection.execute('INSERT INTO whatsapp_messages VALUES (1, ?, NULL)', ('/old/client/db/whatsapp_media/kept.ogg',))
    connection.commit()
    connection.close()
    report = run_storage_maintenance(db_root=tmp_path, now=datetime(2026,11,10,tzinfo=timezone.utc), force=True)
    assert kept.read_bytes() == b'original media or transcript'
    assert sidecar.is_file()
    assert not orphan.exists()
    assert report['orphan_media_pruned'] == 1


def test_cleanup_does_not_treat_message_body_as_a_media_reference(tmp_path):
    media = tmp_path / 'whatsapp_media'; media.mkdir()
    orphan = media / 'orphan.jpg'; orphan.write_bytes(b'unreferenced'); os.utime(orphan, (1,1))
    connection = _database(tmp_path / 'settings.db')
    connection.execute('CREATE TABLE chats (params TEXT)')
    connection.execute('INSERT INTO chats VALUES (?)', (json.dumps({'body': {'path':str(orphan)}}),))
    connection.commit(); connection.close()
    run_storage_maintenance(db_root=tmp_path, force=True)
    assert not orphan.exists()


def test_cleanup_fails_closed_when_media_references_cannot_be_read(tmp_path):
    media = tmp_path / 'whatsapp_media'; media.mkdir()
    original = media / 'voice.ogg'; original.write_bytes(b'original'); os.utime(original, (1,1))
    connection = _database(tmp_path / 'settings.db')
    # Existing table with an unreadable reference layout must never mean "no references".
    connection.execute('CREATE TABLE chats (old_params TEXT)')
    connection.commit(); connection.close()
    report = run_storage_maintenance(db_root=tmp_path, force=True)
    assert original.is_file()
    assert report['orphan_media_scan_incomplete'] is True


def test_manual_cleanup_uses_same_thread_reference_guard(tmp_path):
    media = tmp_path / 'whatsapp_media'; media.mkdir()
    original = media / 'source.pdf'; original.write_bytes(b'%PDF-original')
    orphan = media / 'orphan.jpg'; orphan.write_bytes(b'orphan')
    connection = _database(tmp_path / 'settings.db')
    connection.execute('CREATE TABLE chats (params TEXT)')
    connection.execute('INSERT INTO chats VALUES (?)', (json.dumps({'incoming_attachments':[{'path':str(original)}]}),))
    connection.commit(); connection.close()
    repo = Path(__file__).resolve().parents[2]
    source = (repo / 'bin/clean-db.sh').read_text()
    section = source[source.index('# Orphan WhatsApp media'):]
    script = section.split('<<\'PY\'\n',1)[1].split('\nPY',1)[0]
    result = subprocess.run([sys.executable, '-c', script], cwd=repo,
        env={**os.environ, 'DB_DIR':str(tmp_path), 'ROOT_DIR':str(repo), 'DRY_RUN':'false'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert original.is_file()
    assert not orphan.exists()


def test_malformed_reference_json_skips_deletion_without_losing_valid_references(tmp_path):
    media = tmp_path / 'whatsapp_media'; media.mkdir()
    original = media / 'original.jpg'; original.write_bytes(b'original'); os.utime(original,(1,1))
    conn = _database(tmp_path / 'settings.db')
    conn.execute('CREATE TABLE chats (params TEXT)')
    conn.execute('INSERT INTO chats VALUES (?)', (json.dumps({'incoming_attachments':[{'path':str(original)}]}),))
    conn.execute('INSERT INTO chats VALUES (?)', ('{malformed historical params',))
    conn.commit(); conn.close()
    report = run_storage_maintenance(db_root=tmp_path, force=True)
    assert original.is_file()
    assert report['orphan_media_scan_incomplete'] is True
    assert report['orphan_media_pruned'] == 0


def test_cleanup_preserves_integrity_metadata_sidecars_only_for_referenced_media(tmp_path):
    media = tmp_path / 'whatsapp_media'; media.mkdir()
    names=['source.mp4', 'source.mp4.record.json', 'source.mp4.mime', 'orphan.mp4.record.json']
    for name in names:
        path=media/name; path.write_bytes(b'original or metadata'); os.utime(path,(1,1))
    conn = _database(tmp_path / 'settings.db')
    conn.execute('INSERT INTO whatsapp_messages VALUES (1, ?)', ('whatsapp_media/source.mp4',))
    conn.commit(); conn.close()
    report=run_storage_maintenance(db_root=tmp_path, force=True)
    assert sorted(path.name for path in media.iterdir()) == sorted(names[:3])
    assert report['orphan_media_pruned'] == 1
