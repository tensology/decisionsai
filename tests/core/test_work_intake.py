import contextlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.work_intake import WorkIntake, WorkIntakeAction, WorkIntakeAttachment
from distr.core.work_intake.service import OrchestratorIntakeService


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Run this through the workflow: build the pizza menu", "build the pizza menu"),
        ("Please push the ticket into the loop - fix checkout", "fix checkout"),
        (
            "Run this through the workflow as a strictly read-only audit. Inspect the evidence gates.",
            "strictly read-only audit.",
        ),
    ],
)
def test_ticket_title_removes_transport_scaffolding(request_text, expected):
    from distr.core.work_intake.service import _clean_title

    assert _clean_title(request_text) == expected


@pytest.fixture
def service():
    return OrchestratorIntakeService()


@pytest.mark.parametrize(
    ("payload", "action"),
    [
        ({"source": "telegram", "user_text": "Create a ticket: fix the checkout button"}, WorkIntakeAction.CREATE_TICKET),
        ({"source": "telegram", "transcript": "Push this into the workflow and execute it"}, WorkIntakeAction.RUN_WORKFLOW),
        ({
            "source": "web",
            "user_text": (
                "Run this as a strictly read-only verification ticket through the "
                "configured workflow and report the evidence."
            ),
        }, WorkIntakeAction.RUN_WORKFLOW),
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


@pytest.mark.parametrize(("text", "expected"), [
    ("Make the green button black", WorkIntakeAction.CREATE_TICKET),
    ("Can you fix the checkout error?", WorkIntakeAction.RUN_WORKFLOW),
    ("Please audit the authentication flow", WorkIntakeAction.RUN_WORKFLOW),
    (
        "Inspect the project configuration and existing tests without editing any files.",
        WorkIntakeAction.RUN_WORKFLOW,
    ),
    (
        "Verify the current test command and report evidence without making changes.",
        WorkIntakeAction.RUN_WORKFLOW,
    ),
    (
        "Perform a read-only verification of the project configuration.",
        WorkIntakeAction.RUN_WORKFLOW,
    ),
    (
        "Assess the authentication implementation and report risks.",
        WorkIntakeAction.RUN_WORKFLOW,
    ),
    (
        "Run the existing tests/core/test_orchestrator_qualification.py test file read-only.",
        WorkIntakeAction.RUN_WORKFLOW,
    ),
    (
        "Execute pytest for the current project without changing files.",
        WorkIntakeAction.RUN_WORKFLOW,
    ),
])
def test_project_scoped_single_line_work_chooses_proportional_execution(service, text, expected):
    decision = service.classify(
        WorkIntake(source="telegram", user_text=text, project_hint="Player One Sport")
    )

    assert decision.action == expected
    assert decision.diagnostics["project_scoped"] is True
    assert bool(decision.diagnostics.get("execute_lightweight")) is (
        expected == WorkIntakeAction.CREATE_TICKET
    )


def test_named_test_command_is_lightweight_but_running_tests_uses_workflow(service):
    add_command = service.classify(WorkIntake(
        source="web",
        user_text="Add test command `node --test test/*.test.mjs` to README.",
        project_hint="Pizza House",
    ))
    run_tests = service.classify(WorkIntake(
        source="web",
        user_text="Test the menu validation and fix failures.",
        project_hint="Pizza House",
    ))

    assert add_command.action == WorkIntakeAction.CREATE_TICKET
    assert add_command.diagnostics["execute_lightweight"] is True
    assert run_tests.action == WorkIntakeAction.RUN_WORKFLOW


def test_atomic_readme_instruction_is_not_escalated_by_word_count(service):
    decision = service.classify(WorkIntake(
        source="api",
        user_text=(
            "Add a Quality checks note to the README that documents the existing "
            "npm test command."
        ),
        project_hint="Ember & Crust Pizza House",
    ))

    assert decision.action == WorkIntakeAction.CREATE_TICKET
    assert decision.diagnostics["execute_lightweight"] is True


def test_ticket_word_inside_atomic_change_does_not_turn_it_into_create_only(service):
    decision = service.classify(WorkIntake(
        source="web",
        user_text="Add README note that final ticket completion requires human QA.",
        project_hint="Pizza House",
    ))

    assert decision.action == WorkIntakeAction.CREATE_TICKET
    assert decision.diagnostics["execute_lightweight"] is True


def test_project_scoped_status_question_remains_conversational(service):
    decision = service.classify(
        WorkIntake(
            source="telegram",
            user_text="What is happening with the checkout work?",
            project_hint="Player One Sport",
        )
    )

    assert decision.action == WorkIntakeAction.ANSWER_DIRECTLY


def test_project_scope_does_not_turn_a_lone_verb_into_execution(service):
    decision = service.classify(
        WorkIntake(
            source="telegram",
            user_text="Fix",
            project_hint="Player One Sport",
        )
    )

    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO
    assert "what specifically" in decision.response_text.lower()


def test_project_work_with_unresolved_prior_reference_asks_specific_question(service):
    decision = service.classify(WorkIntake(
        source="api",
        user_text="Change the thing we discussed earlier in the Ember & Crust project.",
        project_hint="Ember & Crust Pizza House",
    ))

    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO
    assert "what specifically" in decision.response_text.lower()


def test_conversation_context_resolves_prior_reference_for_project_work(service):
    decision = service.classify(WorkIntake(
        source="web",
        user_text="Change the thing we discussed earlier.",
        project_hint="Ember & Crust Pizza House",
        conversation_context="The green checkout button should become black.",
    ))

    assert decision.action == WorkIntakeAction.CREATE_TICKET


@pytest.mark.parametrize(
    "request_text",
    [
        "Make it black in Pizza House.",
        "Change that to the new one in Pizza House.",
    ],
)
def test_bare_pronoun_project_change_requires_conversation_context(service, request_text):
    decision = service.classify(WorkIntake(
        source="web",
        user_text=request_text,
        project_hint="Pizza House",
    ))

    assert decision.action == WorkIntakeAction.ASK_MISSING_INFO
    assert decision.response_text == (
        "What specifically should I change in this project, and what result should I verify?"
    )


def test_bare_pronoun_project_change_uses_supplied_conversation_context(service):
    decision = service.classify(WorkIntake(
        source="web",
        user_text="Make it black in Pizza House.",
        project_hint="Pizza House",
        conversation_context="The checkout button is currently green.",
    ))

    assert decision.action == WorkIntakeAction.CREATE_TICKET


def test_telegram_final_answer_closes_exact_pending_intake(monkeypatch):
    from distr.app.events import EventHandlerMixin

    intake_service = Mock()
    intake_service.record_direct_response.return_value = True
    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service",
        lambda: intake_service,
    )
    app = SimpleNamespace(
        telegram_manager=SimpleNamespace(
            _pending_work_intake_uid="intake-123",
            _pending_work_intake_thread_id="456",
        )
    )

    recorded = EventHandlerMixin._record_telegram_direct_intake_response(
        app,
        "Here is the synthesized result.",
    )

    assert recorded is True
    intake_service.record_direct_response.assert_called_once_with(
        source="telegram",
        source_thread_id="456",
        response_text="Here is the synthesized result.",
        intake_uid="intake-123",
    )
    assert app.telegram_manager._pending_work_intake_uid == ""
    assert app.telegram_manager._pending_work_intake_thread_id == ""


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


def test_atomic_project_change_creates_ticket_then_starts_lightweight_execution(service):
    def create(_intake, decision):
        decision.ticket_id = 168
        decision.project_id = 4
        decision.board_id = 10
        decision.handled = True
        decision.status = "ticket_created"

    service._create_ticket = Mock(side_effect=create)
    service._start_lightweight_execution = Mock()
    decision = service.ingest(WorkIntake(
        source="web",
        user_text="Make the green button black",
        project_hint="Player One Sport",
    ))

    assert decision.action == WorkIntakeAction.CREATE_TICKET
    service._start_lightweight_execution.assert_called_once()


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
        metadata={
            "qualification_scenario_id": "ui_change",
            "qualification_auto_record": False,
            "qualification_injected_failure": "provider_timeout",
            "untrusted_extra": "must not enter run context",
        },
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
    assert kwargs["run_metadata"]["qualification_scenario_id"] == "ui_change"
    assert kwargs["run_metadata"]["qualification_auto_record"] is False
    assert kwargs["run_metadata"]["qualification_injected_failure"] == "provider_timeout"
    assert kwargs["run_metadata"]["intake_action"] == "run_workflow"
    assert kwargs["run_metadata"]["intake_reason"]
    assert kwargs["run_metadata"]["intake_confidence"] == 1.0
    assert "untrusted_extra" not in kwargs["run_metadata"]
    session = factory()
    ticket = session.query(KanbanTicket).filter(KanbanTicket.id == decision.ticket_id).one()
    assert "/tmp/menu.png" in ticket.description
    session.close()


def test_active_project_single_line_request_creates_ticket_and_starts_lightweight_worker(intake_db):
    factory, ids = intake_db
    session_provider = lambda: _session_ctx(factory)
    service = OrchestratorIntakeService()
    start_lightweight = Mock()
    service._start_lightweight_execution = start_lightweight
    intake = WorkIntake(
        source="telegram",
        user_text="Make the green button black",
        source_message_id="tg-single-fix-1",
        metadata={"active_project_id": ids["pizza_project"]},
    )

    with patch("distr.core.work_intake.service.get_session", side_effect=session_provider), \
         patch("distr.core.orchestrator.emit_channel_intake_event"):
        decision = service.ingest(intake)

    assert decision.status == "ticket_created"
    assert decision.project_id == ids["pizza_project"]
    assert decision.board_id == ids["pizza_board"]
    assert decision.workflow_run_id is None
    start_lightweight.assert_called_once()


def test_final_chat_response_is_correlated_to_pending_direct_intake(intake_db):
    from distr.core.db.orchestrator import OrchestratorEvent

    factory, _ids = intake_db
    session = factory()
    decision_event = OrchestratorEvent(
        event_uid="decision-direct-1",
        source="web",
        event_type="work_intake_decision",
        status="triaged",
        summary="answer_directly: research this",
        payload=json.dumps({
            "intake": {
                "source": "web",
                "source_thread_id": "42",
                "source_message_id": "web-message-1",
                "intake_uid": "intake-direct-1",
            },
            "decision": {
                "action": "answer_directly",
                "status": "triaged",
                "response_text": "",
            },
        }),
    )
    session.add(decision_event)
    session.commit()
    parent_id = decision_event.id
    session.close()

    emit = Mock(return_value=991)
    with patch(
        "distr.core.work_intake.service.get_session",
        side_effect=lambda: _session_ctx(factory),
    ), patch("distr.core.orchestrator.ensure_orchestrator_tables"), patch(
        "distr.core.orchestrator.emit_event", emit
    ):
        recorded = OrchestratorIntakeService().record_direct_response(
            source="web",
            source_thread_id="42",
            response_text="The research is complete, and here is the synthesized answer.",
        )

    assert recorded is True
    assert emit.call_args.kwargs["event_type"] == "work_intake_response"
    assert emit.call_args.kwargs["parent_event_id"] == parent_id
    assert emit.call_args.kwargs["payload"]["intake_uid"] == "intake-direct-1"
    assert emit.call_args.kwargs["payload"]["phase"] == "final"


def test_overlapping_chat_response_uses_exact_intake_uid_not_latest_event(intake_db):
    from distr.core.db.orchestrator import OrchestratorEvent

    factory, _ids = intake_db
    session = factory()
    parent_ids = {}
    for index, uid in enumerate(("older-intake", "newer-intake"), start=1):
        event = OrchestratorEvent(
            event_uid=f"overlap-decision-{index}",
            source="web",
            event_type="work_intake_decision",
            status="triaged",
            summary="answer_directly: overlapping research",
            payload=json.dumps({
                "intake": {
                    "source": "web",
                    "source_thread_id": "77",
                    "source_message_id": f"web-overlap-{index}",
                    "intake_uid": uid,
                },
                "decision": {
                    "action": "answer_directly",
                    "status": "triaged",
                    "response_text": "",
                },
            }),
        )
        session.add(event)
        session.flush()
        parent_ids[uid] = int(event.id)
    session.commit()
    session.close()

    emit = Mock(return_value=992)
    with patch(
        "distr.core.work_intake.service.get_session",
        side_effect=lambda: _session_ctx(factory),
    ), patch("distr.core.orchestrator.ensure_orchestrator_tables"), patch(
        "distr.core.orchestrator.emit_event", emit
    ):
        recorded = OrchestratorIntakeService().record_direct_response(
            source="web",
            source_thread_id="77",
            response_text="This answer belongs to the older request.",
            intake_uid="older-intake",
        )

    assert recorded is True
    assert emit.call_args.kwargs["parent_event_id"] == parent_ids["older-intake"]
    assert emit.call_args.kwargs["payload"]["intake_uid"] == "older-intake"
    assert emit.call_args.kwargs["payload"]["source_message_id"] == "web-overlap-1"


def test_dangling_project_board_is_repaired_instead_of_using_unrelated_active_board(intake_db):
    from distr.core.db.kanban import KanbanBoard, KanbanLane
    from distr.core.db.projects import Project

    factory, ids = intake_db
    session = factory()
    project = session.get(Project, ids["pizza_project"])
    old_board = session.get(KanbanBoard, ids["pizza_board"])
    project.kanban_board_id = 99999
    old_board.default_project_id = None
    session.commit()
    session.close()

    session_provider = lambda: _session_ctx(factory)
    service = OrchestratorIntakeService()
    service._start_lightweight_execution = Mock()
    with patch("distr.core.work_intake.service.get_session", side_effect=session_provider), \
         patch("distr.core.orchestrator.emit_channel_intake_event"):
        decision = service.ingest(WorkIntake(
            source="telegram",
            user_text="Make the green button black",
            project_hint="Ember & Crust Pizza House",
        ))

    session = factory()
    repaired = session.get(KanbanBoard, decision.board_id)
    lane_names = {
        lane.name for lane in session.query(KanbanLane).filter_by(board_id=repaired.id).all()
    }
    project = session.get(Project, ids["pizza_project"])
    assert repaired.name == "Ember & Crust Pizza House Delivery"
    assert repaired.default_project_id == ids["pizza_project"]
    assert repaired.id != ids["unrelated_board"]
    assert project.kanban_board_id == repaired.id
    assert {"Backlog", "In Progress", "QA", "Complete"} <= lane_names
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
    start_group = Mock(return_value={
        "success": True,
        "group_id": "pizza-delivery",
        "mode": "sequential",
        "ticket_count": 3,
        "started": [{"ticket_id": 1, "run_id": 201}],
        "errors": [],
        "queued_count": 2,
    })
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
         patch("distr.core.workflow.dispatcher.start_workflow_ticket_group", start_group), \
         patch("distr.core.orchestrator.emit_channel_intake_event"):
        first = OrchestratorIntakeService().ingest(intake)
        duplicate = OrchestratorIntakeService().ingest(intake)

    assert first.status == "workflow_started"
    assert first.diagnostics["ticket_ids"] and len(first.diagnostics["ticket_ids"]) == 3
    assert first.diagnostics["workflow_run_ids"] == [201]
    assert first.diagnostics["ticket_group_id"] == "pizza-delivery"
    assert first.diagnostics["ticket_group_mode"] == "sequential"
    assert first.diagnostics["ticket_group_queued_count"] == 2
    assert first.board_id == ids["pizza_board"]
    assert first.project_id == ids["pizza_project"]
    assert duplicate.status == "duplicate"
    assert duplicate.diagnostics["duplicate_ticket_ids"] == first.diagnostics["ticket_ids"]
    assert start_group.call_count == 1

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
    assert [ticket.description for ticket in tickets] == [
        "menu redesign",
        "checkout bug",
        "mobile performance",
    ]
    assert all(ticket.source_provider == "telegram" for ticket in tickets)
    assert [ticket.source_external_id for ticket in tickets] == [
        "tg-pizza-batch-1::item:1",
        "tg-pizza-batch-1::item:2",
        "tg-pizza-batch-1::item:3",
    ]
    group_call = start_group.call_args
    assert [item["ticket_id"] for item in group_call.args[1]] == first.diagnostics["ticket_ids"]
    policy = group_call.kwargs["run_metadata"]["requested_execution_policy"]
    assert policy["roles"]["planning"]["free_only"] is True
    session.close()


def test_explicit_batch_keeps_compound_actions_inside_semicolon_items():
    from distr.core.work_intake.service import _explicit_batch_ticket_items

    request = (
        "Create separate tickets for run tests/core/test_work_intake.py and report "
        "the result without editing files; run tests/core/test_workflow_ticket_group.py "
        "and report the result without editing files. Run them through the Development "
        "workflow in order. This is read-only."
    )

    assert _explicit_batch_ticket_items(request) == [
        "run tests/core/test_work_intake.py and report the result without editing files",
        "run tests/core/test_workflow_ticket_group.py and report the result without editing files",
    ]


def test_explicit_numbered_ticket_batch_preserves_full_ticket_contracts():
    from distr.core.work_intake.service import _explicit_batch_ticket_items

    request = (
        "Create two separate tickets and run them through the existing Development workflow in order. "
        "Ticket one: inspect the current README Quality checks section and report whether the "
        "documented pytest command matches repository configuration; do not edit files. "
        "Ticket two: run the existing focused work-intake and qualification tests read-only and "
        "report the exact result; do not edit files. Keep them in one ordered ticket group, do not "
        "start ticket two before ticket one reaches its terminal workflow state, and return a final "
        "report for both."
    )

    assert _explicit_batch_ticket_items(request) == [
        (
            "inspect the current README Quality checks section and report whether the documented "
            "pytest command matches repository configuration; do not edit files"
        ),
        (
            "run the existing focused work-intake and qualification tests read-only and report "
            "the exact result; do not edit files"
        ),
    ]


def test_numbered_batch_does_not_treat_ticket_sentences_as_group_controls():
    from distr.core.work_intake.service import _explicit_batch_ticket_items

    request = (
        "Create two separate tickets. "
        "Ticket one: inspect repository configuration. Run the focused test and report exact evidence. "
        "Ticket two: inspect the result packet. Report any missing validation evidence. "
        "Keep them in one ordered group and return a final report for both."
    )

    assert _explicit_batch_ticket_items(request) == [
        "inspect repository configuration. Run the focused test and report exact evidence",
        "inspect the result packet. Report any missing validation evidence",
    ]


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
