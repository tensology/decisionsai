import contextlib
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.work_intake import WorkIntake, WorkIntakeAction, WorkIntakeAttachment
from distr.core.work_intake.service import OrchestratorIntakeService


@pytest.fixture
def service():
    return OrchestratorIntakeService()


@pytest.mark.parametrize(
    ("payload", "action"),
    [
        ({"source": "telegram", "user_text": "Create a ticket: fix the checkout button"}, WorkIntakeAction.CREATE_TICKET),
        ({"source": "telegram", "transcript": "Push this into the workflow and execute it"}, WorkIntakeAction.RUN_WORKFLOW),
        ({"source": "whatsapp", "user_text": "Update ticket #17 with the new screenshot"}, WorkIntakeAction.UPDATE_TICKET),
        ({"source": "gmail", "user_text": "Continue workflow run #83 with the revised brief"}, WorkIntakeAction.STEER_RUN),
        ({"source": "web", "user_text": "What is the current status?"}, WorkIntakeAction.ANSWER_DIRECTLY),
    ],
)
def test_channel_neutral_classification(service, payload, action):
    assert service.classify(WorkIntake.from_payload(payload)).action == action


def test_transcript_wins_over_caption_and_preserves_attachments():
    intake = WorkIntake.from_payload({
        "source": "telegram",
        "user_text": "voice note",
        "transcript": "Create a ticket for the mobile menu",
        "attachments": [{"kind": "image", "path": "/tmp/menu.png", "mime_type": "image/png"}],
    })
    assert intake.text == "Create a ticket for the mobile menu"
    assert intake.attachments == [WorkIntakeAttachment(kind="image", path="/tmp/menu.png", mime_type="image/png")]


def test_normal_chat_is_not_intercepted_or_queued(service):
    decision = service.ingest(WorkIntake(source="telegram", user_text="Tell me about the project"))
    assert decision.action == WorkIntakeAction.ANSWER_DIRECTLY
    assert decision.handled is False
    assert "intake_id" not in decision.to_dict()


def test_explicit_run_creates_ticket_then_starts_background_workflow(service):
    def create(_intake, decision):
        decision.ticket_id = 168
        decision.workflow_id = 369
        decision.board_id = 10
        decision.handled = True

    def start(_intake, decision):
        decision.workflow_run_id = 84
        decision.status = "workflow_started"
        decision.response_text = "Started"

    service._create_ticket = Mock(side_effect=create)
    service._start_workflow = Mock(side_effect=start)
    decision = service.ingest(WorkIntake(source="web", user_text="Run this through the workflow: build the pizza menu"))
    assert decision.to_dict()["action"] == "run_workflow"
    assert decision.ticket_id == 168
    assert decision.workflow_run_id == 84
    assert decision.handled is True


def test_empty_request_asks_for_information(service):
    decision = service.ingest(WorkIntake(source="api"))
    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO
    assert decision.status == "needs_info"
    assert decision.handled is True


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def intake_db():
    from distr.core.db import Base
    from distr.core.db.kanban import KanbanBoard, KanbanLane
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    unrelated_project = Project(name="Player One Sport", folder_location="/tmp/player-one")
    pizza_project = Project(name="Ember & Crust Pizza House", folder_location="/tmp/pizza-house")
    workflow = AutoWorkflow(name="Website Delivery", status="active")
    session.add_all([unrelated_project, pizza_project, workflow])
    session.flush()

    unrelated_board = KanbanBoard(
        name="Player One Delivery",
        default_project_id=unrelated_project.id,
        in_use=True,
        position=0,
    )
    pizza_board = KanbanBoard(
        name="Pizza House Delivery",
        default_project_id=pizza_project.id,
        default_workflow_id=workflow.id,
        position=1,
    )
    session.add_all([unrelated_board, pizza_board])
    session.flush()
    unrelated_project.kanban_board_id = unrelated_board.id
    pizza_project.kanban_board_id = pizza_board.id
    session.add_all([
        KanbanLane(board_id=unrelated_board.id, name="Backlog", position=0),
        KanbanLane(board_id=pizza_board.id, name="Ready", position=0),
    ])
    session.commit()
    ids = {
        "pizza_project": pizza_project.id,
        "pizza_board": pizza_board.id,
        "unrelated_board": unrelated_board.id,
        "workflow": workflow.id,
    }
    session.close()
    yield factory, ids
    engine.dispose()


def test_run_resolves_named_project_before_ambient_board_and_preserves_media(intake_db):
    from distr.core.db.kanban import KanbanTicket

    factory, ids = intake_db
    session_provider = lambda: _session_ctx(factory)
    start = Mock(return_value={"run_id": 84, "status": "started", "phase": "initializing"})
    intake = WorkIntake(
        source="telegram",
        user_text="Run this through the workflow: build the pizza menu",
        source_message_id="tg-100",
        attachments=[WorkIntakeAttachment(kind="image", path="/tmp/menu.png", mime_type="image/png")],
    )

    with patch("distr.core.work_intake.service.get_session", side_effect=session_provider), \
         patch("distr.core.workflow.ticket_dispatch.get_session", side_effect=session_provider), \
         patch("distr.core.workflow.service.start_workflow_run", start), \
         patch("distr.core.orchestrator.emit_channel_intake_event"):
        decision = OrchestratorIntakeService().ingest(intake)

    assert decision.status == "workflow_started"
    assert decision.board_id == ids["pizza_board"]
    assert decision.board_id != ids["unrelated_board"]
    assert decision.project_id == ids["pizza_project"]
    assert decision.workflow_id == ids["workflow"]
    kwargs = start.call_args.kwargs
    assert kwargs["dispatch_async"] is True
    assert kwargs["run_metadata"]["project_name"] == "Ember & Crust Pizza House"
    assert kwargs["run_metadata"]["project_folder"] == "/tmp/pizza-house"
    assert kwargs["run_metadata"]["board_name"] == "Pizza House Delivery"
    assert kwargs["run_metadata"]["attachments"][0]["path"] == "/tmp/menu.png"
    session = factory()
    ticket = session.query(KanbanTicket).filter(KanbanTicket.id == decision.ticket_id).one()
    assert "/tmp/menu.png" in ticket.description
    session.close()


