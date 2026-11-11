"""Plan open returns at once; the project scan runs on a background worker."""
from __future__ import annotations

import contextlib
import logging
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.projects import Project
from distr.core.planning import service


@pytest.fixture()
def plan_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'plan-bg.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(service, "get_session", get_session)
    monkeypatch.setattr(service, "_SCANS_IN_FLIGHT", set())
    monkeypatch.setattr(service, "_SCAN_STATUS", {})
    folder = tmp_path / "project"
    (folder / "frontend" / "src").mkdir(parents=True)
    (folder / "frontend" / "src" / "routes.jsx").write_text(
        'export const routes = [{ url: "/", name: "home" }, { url: "/auctions", name: "auctions" }];', encoding="utf-8"
    )
    with get_session() as db:
        project = Project(name="Bg Plan", folder_location=str(folder))
        db.add(project)
        db.commit()
        return {"project_id": project.id}


def _open(plan_db, key="decisions:bg"):
    return service.open_project_plan(
        board_key=key, board_provider="decisions", board_name="Bg", project_id=plan_db["project_id"], defer_scan=True
    )


def _wait(workspace_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        status = service.plan_scan_status(workspace_id)
        if status["state"] != "scanning":
            return status
        time.sleep(0.02)
    raise AssertionError("scan did not settle")


def test_open_returns_immediately_with_scanning_flag_then_scan_lands(plan_db, monkeypatch):
    gate = threading.Event()
    real = service.materialize_project_scan

    def slow(workspace_id, **kwargs):
        assert gate.wait(5)
        return real(workspace_id, **kwargs)

    monkeypatch.setattr(service, "materialize_project_scan", slow)
    started = time.time()
    opened = _open(plan_db)
    assert time.time() - started < 2  # did not wait for the scan
    assert opened["scan"]["state"] == "scanning"
    by_type = {item["item_type"]: item for item in opened["items"]}
    assert "prd" in by_type and "/auctions" not in by_type["prd"]["content"]  # scaffold only so far
    gate.set()
    assert _wait(opened["id"])["state"] == "done"
    later = {item["item_type"]: item for item in service.get_workspace(opened["id"])["items"]}
    assert "/auctions" in later["prd"]["content"]


def test_concurrent_opens_start_only_one_scan_per_project(plan_db, monkeypatch):
    gate = threading.Event()
    calls = []

    def slow(workspace_id, **kwargs):
        calls.append(workspace_id)
        gate.wait(5)
        return {}

    monkeypatch.setattr(service, "materialize_project_scan", slow)
    first = _open(plan_db)
    second = _open(plan_db)
    assert first["scan"]["state"] == second["scan"]["state"] == "scanning"
    assert service.start_background_scan(first["id"], plan_db["project_id"]) is False
    gate.set()
    _wait(first["id"])
    assert len(calls) == 1
    # After it settles a new open may scan again.
    gate.clear(); gate.set()
    _open(plan_db)
    _wait(first["id"])
    assert len(calls) == 2


def test_scan_error_is_logged_recorded_and_plan_stays_usable(plan_db, monkeypatch, caplog):
    def boom(workspace_id, **kwargs):
        raise ValueError("The linked project folder is unavailable.")

    monkeypatch.setattr(service, "materialize_project_scan", boom)
    with caplog.at_level(logging.WARNING, logger=service.__name__):
        opened = _open(plan_db)
        status = _wait(opened["id"])
    assert status == {"state": "error", "error": "The linked project folder is unavailable.", "finished_at": status["finished_at"]}
    assert "Background plan scan failed" in caplog.text
    assert service.get_workspace(opened["id"])["items"]  # scaffold still there
    assert service.start_background_scan(opened["id"], plan_db["project_id"]) is True  # guard released
    _wait(opened["id"])


def test_sync_open_still_scans_inline_and_status_is_idle_by_default(plan_db):
    opened = service.open_project_plan(
        board_key="decisions:sync", board_provider="decisions", board_name="Sync", project_id=plan_db["project_id"]
    )
    assert "scan" not in opened
    assert "/auctions" in {i["item_type"]: i for i in opened["items"]}["prd"]["content"]
    assert service.plan_scan_status(opened["id"]) == {"state": "idle", "error": ""}


@pytest.mark.parametrize('coverage', ['none', 'partial'])
def test_background_status_retains_inventory_limitations_without_workspace_content(monkeypatch, coverage):
    monkeypatch.setattr(service, '_SCAN_STATUS', {})
    monkeypatch.setattr(service, '_SCANS_IN_FLIGHT', {42})
    monkeypatch.setattr(service, 'materialize_project_scan', lambda *args, **kwargs: {
        'summary': {'coverage':coverage, 'limitations':['Only supported source formats were inventoried.'],
                    'route_count':0, 'model_count':0, 'adequate':False},
        'workspace': {'private_content':'Not scan status'}, 'artifacts': {'private':'Not scan status'}})
    service._run_background_scan(17, 42)
    status = service.plan_scan_status(17)
    assert status['state'] == 'done'
    assert status['coverage'] == coverage
    assert status['limitations'] == ['Only supported source formats were inventoried.']
    assert status['route_count'] == status['model_count'] == 0
    assert status['adequate'] is False
    assert 'private_content' not in str(status) and 'workspace' not in status and 'artifacts' not in status


def test_background_mock_without_inventory_summary_does_not_invent_warning(monkeypatch):
    monkeypatch.setattr(service, '_SCAN_STATUS', {})
    monkeypatch.setattr(service, '_SCANS_IN_FLIGHT', {42})
    monkeypatch.setattr(service, 'materialize_project_scan', lambda *args, **kwargs: {})
    service._run_background_scan(17, 42)
    assert service.plan_scan_status(17) == {'state':'done', 'error':'', 'finished_at':service.plan_scan_status(17)['finished_at']}


def test_routes_defer_scan_and_expose_status():
    from pathlib import Path

    source = (Path(service.__file__).resolve().parents[2] / "gui/web/routes/development/planning.py").read_text()
    assert "defer_scan=True," in source
    assert '"/workflows/studio/plan-workspaces/{workspace_id}/scan-status"' in source
