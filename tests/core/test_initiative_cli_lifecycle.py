from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.kanban import KanbanBoard, KanbanTicket
from distr.core.db.projects import Project
from distr.core.initiative.action_handlers import run_project_cli_tasks
from distr.core.kanban.lifecycle import ensure_delivery_lanes


def _delivery_case():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    project = Project(name="Real delivery", folder_location="/tmp/real-delivery")
    session.add(project)
    session.flush()
    board = KanbanBoard(
        name="Real delivery",
        source="database",
        default_project_id=project.id,
    )
    session.add(board)
    session.flush()
    lanes = ensure_delivery_lanes(session, board.id)
    ticket = KanbanTicket(
        lane_id=lanes["Backlog"].id,
        title="Make the green button black",
        linked_project_id=project.id,
    )
    session.add(ticket)
    session.commit()
    return engine, session, ticket.id, {name: lane.id for name, lane in lanes.items()}


def _patch_cli_dependencies(monkeypatch, session, *, success, empty_success=False):
    @contextmanager
    def fake_get_session():
        yield session

    class FakeResult:
        def to_dict(self):
            return {
                "success": success,
                "backend_id": "local-worker",
                "output": (
                    "" if empty_success else "Implemented and checked."
                ) if success else "Worker failed safely.",
                "workspace_state_delta": {
                    "changed": bool(success and not empty_success),
                    "modified": ["styles.css"] if success and not empty_success else [],
                },
            }

    async def fake_run_project_task(*args, **kwargs):
        # The lane transition is committed before a potentially long worker
        # starts, so another session/the UI can observe In Progress here.
        session.expire_all()
        assert session.query(KanbanTicket).first().lane.name == "In Progress"
        assert kwargs["adapter_options"] == {
            "model_provider": "ollama",
            "required_capabilities": ["code", "files"],
            "task_intent": "implementation",
            "skills": [],
            "mutation_expected": True,
        }
        return FakeResult()

    decision = SimpleNamespace(
        to_route_dict=lambda: {
            "complexity": "low",
            "backend": "local-worker",
            "model": "local-code-model",
            "model_provider": "ollama",
            "task_profile": {"intent": "implementation"},
            "skills": [],
        }
    )
    monkeypatch.setattr("distr.core.db.get_session", fake_get_session)
    monkeypatch.setattr(
        "distr.core.kanban.ticket_cli_context.build_kanban_ticket_cli_instruction",
        lambda *args, **kwargs: "Make the green button black",
    )
    monkeypatch.setattr(
        "distr.core.orchestrator_routing.resolve_execution_route",
        lambda **kwargs: decision,
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.run_project_task",
        fake_run_project_task,
    )


def test_successful_direct_cli_ticket_moves_backlog_to_progress_then_qa(monkeypatch):
    engine, session, ticket_id, lanes = _delivery_case()
    try:
        _patch_cli_dependencies(monkeypatch, session, success=True)

        result = run_project_cli_tasks({"ticket_ids": [ticket_id]})

        session.expire_all()
        ticket = session.query(KanbanTicket).filter_by(id=ticket_id).one()
        assert result["success"] is True
        assert result["message"] == (
            "Completed “Make the green button black”. It is waiting in QA for your review."
        )
        assert ticket.lane_id == lanes["QA"]
        assert ticket.lane.name == "QA"
    finally:
        session.close()
        engine.dispose()


def test_failed_direct_cli_ticket_stays_in_progress_for_recovery(monkeypatch):
    engine, session, ticket_id, lanes = _delivery_case()
    try:
        _patch_cli_dependencies(monkeypatch, session, success=False)

        result = run_project_cli_tasks({"ticket_ids": [ticket_id]})

        session.expire_all()
        ticket = session.query(KanbanTicket).filter_by(id=ticket_id).one()
        assert result["success"] is False
        assert result["message"].startswith(
            "I could not complete “Make the green button black”. It remains In Progress."
        )
        assert ticket.lane_id == lanes["In Progress"]
        assert ticket.lane.name == "In Progress"
    finally:
        session.close()
        engine.dispose()


def test_empty_success_without_mutation_stays_in_progress(monkeypatch):
    engine, session, ticket_id, lanes = _delivery_case()
    try:
        _patch_cli_dependencies(monkeypatch, session, success=True, empty_success=True)

        result = run_project_cli_tasks({"ticket_ids": [ticket_id]})

        session.expire_all()
        ticket = session.query(KanbanTicket).filter_by(id=ticket_id).one()
        assert result["success"] is False
        assert "claimed success" in result["results"][0]["error"]
        assert ticket.lane_id == lanes["In Progress"]
    finally:
        session.close()
        engine.dispose()