def test_retried_channel_message_is_idempotent_and_keeps_original_board(intake_db):
    from distr.core.db.kanban import KanbanTicket

    factory, ids = intake_db
    session_provider = lambda: _session_ctx(factory)
    start = Mock(return_value={"run_id": 85, "status": "started"})
    intake = WorkIntake(
        source="telegram",
        user_text="Run this through the workflow: build the pizza menu",
        source_message_id="tg-retry-1",
    )

    with patch("distr.core.work_intake.service.get_session", side_effect=session_provider), \
         patch("distr.core.workflow.ticket_dispatch.get_session", side_effect=session_provider), \
         patch("distr.core.workflow.service.start_workflow_run", start), \
         patch("distr.core.orchestrator.emit_channel_intake_event"):
        first = OrchestratorIntakeService().ingest(intake)
        duplicate = OrchestratorIntakeService().ingest(intake)

    assert first.ticket_id == duplicate.ticket_id
    assert duplicate.status == "duplicate"
    assert duplicate.board_id == ids["pizza_board"]
    assert start.call_count == 1
    session = factory()
    assert session.query(KanbanTicket).filter(
        KanbanTicket.source_external_id == "tg-retry-1",
    ).count() == 1
    session.close()


def test_explicit_multi_ticket_workflow_request_creates_and_runs_each_item(intake_db):
    from distr.core.db.kanban import KanbanTicket

    factory, ids = intake_db
    session_provider = lambda: _session_ctx(factory)
    run_ids = iter((201, 202, 203))
    start = Mock(
        side_effect=lambda *_args, **_kwargs: {
            "run_id": next(run_ids),
            "status": "started",
        }
    )
    intake = WorkIntake(
        source="telegram",
        user_text=(
            "For Pizza House, create separate tickets for the menu redesign, "
            "checkout bug, and mobile performance. Run them through the development "
            "workflow. Prefer local/free models for planning and update me here."
        ),
        source_message_id="tg-pizza-batch-1",
    )

    with patch("distr.core.work_intake.service.get_session", side_effect=session_provider), \
         patch("distr.core.workflow.ticket_dispatch.get_session", side_effect=session_provider), \
         patch("distr.core.workflow.service.start_workflow_run", start), \
         patch("distr.core.orchestrator.emit_channel_intake_event"):
        first = OrchestratorIntakeService().ingest(intake)
        duplicate = OrchestratorIntakeService().ingest(intake)

    assert first.status == "workflow_started"
    assert first.diagnostics["ticket_ids"] and len(first.diagnostics["ticket_ids"]) == 3
    assert first.diagnostics["workflow_run_ids"] == [201, 202, 203]
    assert first.board_id == ids["pizza_board"]
    assert first.project_id == ids["pizza_project"]
    assert duplicate.status == "duplicate"
    assert duplicate.diagnostics["duplicate_ticket_ids"] == first.diagnostics["ticket_ids"]
    assert start.call_count == 3

    session = factory()
    tickets = session.query(KanbanTicket).filter(
        KanbanTicket.source_external_id.like("tg-pizza-batch-1::item:%")
    ).order_by(KanbanTicket.id).all()
    assert [ticket.title for ticket in tickets] == [
        "menu redesign",
        "checkout bug",
        "mobile performance",
    ]
    assert all(ticket.linked_project_id == ids["pizza_project"] for ticket in tickets)
    assert all("Original request:" in ticket.description for ticket in tickets)
    for call in start.call_args_list:
        policy = call.kwargs["run_metadata"]["requested_execution_policy"]
        assert policy["roles"]["planning"]["free_only"] is True
    session.close()


@pytest.mark.parametrize("source", ["whatsapp", "gmail"])
def test_shared_channel_request_creates_one_project_ticket_with_source_trace(intake_db, source):
    from distr.core.db.kanban import KanbanTicket

    factory, ids = intake_db
    session_provider = lambda: _session_ctx(factory)
    external_id = f"{source}-pizza-1"
    intake = WorkIntake(
        source=source,
        user_text="Create a ticket: prepare the Ember & Crust Pizza House launch checklist",
        project_hint="Ember & Crust Pizza House",
        source_message_id=external_id,
    )

    with patch("distr.core.work_intake.service.get_session", side_effect=session_provider), \
         patch("distr.core.orchestrator.emit_channel_intake_event"):
        first = OrchestratorIntakeService().ingest(intake)
        duplicate = OrchestratorIntakeService().ingest(intake)

    assert first.status == "ticket_created"
    assert first.board_id == ids["pizza_board"]
    assert first.project_id == ids["pizza_project"]
    assert duplicate.status == "duplicate"
    assert duplicate.ticket_id == first.ticket_id

    session = factory()
    tickets = session.query(KanbanTicket).filter(
        KanbanTicket.source_provider == source,
        KanbanTicket.source_external_id == external_id,
    ).all()
    assert len(tickets) == 1
    assert tickets[0].lane.board_id == ids["pizza_board"]
    assert tickets[0].linked_project_id == ids["pizza_project"]
    session.close()
