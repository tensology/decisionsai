"""Isolated SQLite coverage for the time-ledger export, delete, create, and restore routes.

These tests never open the application database. The documented JSON examples are
posted only at the temporary server created here.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_ROOT = Path(__file__).resolve().parents[2]


def _stub_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    sys.modules[name] = module


# The web package imports the full GUI server. The ledger route does not need that.
_stub_package("distr.gui.web", _ROOT / "distr/gui/web")
_stub_package("distr.gui.web.routes", _ROOT / "distr/gui/web/routes")
_stub_package("distr.gui.web.routes.development", _ROOT / "distr/gui/web/routes/development")

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
from distr.core.reports import month
from distr.core.workflow.time_ledger import (
    LedgerError,
    create_entry,
    delete_entry,
    detach_reader,
    export_entries,
    ledger_digest,
    parse_durable_id,
    restore_backup,
)
from distr.gui.web.routes.development.time_ledger import register_routes

ORIGINAL_DOC_SHA256 = "8d9e7045941e8a521156afd2ea9cd5c2457a9c755aabf4b025f4aa970f581bdd"
DOC_BULLET = "- Time ledger (closed intervals only): see [Time ledger](#time-ledger).\n"
WINDOW = {
    "window_start": "2026-10-23T00:00:00+02:00",
    "window_end": "2026-11-08T00:00:00+02:00",
}
CREATE_EXAMPLE = {
    "started_at": "2026-10-30T16:06:00.000000+02:00",
    "ended_at": "2026-10-30T16:06:08.000000+02:00",
    "project_id": None,
    "source": "historical",
    "label": "Recovered interval",
    "seconds": 8,
    "idempotency_key": "example-recovered-8s",
}


@pytest.fixture()
def ledger(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    backup_dir = tmp_path / "time_ledger_backups"
    with get_session() as db:
        project = Project(name="Alpha", folder_location=str(tmp_path / "alpha"))
        db.add(project)
        db.flush()
        assigned = Chat(title="Assigned thread", project_id=project.id, params="{}")
        loose = Chat(title="Loose thread", project_id=None, params="{}")
        db.add_all([assigned, loose])
        db.flush()
        item_assigned = DevelopmentWorkItem(
            chat_id=assigned.id, identity_key=f"thread:{assigned.id}", project_id=project.id,
            ticket_title="Assigned work", time_accumulated_seconds=50, time_paused=True,
        )
        item_loose = DevelopmentWorkItem(
            chat_id=loose.id, identity_key=f"thread:{loose.id}",
            ticket_title="Loose work", time_accumulated_seconds=50, time_paused=True,
        )
        db.add_all([item_assigned, item_loose])
        db.flush()
        short = DevelopmentTimeEntry(
            work_item_id=item_loose.id, chat_id=loose.id,
            started_at=datetime(2026, 10, 30, 14, 6, 0, 114898),
            ended_at=datetime(2026, 10, 30, 14, 6, 8, 114898),
            seconds=8, source="auto_pause",
            created_at=datetime(2026, 10, 30, 14, 6, 8, 200000),
        )
        assigned_row = DevelopmentTimeEntry(
            work_item_id=item_assigned.id, chat_id=assigned.id,
            started_at=datetime(2026, 10, 30, 9, 0, 0),
            ended_at=datetime(2026, 10, 30, 10, 0, 0),
            seconds=3600, source="play",
            created_at=datetime(2026, 10, 30, 10, 0, 0),
        )
        orphan = DevelopmentTimeEntry(
            id=900, work_item_id=424242, chat_id=424242,
            started_at=datetime(2026, 10, 24, 1, 2, 3, 4),
            ended_at=datetime(2026, 10, 24, 1, 2, 11, 4),
            seconds=8, source="manual",
            created_at=datetime(2026, 10, 24, 1, 2, 11, 4),
        )
        crossing = DevelopmentTimeEntry(
            work_item_id=item_loose.id, chat_id=loose.id,
            started_at=datetime(2026, 10, 22, 21, 0, 0),
            ended_at=datetime(2026, 10, 23, 8, 0, 0),
            seconds=39600, source="auto_pause",
            created_at=datetime(2026, 10, 23, 8, 0, 0),
        )
        db.add_all([short, assigned_row, orphan, crossing])
        db.flush()
        ids = {
            "project_id": int(project.id),
            "loose_chat": int(loose.id),
            "assigned_chat": int(assigned.id),
            "loose_item": int(item_loose.id),
            "assigned_item": int(item_assigned.id),
            "short": int(short.id),
            "assigned": int(assigned_row.id),
            "orphan": int(orphan.id),
            "crossing": int(crossing.id),
        }
    app = FastAPI()
    router = APIRouter()
    register_routes(router, None, get_session=get_session, backup_dir=backup_dir)
    app.include_router(router, prefix="/api")
    state = {
        "get_session": get_session,
        "backup_dir": backup_dir,
        "ids": ids,
        "client": TestClient(app),
    }
    try:
        yield state
    finally:
        detach_reader()


def _export(state, durable_id):
    manifest = export_entries(state["get_session"], state["backup_dir"], [durable_id])
    return manifest


def _delete(state, durable_id, manifest, **overrides):
    row = next(item for item in manifest["rows"] if item["durable_id"] == durable_id)
    body = {
        "durable_id": durable_id,
        "confirm": "delete-time-entry",
        "fingerprint": row["fingerprint"],
        "backup_id": manifest["backup_id"],
        "allow_assigned": False,
        **WINDOW,
    }
    body.update(overrides)
    return delete_entry(state["get_session"], state["backup_dir"], body)


def test_development_router_registers_the_ledger_module():
    source = (_ROOT / "distr/gui/web/routes/development/__init__.py").read_text(encoding="utf-8")
    assert "time_ledger" in source.split("for module in", 1)[1]


def test_projection_ids_are_not_ledger_rows():
    for value in ("b413", "pi_local:abc", "codex_local:abc", "cursor_local:abc", "claude_local:abc", "execution:4", "chat:158"):
        with pytest.raises(LedgerError) as caught:
            parse_durable_id(value)
        assert caught.value.status == 409
        assert caught.value.code == "not_a_time_ledger_row"


def test_export_delete_restore_preserves_microseconds_and_orphan_refs(ledger):
    state = ledger
    durable = f"time:{state['ids']['orphan']}"
    before_parents = _parent_counts(state)
    digest_others = ledger_digest(state["get_session"])
    manifest = _export(state, durable)
    fields = manifest["rows"][0]["fields"]
    assert fields["started_at"] == "2026-10-24T01:02:03.000004"
    assert fields["work_item_id"] == 424242 and fields["chat_id"] == 424242
    assert fields["seconds"] == 8
    deleted = _delete(state, durable, manifest)
    assert deleted["deleted"] is True and deleted["effects"]["timer_counter_changed"] is False
    with state["get_session"]() as db:
        assert db.get(DevelopmentTimeEntry, 900) is None
    restored = restore_backup(state["get_session"], state["backup_dir"], {
        "backup_id": manifest["backup_id"], "confirm": "restore-time-ledger",
    })
    assert restored["restored"] == [durable]
    again = restore_backup(state["get_session"], state["backup_dir"], {
        "backup_id": manifest["backup_id"], "confirm": "restore-time-ledger",
    })
    assert again["already_present"] == [durable] and again["restored"] == []
    with state["get_session"]() as db:
        row = db.get(DevelopmentTimeEntry, 900)
        assert row.work_item_id == 424242 and row.chat_id == 424242
        assert row.started_at == datetime(2026, 10, 24, 1, 2, 3, 4)
        assert row.seconds == 8 and row.source == "manual"
        assert db.get(Chat, 424242) is None
        assert db.get(DevelopmentWorkItem, 424242) is None
    assert _parent_counts(state) == before_parents
    assert ledger_digest(state["get_session"]) == digest_others


def test_assigned_live_window_and_backup_guards(ledger):
    state = ledger
    assigned = f"time:{state['ids']['assigned']}"
    short = f"time:{state['ids']['short']}"
    crossing = f"time:{state['ids']['crossing']}"
    digest = ledger_digest(state["get_session"])
    manifest = export_entries(state["get_session"], state["backup_dir"], [assigned, short, crossing])

    with pytest.raises(LedgerError) as assigned_error:
        _delete(state, assigned, manifest)
    assert assigned_error.value.code == "assigned"

    with state["get_session"]() as db:
        item = db.get(DevelopmentWorkItem, state["ids"]["loose_item"])
        item.time_paused = False
    with pytest.raises(LedgerError) as live_error:
        _delete(state, short, manifest)
    assert live_error.value.code == "live_timer"
    with state["get_session"]() as db:
        item = db.get(DevelopmentWorkItem, state["ids"]["loose_item"])
        item.time_paused = True
        assert item.time_accumulated_seconds == 50

    with pytest.raises(LedgerError) as window_error:
        _delete(state, crossing, manifest)
    assert window_error.value.code == "outside_window"

    with pytest.raises(LedgerError) as stale:
        delete_entry(state["get_session"], state["backup_dir"], {
            "durable_id": short,
            "confirm": "delete-time-entry",
            "fingerprint": next(row["fingerprint"] for row in manifest["rows"] if row["durable_id"] == short),
            "backup_id": manifest["backup_id"],
            **WINDOW,
        }, now=datetime.now(UTC) + timedelta(hours=25))
    assert stale.value.code == "backup_stale"

    path = state["backup_dir"] / f"{manifest['backup_id']}.json"
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("auto_pause", "play", 1), encoding="utf-8")
    with pytest.raises(LedgerError) as tamper:
        _delete(state, short, manifest)
    assert tamper.value.code == "backup_tampered"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(LedgerError) as missing:
        delete_entry(state["get_session"], state["backup_dir"], {
            "durable_id": short, "confirm": "delete-time-entry",
            "fingerprint": "a" * 64, "backup_id": "ab" * 16, **WINDOW,
        })
    assert missing.value.code == "backup_missing"
    with pytest.raises(LedgerError) as escaped:
        delete_entry(state["get_session"], state["backup_dir"], {
            "durable_id": short, "confirm": "delete-time-entry",
            "fingerprint": "a" * 64, "backup_id": "../secrets", **WINDOW,
        })
    assert escaped.value.code == "path_rejected"
    assert ledger_digest(state["get_session"]) == digest


def test_restore_collision_rolls_back(ledger):
    state = ledger
    first = f"time:{state['ids']['short']}"
    second = f"time:{state['ids']['orphan']}"
    manifest = export_entries(state["get_session"], state["backup_dir"], [first, second])
    _delete(state, first, manifest)
    with state["get_session"]() as db:
        row = db.get(DevelopmentTimeEntry, state["ids"]["orphan"])
        row.seconds = 99
    with pytest.raises(LedgerError) as collision:
        restore_backup(state["get_session"], state["backup_dir"], {
            "backup_id": manifest["backup_id"], "confirm": "restore-time-ledger",
        })
    assert collision.value.code == "collision"
    with state["get_session"]() as db:
        assert db.get(DevelopmentTimeEntry, state["ids"]["short"]) is None
        assert db.get(DevelopmentTimeEntry, state["ids"]["orphan"]).seconds == 99


def test_create_is_idempotent_and_visible_to_the_period_reader(ledger):
    state = ledger
    created = create_entry(state["get_session"], dict(CREATE_EXAMPLE))
    assert created["idempotent"] is False
    assert created["stored_seconds"] == 8 and created["derived_seconds"] == 8
    assert created["seconds_origin"] == "stored"
    assert created["started_at"] == CREATE_EXAMPLE["started_at"]
    replay = create_entry(state["get_session"], dict(CREATE_EXAMPLE))
    assert replay["idempotent"] is True and replay["durable_id"] == created["durable_id"]
    changed = dict(CREATE_EXAMPLE)
    changed["label"] = "Different label"
    with pytest.raises(LedgerError) as conflict:
        create_entry(state["get_session"], changed)
    assert conflict.value.code == "idempotency_conflict"
    mismatched = dict(CREATE_EXAMPLE)
    mismatched["idempotency_key"] = "other-key"
    mismatched["seconds"] = 0
    mismatched["label"] = "Stored zero"
    zero = create_entry(state["get_session"], mismatched)
    assert zero["stored_seconds"] == 0 and zero["derived_seconds"] == 8 and zero["seconds_origin"] == "stored"
    blocks, _counts = month.collect_database_blocks(
        datetime(2026, 10, 30, 0, 0), datetime(2026, 10, 31, 0, 0), state["get_session"],
    )
    found = [block for block in blocks if block["ref"] == created["durable_id"]]
    assert len(found) == 1
    assert found[0]["project_id"] is None
    assert found[0]["source"] == "historical_time"
    assert found[0]["source_label"] == "Recovered interval"
    assert int((found[0]["end"] - found[0]["start"]).total_seconds()) == 8
    replay_without_key = create_entry(state["get_session"], {**CREATE_EXAMPLE, "idempotency_key": None, "label": "Recovered interval"})
    assert replay_without_key["idempotent"] is True and replay_without_key["durable_id"] == created["durable_id"]
    with pytest.raises(LedgerError) as naive:
        create_entry(state["get_session"], {**CREATE_EXAMPLE, "idempotency_key": "naive", "started_at": "2026-10-30T16:06:00"})
    assert naive.value.code == "invalid_time"
    with pytest.raises(LedgerError) as missing_project:
        create_entry(state["get_session"], {**CREATE_EXAMPLE, "idempotency_key": "missing-project", "project_id": 99999})
    assert missing_project.value.code == "invalid_project"
    with pytest.raises(LedgerError) as native:
        create_entry(state["get_session"], {**CREATE_EXAMPLE, "idempotency_key": "native", "source": "pi_local"})
    assert native.value.code == "invalid_source"


def test_assigned_rows_cannot_be_deleted(ledger):
    state = ledger
    durable = f"time:{state['ids']['assigned']}"
    manifest = _export(state, durable)
    with pytest.raises(LedgerError) as denied:
        _delete(state, durable, manifest)
    assert denied.value.code == "assigned"
    with pytest.raises(LedgerError) as override:
        _delete(state, durable, manifest, allow_assigned=True, confirm="delete-assigned-time-entry")
    assert override.value.code == "assigned"
    with state["get_session"]() as db:
        item = db.get(DevelopmentWorkItem, state["ids"]["assigned_item"])
        assert item.time_accumulated_seconds == 50
        assert db.get(DevelopmentTimeEntry, state["ids"]["assigned"]) is not None


def test_create_does_not_duplicate_an_assigned_interval(ledger):
    state = ledger
    with pytest.raises(LedgerError) as caught:
        create_entry(state["get_session"], {
            "started_at": "2026-10-30T11:00:00+02:00",
            "ended_at": "2026-10-30T12:00:00+02:00",
            "project_id": state["ids"]["project_id"],
            "source": "manual",
            "label": "Same assigned hour",
            "idempotency_key": "assigned-dup",
        })
    assert caught.value.code == "duplicate_assigned_evidence"
    with state["get_session"]() as db:
        row = db.get(DevelopmentTimeEntry, state["ids"]["assigned"])
        assert row.seconds == 3600 and row.source == "play"


def test_http_examples_do_not_touch_rows_without_a_verified_backup(ledger):
    client = ledger["client"]
    digest = ledger_digest(ledger["get_session"])
    literal_delete = {
        "durable_id": "time:78",
        "confirm": "delete-time-entry",
        "fingerprint": "<row fingerprint from the backup>",
        "backup_id": "<backup_id from the export response>",
        **WINDOW,
    }
    denied = client.post("/api/workflows/studio/time-ledger/delete", json=literal_delete)
    assert denied.status_code in {400, 409}
    missing = client.post("/api/workflows/studio/time-ledger/backups", json={"durable_ids": ["time:78"]})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "not_found"
    projection = client.post("/api/workflows/studio/time-ledger/delete", json={**literal_delete, "durable_id": "b413"})
    assert projection.status_code == 409
    assert projection.json()["detail"]["code"] == "not_a_time_ledger_row"
    extra = client.post("/api/workflows/studio/time-ledger/entries", json={**CREATE_EXAMPLE, "transcript": "secret"})
    assert extra.status_code == 422
    created = client.post("/api/workflows/studio/time-ledger/entries", json=CREATE_EXAMPLE)
    assert created.status_code == 200
    body = created.json()
    assert body["durable_id"].startswith("historical:")
    assert body["derived_seconds"] == 8
    replay = client.post("/api/workflows/studio/time-ledger/entries", json=CREATE_EXAMPLE)
    assert replay.json()["idempotent"] is True
    exported = client.post("/api/workflows/studio/time-ledger/backups", json={"durable_ids": [f"time:{ledger['ids']['short']}"]})
    assert exported.status_code == 200
    manifest = exported.json()
    assert manifest["schema"] == "decisions-time-ledger-backup/v2"
    assert "checksum" in manifest and "rows" in manifest
    assert manifest["rows"][0]["fields"]["started_at"].endswith(".114898")
    fetched = client.get(f"/api/workflows/studio/time-ledger/backups/{manifest['backup_id']}")
    assert fetched.json()["checksum"] == manifest["checksum"]
    removed = client.post("/api/workflows/studio/time-ledger/delete", json={
        "durable_id": f"time:{ledger['ids']['short']}",
        "confirm": "delete-time-entry",
        "fingerprint": manifest["rows"][0]["fingerprint"],
        "backup_id": manifest["backup_id"],
        **WINDOW,
    })
    assert removed.status_code == 200 and removed.json()["deleted"] is True
    brought_back = client.post("/api/workflows/studio/time-ledger/restore", json={
        "backup_id": manifest["backup_id"], "confirm": "restore-time-ledger",
    })
    assert brought_back.status_code == 200
    assert f"time:{ledger['ids']['short']}" in brought_back.json()["restored"]
    assert ledger_digest(ledger["get_session"]) != digest


def test_documented_guide_preserves_the_mac_bytes_outside_the_addition():
    path = Path(__file__).resolve().parents[2] / "docs" / "api.md"
    text = path.read_text(encoding="utf-8")
    assert DOC_BULLET in text
    assert "Do not run this against the live app" in text
    assert "/api/workflows/studio/time-ledger/backups" in text
    assert "/api/workflows/studio/time-ledger/entries" in text
    assert "/api/workflows/studio/time-ledger/delete" in text
    assert "/api/workflows/studio/time-ledger/restore" in text
    assert "delete-time-entry" in text and "restore-time-ledger" in text
    stripped = text.replace(DOC_BULLET, "", 1)
    start = stripped.index("\n## Time ledger\n")
    end = stripped.index("\n## Known gaps\n")
    restored = stripped[:start] + stripped[end:]
    assert hashlib.sha256(restored.encode("utf-8")).hexdigest() == ORIGINAL_DOC_SHA256
    example = _json_after(text, "### Example: create historical entry")
    assert example == CREATE_EXAMPLE
    delete_example = _json_after(text, "### Example: delete one durable row")
    assert set(delete_example) == {
        "durable_id", "confirm", "fingerprint", "backup_id", "window_start", "window_end",
    }
    restore_example = _json_after(text, "### Example: restore a backup")
    assert restore_example["confirm"] == "restore-time-ledger"


def _json_after(text: str, heading: str) -> dict:
    chunk = text.split(heading, 1)[1]
    fenced = chunk.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(fenced)


def test_restore_after_parent_project_changes_keeps_the_parent(ledger):
    state = ledger
    durable = f"time:{state['ids']['short']}"
    backup = _export(state, durable)
    _delete(state, durable, backup)
    with state["get_session"]() as db:
        db.get(Chat, state["ids"]["loose_chat"]).project_id = state["ids"]["project_id"]
    result = restore_backup(state["get_session"], state["backup_dir"], {
        "backup_id": backup["backup_id"], "confirm": "restore-time-ledger",
    })
    assert result["restored"] == [durable]
    assert result["attribution_notes"][0]["parent_changed"] is False
    assert result["attribution_notes"][0]["current_attribution"]["project_id"] == state["ids"]["project_id"]
    with state["get_session"]() as db:
        row = db.get(DevelopmentTimeEntry, state["ids"]["short"])
        assert time_ledger_fields(row) == backup["rows"][0]["fields"]
        assert db.get(Chat, state["ids"]["loose_chat"]).project_id == state["ids"]["project_id"]


def time_ledger_fields(row):
    from distr.core.workflow.time_ledger import _time_fields
    return _time_fields(row)


def test_create_sees_execution_inserted_before_the_write(ledger, monkeypatch):
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.workflow import time_ledger as api
    original = api.ensure_schema
    pid = ledger["ids"]["project_id"]
    once = {"done": False}

    def raced(factory):
        if not once["done"]:
            once["done"] = True
            with factory() as db:
                db.add(ProjectExecutionSession(
                    project_id=pid, started_at=datetime(2026, 10, 30, 14, 6),
                    completed_at=datetime(2026, 10, 30, 14, 6, 8),
                    updated_at=datetime(2026, 10, 30, 14, 6, 8),
                    status="completed", route_backend="pi",
                ))
        return original(factory)

    monkeypatch.setattr(api, "ensure_schema", raced)
    with pytest.raises(LedgerError):
        create_entry(ledger["get_session"], {**CREATE_EXAMPLE, "project_id": pid})
    with ledger["get_session"]() as db:
        assert db.query(api.HistoricalTimeEntry).count() == 0


def test_simultaneous_same_key_returns_one_resource(ledger):
    import threading
    import concurrent.futures
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from distr.core.workflow import time_ledger as api
    api.ensure_schema(ledger["get_session"])
    barrier = threading.Barrier(2)

    def wait(session, ctx, instances):
        if any(isinstance(row, api.HistoricalTimeEntry) for row in session.new):
            barrier.wait(timeout=5)

    event.listen(Session, "before_flush", wait)

    def create():
        try:
            return create_entry(ledger["get_session"], dict(CREATE_EXAMPLE))
        except LedgerError as exc:
            return {"error": exc.code}

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
    finally:
        event.remove(Session, "before_flush", wait)
    assert all("durable_id" in row for row in results), results
    assert results[0]["durable_id"] == results[1]["durable_id"]


def test_idempotent_replay_after_later_execution(ledger):
    from distr.core.db.kanban import ProjectExecutionSession
    pid = ledger["ids"]["project_id"]
    body = {**CREATE_EXAMPLE, "project_id": pid}
    first = create_entry(ledger["get_session"], body)
    with ledger["get_session"]() as db:
        db.add(ProjectExecutionSession(
            project_id=pid, started_at=datetime(2026, 10, 30, 14, 6),
            completed_at=datetime(2026, 10, 30, 14, 6, 8),
            updated_at=datetime(2026, 10, 30, 14, 6, 8),
            status="completed", route_backend="pi",
        ))
    replay = create_entry(ledger["get_session"], body)
    assert replay["idempotent"] is True and replay["durable_id"] == first["durable_id"]


def _parent_counts(state) -> dict:
    with state["get_session"]() as db:
        return {
            "chats": db.query(Chat).count(),
            "work_items": db.query(DevelopmentWorkItem).count(),
            "projects": db.query(Project).count(),
        }
