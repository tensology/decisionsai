from __future__ import annotations

import contextlib
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow, DevelopmentWorkItem
from distr.core.workflow.work_dispatch import dispatch_work_item


@contextlib.contextmanager
def _session(factory):
    db = factory()
    try:
        yield db
        db.commit()
    finally:
        db.close()


def test_dispatch_prepares_thread_before_starting_run(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch._resolve_scope",
        lambda **kwargs: {
            "title": "Open-ended work",
            "project_id": None,
            "board_id": None,
            "board_provider": None,
            "board_key": None,
            "board_ticket_key": None,
            "board_ticket_title": None,
            "board_ticket_lane": None,
        },
    )

    def ensure(**kwargs):
        calls.append(("thread", kwargs))
        return 17

    def start(workflow_id, **kwargs):
        calls.append(("run", {"workflow_id": workflow_id, **kwargs}))
        return {"run_id": 99, "status": "running"}

    monkeypatch.setattr("distr.core.workflow.development_threads.ensure_development_thread", ensure)
    monkeypatch.setattr("distr.core.workflow.service.start_workflow_run", start)
    monkeypatch.setattr(
        "distr.core.workflow.development_control.resume_thread_time",
        lambda chat_id, **_kwargs: calls.append(("time", {"chat_id": chat_id})) or {"chat_id": chat_id},
    )

    result = dispatch_work_item(
        workflow_id=44,
        context="Implement the change",
        source_type="initiative",
        source_ref="proposal-9",
        dispatch_async=True,
    )

    assert [kind for kind, _ in calls] == ["thread", "run", "time"]
    assert calls[1][1]["run_metadata"]["chat_id"] == 17
    assert calls[1][1]["run_metadata"]["work_item_identity"] == "source:initiative:proposal-9"
    orchestration = calls[1][1]["run_metadata"]["orchestration"]
    assert orchestration["owner_chat_id"] == 17
    assert orchestration["agent_role"] == "workflow_orchestrator"
    assert orchestration["execution_kind"] == "deterministic_orchestrator_subagent"
    assert result["development_url"] == "/development/threads/17/"


def test_dispatch_never_starts_run_when_thread_preparation_fails(monkeypatch):
    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch._resolve_scope",
        lambda **kwargs: {
            "title": "Broken preparation",
            "project_id": None,
            "board_id": None,
            "board_provider": None,
            "board_key": None,
            "board_ticket_key": None,
            "board_ticket_title": None,
            "board_ticket_lane": None,
        },
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_threads.ensure_development_thread",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )
    starts = []
    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    import pytest

    with pytest.raises(RuntimeError, match="thread unavailable"):
        dispatch_work_item(workflow_id=44, context="Do not run")
    assert starts == []


def test_dispatch_preserves_external_ticket_identity_when_local_mirror_is_linked(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    with provider() as db:
        workflow = AutoWorkflow(name="Development", workflow_type="manual")
        board = KanbanBoard(name="Project", source="database")
        db.add_all([workflow, board])
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog")
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(title="Approved", lane_id=lane.id, external_id="E2E-91-001")
        chat = Chat(title="Approved", params=json.dumps({"development": {
            "board_provider": "tensology",
            "board_key": "tensology:91",
            "board_ticket_key": "E2E-91-001",
        }}))
        db.add_all([ticket, chat])
        db.flush()
        db.add(DevelopmentWorkItem(
            chat_id=chat.id,
            identity_key="ticket:tensology:tensology:91:e2e-91-001",
            board_provider="tensology",
            board_key="tensology:91",
            ticket_key="E2E-91-001",
            local_ticket_id=ticket.id,
            workflow_id=workflow.id,
        ))
        workflow_id, ticket_id, chat_id = workflow.id, ticket.id, chat.id

    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: {"run_id": 1, "status": "running"},
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_control.resume_thread_time",
        lambda *args, **kwargs: {},
    )
    result = dispatch_work_item(
        workflow_id=workflow_id,
        chat_id=chat_id,
        ticket_id=ticket_id,
        source_type="studio_thread",
        _session_provider=provider,
    )

    assert result["work_item_identity"] == "ticket:tensology:tensology:91:e2e-91-001"
    with provider() as db:
        item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == chat_id).one()
        assert item.identity_key == result["work_item_identity"]
        assert item.board_provider == "tensology"
