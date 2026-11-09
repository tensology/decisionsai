"""Timer-window read: chart overlap, including rows History hides. No writes."""
from __future__ import annotations

import contextlib
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
from distr.core.reports.time_window import list_timer_rows, window_bounds


def test_window_is_johannesburg_midnight():
    lo, hi = window_bounds(__import__("datetime").date(2026, 10, 23), __import__("datetime").date(2026, 11, 8))
    assert lo == datetime(2026, 10, 22, 22, 0)
    assert hi == datetime(2026, 11, 8, 22, 0)


def test_lists_pre_reset_rows_and_does_not_write(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'timers.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    with get_session() as db:
        project = Project(name="Alpha", folder_location=str(tmp_path / "alpha"))
        db.add(project)
        db.flush()
        chat = Chat(title="Thread", project_id=project.id, params="{}")
        loose = Chat(title="Loose", project_id=None, params="{}")
        db.add_all([chat, loose])
        db.flush()
        item = DevelopmentWorkItem(chat_id=chat.id, identity_key=f"t:{chat.id}", project_id=project.id, ticket_title="Ticket title")
        loose_item = DevelopmentWorkItem(chat_id=loose.id, identity_key=f"t:{loose.id}")
        db.add_all([item, loose_item])
        db.flush()
        kept = DevelopmentTimeEntry(
            work_item_id=item.id, chat_id=chat.id,
            started_at=datetime(2026, 10, 23, 8, 0), ended_at=datetime(2026, 10, 23, 9, 0),
            seconds=3600, source="play",
        )
        hidden = DevelopmentTimeEntry(
            work_item_id=loose_item.id, chat_id=loose.id,
            started_at=datetime(2026, 10, 23, 10, 0), ended_at=datetime(2026, 10, 23, 10, 5),
            seconds=300, source="auto_pause",
        )
        db.add_all([kept, hidden])
        db.flush()
        reset = DevelopmentTimeEntry(
            work_item_id=loose_item.id, chat_id=loose.id,
            started_at=datetime(2026, 10, 23, 11, 0), ended_at=datetime(2026, 10, 23, 11, 0),
            seconds=0, source="reset",
        )
        outside = DevelopmentTimeEntry(
            work_item_id=item.id, chat_id=chat.id,
            started_at=datetime(2026, 9, 1, 8, 0), ended_at=datetime(2026, 9, 1, 9, 0),
            seconds=3600, source="play",
        )
        tiny = DevelopmentTimeEntry(
            work_item_id=item.id, chat_id=chat.id,
            started_at=datetime(2026, 10, 24, 12, 0, 0), ended_at=datetime(2026, 10, 24, 12, 0, 0, 200000),
            seconds=0, source="play",
        )
        db.add_all([reset, outside, tiny])
        before = db.query(DevelopmentTimeEntry).count()

    result = list_timer_rows(__import__("datetime").date(2026, 10, 23), __import__("datetime").date(2026, 11, 8), get_session)
    ids = {row["durable_id"] for row in result["items"]}
    assert "time:" + str(kept.id) in ids
    assert "time:" + str(hidden.id) in ids
    assert all(row["source"] != "reset" for row in result["items"])
    assert all(not row["durable_id"].endswith(str(outside.id)) or row["chat_id"] != outside.chat_id for row in result["items"])
    assert str(outside.id) not in {str(row["id"]) for row in result["items"]}
    hidden_row = next(row for row in result["items"] if row["id"] == hidden.id)
    assert hidden_row["hidden_from_history"] is True
    assert hidden_row["project_id"] is None
    kept_row = next(row for row in result["items"] if row["id"] == kept.id)
    assert kept_row["project_id"] == project.id
    assert kept_row["stored_ticket_title"] == "Ticket title"
    assert kept_row["evidence"] == "stored timer row"
    tiny_row = next(row for row in result["items"] if row["id"] == tiny.id)
    assert tiny_row["chart_keeps_row"] is False
    with get_session() as db:
        assert db.query(DevelopmentTimeEntry).count() == before


def test_project_hint_uses_work_item_then_parent_and_does_not_write(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'assign.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    with get_session() as db:
        via_item = Project(name="Item", folder_location=str(tmp_path / "item"))
        via_parent = Project(name="Parent", folder_location=str(tmp_path / "parent"))
        db.add_all([via_item, via_parent])
        db.flush()
        parent = Chat(title="Parent", project_id=via_parent.id, params="{}")
        db.add(parent)
        db.flush()
        item_chat = Chat(title="Item chat", project_id=None, params="{}")
        child = Chat(title="Child", project_id=None, parent_id=parent.id, params="{}")
        loose = Chat(title="Loose", project_id=None, params="{}")
        db.add_all([item_chat, child, loose])
        db.flush()
        item = DevelopmentWorkItem(chat_id=item_chat.id, identity_key="item", project_id=via_item.id)
        child_item = DevelopmentWorkItem(chat_id=child.id, identity_key="child", project_id=None)
        loose_item = DevelopmentWorkItem(chat_id=loose.id, identity_key="loose", project_id=None)
        db.add_all([item, child_item, loose_item])
        db.flush()
        rows = [
            DevelopmentTimeEntry(work_item_id=item.id, chat_id=item_chat.id, started_at=datetime(2026, 10, 30, 8, 0), ended_at=datetime(2026, 10, 30, 9, 0), seconds=3600, source="play"),
            DevelopmentTimeEntry(work_item_id=child_item.id, chat_id=child.id, started_at=datetime(2026, 10, 30, 10, 0), ended_at=datetime(2026, 10, 30, 11, 0), seconds=3600, source="play"),
            DevelopmentTimeEntry(work_item_id=loose_item.id, chat_id=loose.id, started_at=datetime(2026, 10, 30, 12, 0), ended_at=datetime(2026, 10, 30, 13, 0), seconds=3600, source="play"),
        ]
        db.add_all(rows)
        db.flush()
        ids = [int(row.id) for row in rows]
        before = [(row.work_item_id, row.chat_id) for row in db.query(DevelopmentTimeEntry).all()]
    result = list_timer_rows(__import__("datetime").date(2026, 10, 30), __import__("datetime").date(2026, 10, 30), get_session)
    by_id = {row["id"]: row for row in result["items"]}
    assert by_id[ids[0]]["project_id"] == via_item.id and by_id[ids[0]]["project_provenance"] == "work_item"
    assert by_id[ids[1]]["project_id"] == via_parent.id and by_id[ids[1]]["project_provenance"] == "parent_chat"
    assert by_id[ids[2]]["project_id"] is None and by_id[ids[2]]["project_provenance"] == "unknown"
    with get_session() as db:
        assert [(row.work_item_id, row.chat_id) for row in db.query(DevelopmentTimeEntry).all()] == before
