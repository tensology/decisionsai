"""Offline golden smoke for trustworthy Development contracts.

Live harness / Codex CLI smoke stays skipped unless DECISIONS_GOLDEN_LIVE=1.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import DevelopmentWorkItem
from distr.core.reports.service import _safe_duration_seconds, list_time_entries
from distr.core.workflow import development_control as control

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "dev_golden"


@pytest.fixture()
def development_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'development-golden.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(control, "get_session", get_session)
    monkeypatch.setattr("distr.core.reports.service.get_session", get_session)
    with get_session() as db:
        project_root = tmp_path / "project"
        project_root.mkdir()
        project = Project(name="Golden", folder_location=str(project_root))
        db.add(project)
        db.flush()
        chat = Chat(
            title="Golden smoke",
            project_id=project.id,
            params=json.dumps({"development": {}}),
            route_mode="auto",
            execution_profile="code",
            autonomy_level="full",
        )
        db.add(chat)
        db.flush()
        db.add(
            DevelopmentWorkItem(
                chat_id=chat.id,
                identity_key=f"thread:{chat.id}",
                workflow_id=None,
                project_id=project.id,
            )
        )
        db.commit()
        return {"session": get_session, "chat_id": chat.id, "project_id": project.id}


def test_golden_fixture_files_exist():
    assert (FIXTURE_DIR / "README.md").is_file()
    assert (FIXTURE_DIR / "app.py").is_file()
    assert (FIXTURE_DIR / "test_add.py").is_file()
    assert (FIXTURE_DIR / "prompt.txt").is_file()
    assert "return a - b" in (FIXTURE_DIR / "app.py").read_text(encoding="utf-8")
    assert "add(2, 3) == 5" in (FIXTURE_DIR / "test_add.py").read_text(encoding="utf-8")


def test_pause_after_resume_creates_time_entry_for_reports(development_db):
    control.resume_thread_time(development_db["chat_id"])
    with development_db["session"]() as db:
        item = db.query(DevelopmentWorkItem).filter_by(chat_id=development_db["chat_id"]).one()
        item.time_started_at = utc_now_naive() - timedelta(minutes=2)
        db.commit()
    paused = control.pause_thread_time(development_db["chat_id"])
    assert paused["paused"] is True
    assert any(int(entry.get("seconds") or 0) >= 0 for entry in paused.get("entries") or [])

    listed = list_time_entries(limit=20)
    assert any(int(row["chat_id"]) == int(development_db["chat_id"]) for row in listed["items"])


def test_cancelled_report_duration_sanitize_drops_ten_day_gap():
    started = utc_now_naive() - timedelta(days=10)
    completed = utc_now_naive()
    record = SimpleNamespace(started_at=started, completed_at=completed, status="cancelled")
    assert _safe_duration_seconds(record, status="cancelled") is None
    record.status = "completed"
    assert _safe_duration_seconds(record, status="completed") is None  # still > 24h
    short = SimpleNamespace(
        started_at=utc_now_naive() - timedelta(minutes=12),
        completed_at=utc_now_naive(),
        status="completed",
    )
    assert _safe_duration_seconds(short, status="completed") == pytest.approx(12 * 60, abs=5)


@pytest.mark.skipif(
    os.environ.get("DECISIONS_GOLDEN_LIVE") != "1",
    reason="Live harness smoke requires DECISIONS_GOLDEN_LIVE=1",
)
def test_live_harness_golden_smoke():
    raise AssertionError("Live golden harness smoke is not wired in this offline contract suite.")
