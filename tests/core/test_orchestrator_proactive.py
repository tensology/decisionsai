from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import distr.core.db.orchestrator  # noqa: F401
import distr.core.db.kanban  # noqa: F401
import distr.core.db.projects  # noqa: F401
from distr.core.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _factory(tmp_path):
    db_path = tmp_path / "orchestrator_proactive.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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


def _seed_work_signal(factory, tmp_path):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    with _session_ctx(factory) as session:
        project = Project(
            name="Merrypak",
            folder_location=str(tmp_path / "www.merrypak.co.za"),
            coding_backend="codex",
            in_use=True,
        )
        session.add(project)
        session.flush()

        board = KanbanBoard(
            name="Merrypak Board",
            default_project_id=project.id,
            in_use=True,
            orchestrator_policy=json.dumps(
                {
                    "harness_preferences": {
                        "frontend": {"backend": "cursor", "skills": ["react-frontend-expert"]}
                    }
                }
            ),
        )
        session.add(board)
        session.flush()

        lane = KanbanLane(board_id=board.id, name="Inbox", position=0)
        session.add(lane)
        session.flush()

        ticket = KanbanTicket(
            lane_id=lane.id,
            title="Client says checkout page is broken",
            description="Urgent: Merrypak client says the React checkout page fails on submit. Please fix today.",
            priority="critical",
            complexity="high",
            linked_project_id=project.id,
            source_provider="gmail",
            source_contact="client@example.com",
            source_thread_id="gmail-thread-1",
            source_label="Client email",
            position=0,
        )
        session.add(ticket)
        session.flush()
        return SimpleNamespace(project_id=project.id, board_id=board.id, ticket_id=ticket.id)


def test_proactive_scan_prioritizes_work_matches_project_and_emits_activity(tmp_path, monkeypatch):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.orchestrator_proactive import run_proactive_check

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator_proactive.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(
        "distr.core.external_agent_context.build_external_agent_context",
        lambda limit=8: {
            "codex_threads": [
                {
                    "cwd": str(tmp_path / "www.merrypak.co.za"),
                    "title": "Fix Merrypak checkout validation",
                }
            ],
            "cursor_workspaces": [{"folder": str(tmp_path / "www.merrypak.co.za")}],
        },
    )

    result = run_proactive_check(limit=5)

    assert result["success"] is True
    assert result["summary"]["total_candidates"] == 1
    candidate = result["candidates"][0]
    assert candidate["priority"] == "critical"
    assert candidate["priority_score"] >= 90
    assert candidate["project_id"] == ids.project_id
    assert candidate["project_name"] == "Merrypak"
    assert candidate["board_id"] == ids.board_id
    assert candidate["source"] == "gmail"
    assert candidate["recommended_action"] == "ask_approval_to_dispatch"
    assert "Merrypak" in candidate["approval_question"]
    assert "Codex" in candidate["developer_context"]["recent_surfaces"]
    assert "Cursor" in candidate["developer_context"]["recent_surfaces"]
    assert "Hermes" not in result["spoken_summary"]

    with _session_ctx(factory) as session:
        events = session.query(OrchestratorEvent).filter(OrchestratorEvent.event_type == "proactive_work_candidate").all()
        assert len(events) == 1
        payload = json.loads(events[0].payload)
        assert payload["candidate"]["ticket_id"] == ids.ticket_id


def test_scan_drafts_only_work_with_distinct_pending_stages(tmp_path, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import orchestrator_proactive as proactive
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool
    from distr.core.db.kanban import KanbanTicket, KanbanTicketTodo, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    from distr.gui.web.routes.development.workflows import register_routes

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(proactive, "get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(proactive, "_external_context", lambda: {})
    monkeypatch.setattr(proactive, "_resolve_route", lambda signal: {"backend": "pi", "model": "fixture-model"})
    steps = ["Reproduce the checkout error", "Repair the checkout request", "Verify the payment handoff"]
    with _session_ctx(factory) as session:
        large = session.get(KanbanTicket, ids.ticket_id)
        direct = KanbanTicket(lane_id=large.lane_id, title="Repair the checkout label", complexity="high",
                              linked_project_id=ids.project_id)
        session.add(direct)
        session.add_all(KanbanTicketTodo(ticket_id=large.id, text=label, position=position)
                        for position, label in enumerate(steps))
        session.add(KanbanTicketTodo(ticket_id=large.id, text="Confirm the request", position=0, done=True))
        session.flush()
        direct_id = direct.id

    def scan():
        result = json.loads(ProactiveOrchestratorTool()._run(action="scan", board_id=ids.board_id, format="json"))
        return result, {candidate["ticket_id"]: candidate for candidate in result["candidates"]}

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    review_url = f"/workflows/studio/boards/{ids.board_id}/tickets/{ids.ticket_id}/workflow-draft"
    assert client.get(review_url).json() == {"status": "missing"}

    result, candidates = scan()
    proposed = candidates[ids.ticket_id]
    assert proposed["recommended_action"] == "review_workflow_draft"
    assert "review" in result["spoken_summary"].lower()
    draft = proposed["workflow_draft"]
    assert draft["status"] == "awaiting_review"
    assert draft["project_id"] == ids.project_id
    assert draft["board_id"] == ids.board_id
    assert draft["ticket_id"] == ids.ticket_id
    assert [step["instruction"] for step in draft["steps"]] == steps
    assert client.get(review_url).json() == {
        "status": "awaiting_review", "candidate_id": proposed["candidate_id"],
        "project_name": "Merrypak", "draft": draft,
    }
    wrong_board = client.get(f"/workflows/studio/boards/999/tickets/{ids.ticket_id}/workflow-draft")
    assert wrong_board.status_code == 404
    assert all(step["acceptance"] for step in draft["steps"])
    assert candidates[direct_id]["recommended_action"] == "ask_approval_to_dispatch"
    assert "workflow_draft" not in candidates[direct_id]
    dispatch = proactive.dispatch_proactive_candidate(proposed["candidate_id"])
    assert not dispatch["success"]
    assert "workflow draft for review" in dispatch["error"]
    assert scan()[1][ids.ticket_id]["candidate_id"] == proposed["candidate_id"]
    with _session_ctx(factory) as session:
        event = session.get(OrchestratorEvent, proposed["candidate_id"])
        assert json.loads(event.payload)["candidate"]["workflow_draft"] == draft
        assert session.query(AutoWorkflow).count() == session.query(AutoWorkflowRun).count() == 0
        assert session.query(ProjectExecutionSession).count() == 0
        assert session.get(KanbanTicket, ids.ticket_id).linked_workflow_id is None
        item = session.query(KanbanTicketTodo).filter_by(ticket_id=ids.ticket_id, text=steps[0]).one()
        item.text = "Reproduce the checkout error on a phone"
    assert client.get(review_url).json() == {"status": "stale"}
    revised = scan()[1][ids.ticket_id]
    assert revised["candidate_id"] != proposed["candidate_id"]
    assert revised["workflow_draft"]["steps"][0]["instruction"].endswith("on a phone")
    assert client.get(review_url).json()["candidate_id"] == revised["candidate_id"]
    with _session_ctx(factory) as session:
        item = session.query(KanbanTicketTodo).filter_by(ticket_id=ids.ticket_id, text="Reproduce the checkout error on a phone").one()
        item.text = steps[0]
    assert client.get(review_url).json() == {"status": "stale"}
    reverted = scan()[1][ids.ticket_id]
    assert reverted["candidate_id"] > revised["candidate_id"]
    assert client.get(review_url).json()["candidate_id"] == reverted["candidate_id"]


def test_description_draft_uses_only_implementation_steps_and_defers_to_saved_checklist(tmp_path, monkeypatch):
    from distr.core import orchestrator_proactive as proactive
    from distr.core.db.kanban import KanbanTicket, KanbanTicketTodo

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(proactive, "get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(proactive, "_external_context", lambda: {})
    monkeypatch.setattr(proactive, "_resolve_route", lambda signal: {"backend": "pi", "model": "fixture-model"})
    with _session_ctx(factory) as db:
        db.get(KanbanTicket, ids.ticket_id).description = """## Background
- A recent update changed checkout.

```md
## Workflow steps
1. Example inside a code block
```

## Implementation plan
1. Reproduce the checkout error
   - Capture the browser console
2. Repair the checkout request
3. [x] Confirm the request
4. Verify the payment handoff
5. reproduce the checkout error

## Notes
- Confirm which release caused this
"""

    def scan():
        return proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]

    description_candidate = scan()
    draft = description_candidate["workflow_draft"]
    assert [step["instruction"] for step in draft["steps"]] == [
        "Reproduce the checkout error", "Repair the checkout request", "Verify the payment handoff",
    ]
    assert "ticket description" in draft["reason"]
    assert proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id)["draft"] == draft

    with _session_ctx(factory) as db:
        db.add(KanbanTicketTodo(ticket_id=ids.ticket_id, text="Confirm the request", done=True))
    assert proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id) == {"status": "stale"}
    direct_candidate = scan()
    assert direct_candidate["recommended_action"] == "ask_approval_to_dispatch"

    with _session_ctx(factory) as db:
        db.add_all(KanbanTicketTodo(ticket_id=ids.ticket_id, position=i, text=label)
                   for i, label in enumerate(("Reproduce on mobile", "Repair the layout", "Verify the payment form")))
    dispatch = proactive.dispatch_proactive_candidate(direct_candidate["candidate_id"])
    assert not dispatch["success"]
    assert "now has a workflow draft for review" in dispatch["error"]
    revised = scan()
    assert revised["candidate_id"] != description_candidate["candidate_id"]
    assert "pending checklist items" in revised["workflow_draft"]["reason"]


@pytest.mark.parametrize("description", [
    "First, inspect the receipt. Then, confirm the destination.",
    "> First, inspect the receipt. Then, confirm the destination. Finally, send it.",
    "\x60\x60\x60md\nFirst, inspect the receipt. Then, confirm the destination. Finally, send it.\n\x60\x60\x60",
    "First, inspect the receipt. Then, inspect the receipt. Finally, send it.",
    "First, inspect the receipt. Then, confirm the destination. Finally, send it.\n\n"
    "First, find the existing receipt. Then, verify delivery. Finally, close the ticket.",
])
def test_ordered_prose_ignores_incomplete_or_ambiguous_sequences(description):
    from distr.core.orchestrator_proactive import _ordered_prose_workflow_steps

    assert _ordered_prose_workflow_steps(description) == []


def test_requested_outcomes_exclude_examples_constraints_and_completed_work():
    from distr.core.orchestrator_proactive import _requested_workflow_outcomes

    description = """The refund form must retain the selected order.
> The example must never be copied into the workflow.
If the gateway is down, the interface must show the existing error.
The workflow must not issue any refunds.

## Requirements
- Display the original payment reference.
- [x] Confirm the support contact.
- [ ] Show the status for each payment attempt.
- Do not contact customers.
- "Example checkout wording"
- display the original payment reference.

## Examples
The example must include a placeholder account.
"""
    assert _requested_workflow_outcomes(description) == [
        "The refund form must retain the selected order",
        "If the gateway is down, the interface must show the existing error",
        "Display the original payment reference",
        "Show the status for each payment attempt",
    ]


@pytest.mark.parametrize("description", [
    "The checkout must be improved.",
    "The checkout must work. The checkout must work. The checkout must work.",
    "The checkout must not charge twice. The audit must not email customers. The worker must not deploy.",
    "## Examples\n- Update checkout\n- Edit settings\n- Add a shortcut",
    "```text\nThe checkout must work. The receipt must show tax. The layout must fit phones.\n```",
])
def test_briefs_without_three_distinct_outcomes_keep_the_direct_review_path(description):
    from distr.core.orchestrator_proactive import _workflow_draft

    signal = {"title": "Review checkout", "description": description, "project_id": 7, "board_id": 2, "ticket_id": 3}
    assert _workflow_draft(signal, None) is None


@pytest.mark.parametrize(("assignment", "drift"), [
    (None, None), ("ticket", None), ("board", None), ("ticket", "step"), ("board", "action"),
    ("ticket", "terminal"), ("ticket", "scope_init"), ("board", "scope_step"),
    ("ticket", "scope_resume"), ("ticket", "scope_terminal"), ("ticket", "scope_result"),
    ("ticket", "scope_preflight"), ("ticket", "definition_preflight"),
    ("ticket", "scope_engagement"), ("ticket", "definition_engagement"),
    (None, "folder_review"), ("ticket", "folder_review"), ("ticket", "folder_replaced_review"),
])
def test_approved_draft_launch_is_atomic_and_duplicate_clicks_return_one_run(tmp_path, monkeypatch, assignment, drift):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import orchestrator_proactive as proactive
    from distr.core.db import Action, Chat
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketTodo
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, DevelopmentWorkItem
    from distr.core.workflow import dispatcher, draft_launch
    from distr.gui.web.routes.development.workflows import register_routes

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    provider = lambda: _session_ctx(factory)
    if drift in {"folder_review", "folder_replaced_review"}:
        with provider() as db:
            Path(db.get(Project, ids.project_id).folder_location).mkdir(exist_ok=True)
    monkeypatch.setattr("distr.core.orchestrator.get_session", provider)
    monkeypatch.setattr(proactive, "get_session", provider)
    monkeypatch.setattr(draft_launch, "get_session", provider)
    monkeypatch.setattr(dispatcher, "get_session", provider)
    monkeypatch.setattr(proactive, "_external_context", lambda: {})
    monkeypatch.setattr(proactive, "_resolve_route", lambda signal: {"backend": "pi", "model": "fixture-model"})
    monkeypatch.setattr(dispatcher, "record_workflow_chat_event", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_finalize_terminal_run", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.orchestration_events.emit_orchestration_event", lambda **kw: None)
    workers = []

    class HeldTimer:
        def __init__(self, delay, callback):
            self.callback = callback

        def start(self):
            workers.append(self.callback)

    monkeypatch.setattr(dispatcher, "threading", SimpleNamespace(
        Timer=HeldTimer, get_ident=dispatcher.threading.get_ident, Lock=dispatcher.threading.Lock,
    ))
    workflow_id = None
    original = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
    with provider() as db:
        if assignment:
            saved = AutoWorkflow(name="Checkout review", status="active")
            db.add(saved)
            db.flush()
            workflow_id = saved.id
            if assignment == "ticket":
                db.get(KanbanTicket, ids.ticket_id).linked_workflow_id = workflow_id
            else:
                db.get(KanbanBoard, ids.board_id).default_workflow_id = workflow_id
            step = AutoWorkflowStep(name="Inspect checkout", instruction="Inspect the payment handoff",
                                    action_type="send_to_project_cli", linked_project_id=ids.project_id)
            if drift == "action":
                action = Action(title="Inspect the payment handoff", is_instruction=True, instruction_text=step.instruction)
                db.add(action)
                db.flush()
                step.action_type = "decision_action"
                step.config = json.dumps({"action_id": action.id})
            saved.steps.append(step)
        else:
            for position, text in enumerate(["Reproduce checkout", "Repair checkout", "Verify checkout"]):
                db.add(KanbanTicketTodo(ticket_id=ids.ticket_id, text=text, position=position))
    if assignment:
        assert not proactive.dispatch_proactive_candidate(original["candidate_id"])["success"]
    candidate = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
    assert candidate["recommended_action"] == "review_workflow_draft"
    assert candidate["workflow_draft"].get("workflow_id") == workflow_id
    approval = {"approved": True, "candidate_id": candidate["candidate_id"],
                "revision": candidate["workflow_draft"]["checklist_revision"]}
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    path = f"/workflows/studio/boards/{ids.board_id}/tickets/{ids.ticket_id}/workflow-draft"
    assert client.post(path + "/approve", json={**approval, "approved": False}).status_code == 422
    assert client.post(path + "/approve", json={**approval, "revision": "0" * 64}).status_code == 409
    rejected = client.post(path + "/approve", json=approval)
    assert rejected.status_code == 409 and "Inbox" in rejected.json()["detail"]
    with provider() as db:
        assert db.query(AutoWorkflow).count() == bool(assignment)
        assert db.query(AutoWorkflowRun).count() == db.query(Chat).count() == 0
        ticket = db.get(KanbanTicket, ids.ticket_id)
        db.get(KanbanLane, ticket.lane_id).name = "Backlog"
        if assignment:
            step = db.get(AutoWorkflow, workflow_id).steps[0]
            if drift == "action":
                db.get(Action, json.loads(step.config)["action_id"]).instruction_text = "Review the revised Action"
            else:
                step.instruction = "Inspect the revised payment handoff"
    if assignment:
        assert client.get(path).json() == {"status": "stale"}
        assert client.post(path + "/approve", json=approval).status_code == 409
        revised = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
        approval = {"approved": True, "candidate_id": revised["candidate_id"],
                    "revision": revised["workflow_draft"]["checklist_revision"]}
    if drift in {"folder_review", "folder_replaced_review"}:
        original_approval = approval
        moved_folder = tmp_path / "new-checkout"
        moved_folder.mkdir()
        with provider() as db:
            project = db.get(Project, ids.project_id)
            checkout = Path(project.folder_location)
            (checkout / "readme.txt").write_text("Ordinary file edit.", encoding="utf-8")
        assert client.get(path).json()["draft"]["checklist_revision"] == original_approval["revision"]
        with provider() as db:
            if drift == "folder_replaced_review":
                checkout.rename(tmp_path / "original-checkout")
                moved_folder.rename(checkout)
            else:
                db.get(Project, ids.project_id).folder_location = str(moved_folder)
        assert client.get(path).json() == {"status": "stale"}
        assert client.post(path + "/approve", json=original_approval).status_code == 409
        assert workers == []
        revised = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
        approval = {"approved": True, "candidate_id": revised["candidate_id"],
                    "revision": revised["workflow_draft"]["checklist_revision"]}
        assert approval["revision"] != original_approval["revision"]
        assert approval["candidate_id"] != original_approval["candidate_id"]
        receipt = client.post(path + "/approve", json=approval)
        assert receipt.status_code == 200, receipt.text
        assert len(workers) == 1
        return

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post(path + "/approve", json=approval), range(2)))
        assert [response.status_code for response in responses] == [200, 200], [response.text for response in responses]
        receipt = responses[0].json()
        assert responses[1].json() == receipt == client.get(path).json()
        assert receipt["status"] == "launched"
        assert len(workers) == 1
        with provider() as db:
            run = db.query(AutoWorkflowRun).one()
            workflow = db.query(AutoWorkflow).one()
            ticket = db.get(KanbanTicket, ids.ticket_id)
            item = db.query(DevelopmentWorkItem).one()
            turn = db.query(Chat).filter_by(parent_id=receipt["chat_id"]).one()
            assert (run.id, run.workflow_id, run.board_id, run.ticket_id) == (
                receipt["run_id"], workflow.id, ids.board_id, ticket.id,
            )
            assert run.chat_id == ticket.source_chat_id == item.chat_id == receipt["chat_id"]
            assert item.project_id == ids.project_id and item.workflow_id == workflow.id
            run_data = json.loads(run.run_data)
            assert run_data["development_turn_id"] == turn.id
            assert run_data["execution_route"]["model"] == "fixture-model"
            if assignment:
                assert workflow.id == workflow_id and workflow.chat_id is None
                assert run_data["skip_ticket_queue_advance"] is True
                assert workflow.steps[0].linked_project_id == ids.project_id
                if drift == "action":
                    action = db.get(Action, json.loads(workflow.steps[0].config)["action_id"])
                    assert action.instruction_text == "Review the revised Action"
                    action.instruction_text = "Unapproved replacement Action"
                elif drift in {"step", "terminal"}:
                    workflow.steps[0].instruction = "Unapproved replacement step"
                else:
                    assert workflow.steps[0].instruction == "Inspect the revised payment handoff"
            else:
                assert workflow.chat_id == run.chat_id
                assert [(step.instruction, step.linked_project_id) for step in workflow.steps] == [
                    ("Reproduce checkout", ids.project_id), ("Repair checkout", ids.project_id), ("Verify checkout", ids.project_id)
                ]
            if drift is None:
                run.status = ticket.workflow_status = "failed"
        if drift in {"definition_preflight", "definition_engagement"}:
            run_id = receipt["run_id"]
            with provider() as db:
                step_id = db.get(AutoWorkflowRun, run_id).current_step_id

            def edit_during_preflight(*_args):
                with provider() as db:
                    db.get(AutoWorkflowStep, step_id).instruction = "Unreviewed replacement"
                return {"ok": True, "summary": "Preflight passed"}

            if drift == "definition_preflight":
                monkeypatch.setattr(dispatcher, "build_step_preflight", edit_during_preflight)
            else:
                monkeypatch.setattr(dispatcher, "build_step_preflight", lambda *_: {"ok": True})
                monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_started",
                                    edit_during_preflight)
            monkeypatch.setattr(dispatcher, "emit_step_activity", Mock())
            executed = Mock(side_effect=AssertionError("Worker dispatched after the definition changed"))
            monkeypatch.setattr(dispatcher.StepDispatcher, "_execute", executed)
            result = dispatcher.StepDispatcher().run_in_workflow(step_id, run_id)
            assert result["error"] == draft_launch.WORKFLOW_DEFINITION_CHANGED
            executed.assert_not_called()
            stopped = client.get(path).json()
            assert stopped["run_status"] == "failed" and stopped["warning"] == result["error"]
            return
        if drift and drift.startswith("scope_"):
            from distr.core.db.kanban import ProjectExecutionSession

            foreign = _seed_work_signal(factory, tmp_path / "moved-project")
            run_id = receipt["run_id"]
            with provider() as db:
                ticket = db.get(KanbanTicket, ids.ticket_id)
                original_lane = ticket.lane_id
                original_status = ticket.workflow_status
                run = db.get(AutoWorkflowRun, run_id)
                step_id = run.current_step_id
                if drift in {"scope_init", "scope_resume"}:
                    db.get(Chat, receipt["chat_id"]).project_id = foreign.project_id
                    if drift == "scope_resume":
                        run.status = "waiting"
                        run.run_data = json.dumps({**json.loads(run.run_data), "waiting_kind": "worker_needs_input"})
                        db.add_all([
                            ProjectExecutionSession(project_id=ids.project_id, run_id=run_id, route_backend="pi", status="waiting"),
                            ProjectExecutionSession(project_id=foreign.project_id, route_backend="pi", status="running"),
                        ])
                elif drift in {"scope_step", "scope_result"}:
                    ticket.linked_project_id = foreign.project_id
                elif drift not in {"scope_preflight", "scope_engagement"}:
                    ticket.lane_id = db.get(KanbanTicket, foreign.ticket_id).lane_id
            record = Mock()
            monkeypatch.setattr(dispatcher, "record_workflow_chat_event", record)
            if drift == "scope_init":
                dispatched = Mock(return_value={"success": True})
                monkeypatch.setattr(dispatcher.StepDispatcher, "run_in_workflow", dispatched)
                result = workers[0]()
                dispatched.assert_not_called()
            elif drift == "scope_step":
                dispatched = Mock(return_value={"error": "Step must not be loaded"})
                monkeypatch.setattr(dispatcher.StepDispatcher, "_load_step", dispatched)
                result = dispatcher.StepDispatcher().run_in_workflow(step_id, run_id)
                dispatched.assert_not_called()
            elif drift in {"scope_preflight", "scope_engagement"}:
                def move_during_preflight(*_args):
                    with provider() as db:
                        db.get(KanbanTicket, ids.ticket_id).linked_project_id = foreign.project_id
                    return {"ok": True, "summary": "Preflight passed"}

                during_preflight = drift == "scope_preflight"
                monkeypatch.setattr(dispatcher, "build_step_preflight",
                                    move_during_preflight if during_preflight else lambda *_: {"ok": True})
                monkeypatch.setattr(dispatcher, "emit_step_activity", Mock())
                notify = Mock(side_effect=None if during_preflight else move_during_preflight)
                monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_started", notify)
                executed = Mock(side_effect=AssertionError("Worker dispatched after ownership changed"))
                monkeypatch.setattr(dispatcher.StepDispatcher, "_execute", executed)
                result = dispatcher.StepDispatcher().run_in_workflow(step_id, run_id)
                executed.assert_not_called()
                if during_preflight:
                    notify.assert_not_called()
                else:
                    notify.assert_called_once_with(run_id, step_id)
            elif drift == "scope_resume":
                resumed = Mock()
                terminate = Mock()
                monkeypatch.setattr(dispatcher, "_resume_worker_needs_input", resumed)
                monkeypatch.setattr("distr.core.project_cli_backends.registry.terminate_backend_process", terminate)
                result = dispatcher.continue_waiting_step(run_id, "Continue")
                resumed.assert_not_called()
                terminate.assert_called_once_with(ids.project_id, "pi", board_id=ids.board_id,
                                                  execution_id=run_id, execution_kind="workflow")
                with provider() as db:
                    assert db.query(ProjectExecutionSession).filter_by(run_id=run_id).one().status == "cancelled"
                    assert db.query(ProjectExecutionSession).filter_by(project_id=foreign.project_id).one().status == "running"
            elif drift == "scope_result":
                from distr.core.workflow import router as step_router

                monkeypatch.setattr(step_router, "get_session", provider)
                verify = Mock()
                monkeypatch.setattr(step_router, "_run_verification", verify)
                assert step_router.StepRouter().route(step_id, "Completed old work", True, run_id)["action"] == "ignore"
                verify.assert_not_called()
                result = {"error": draft_launch.WORKFLOW_APPROVAL_SCOPE_CHANGED}
            else:
                assert dispatcher.complete_run(run_id, "completed")
                result = {"error": draft_launch.WORKFLOW_APPROVAL_SCOPE_CHANGED}
            assert result["error"] == draft_launch.WORKFLOW_APPROVAL_SCOPE_CHANGED
            if drift == "scope_engagement":
                assert [call.args[1] for call in record.call_args_list] == ["step_started"]
            else:
                record.assert_not_called()
            with provider() as db:
                run = db.get(AutoWorkflowRun, run_id)
                ticket = db.get(KanbanTicket, ids.ticket_id)
                assert run.status == "failed"
                stored = json.loads(run.run_data)
                assert stored["terminal_warning"] == result["error"]
                assert stored["result_packet"]["status"] == "failed"
                assert ticket.workflow_status == original_status
                db.get(Chat, receipt["chat_id"]).project_id = ids.project_id
                ticket.linked_project_id = ids.project_id
                ticket.lane_id = original_lane
            stopped = client.get(path).json()
            assert stopped["run_status"] == "failed" and stopped["warning"] == result["error"]
            assert len(workers) == 1
            return
        if drift:
            dispatch_step = Mock(side_effect=AssertionError("unapproved step was dispatched"))
            monkeypatch.setattr(dispatcher.StepDispatcher, "run_in_workflow", dispatch_step)
            if drift == "terminal":
                assert dispatcher.complete_run(receipt["run_id"], "completed")
            else:
                assert workers[0]()["error"] == draft_launch.WORKFLOW_DEFINITION_CHANGED
            dispatch_step.assert_not_called()
            stopped = client.get(path).json()
            assert stopped["run_status"] == "failed" and stopped["warning"] == draft_launch.WORKFLOW_DEFINITION_CHANGED
            candidate = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
            review = client.get(path).json()
            assert review["status"] == "awaiting_review" and review["candidate_id"] == candidate["candidate_id"]
            assert client.post(path + "/approve", json=approval).status_code == 409
            assert len(workers) == 1
            approved_again = client.post(path + "/approve", json={
                "approved": True, "candidate_id": review["candidate_id"],
                "revision": review["draft"]["checklist_revision"],
            })
            responses.append(approved_again)
            assert approved_again.status_code == 200, approved_again.text
            rerun = approved_again.json()
            assert rerun["chat_id"] == receipt["chat_id"] and rerun["run_id"] != receipt["run_id"]
            assert len(workers) == 2
            return
        proactive.run_proactive_check(board_id=ids.board_id)
        recovered = client.get(path).json()
        assert recovered["run_id"] == receipt["run_id"] and recovered["run_status"] == "failed"
        assert client.post(path + "/approve", json=approval).json() == recovered
        assert len(workers) == 1

        # A launch receipt cannot point to a thread whose live ownership moved.
        foreign = _seed_work_signal(factory, tmp_path / "other-project")
        with provider() as db:
            root = db.get(Chat, receipt["chat_id"])
            root.project_id = foreign.project_id
        assert client.get(path).json() == {"status": "unconfirmed"}
        rejected = client.post(path + "/approve", json=approval)
        assert rejected.status_code == 409 and "could not be confirmed" in rejected.json()["detail"]
        unaffected = proactive.run_proactive_check(board_id=foreign.board_id)["candidates"]
        assert [(row["project_id"], row["ticket_id"]) for row in unaffected] == [(foreign.project_id, foreign.ticket_id)]
        with provider() as db:
            root = db.get(Chat, receipt["chat_id"])
            root.project_id = ids.project_id
            item = db.query(DevelopmentWorkItem).filter_by(chat_id=root.id).one()
            item.board_key = f"decisions:{foreign.board_id}"
        assert client.get(path).json() == {"status": "unconfirmed"}
        with provider() as db:
            item = db.query(DevelopmentWorkItem).filter_by(chat_id=receipt["chat_id"]).one()
            item.board_key = f"decisions:{ids.board_id}"
            db.get(KanbanTicket, ids.ticket_id).source_chat_id = None
        assert client.get(path).json() == {"status": "unconfirmed"}
        with provider() as db:
            db.get(KanbanTicket, ids.ticket_id).source_chat_id = receipt["chat_id"]
            assert db.query(AutoWorkflowRun).count() == 1
        assert client.get(path).json() == recovered
        assert len(workers) == 1
    finally:
        for response in locals().get("responses", ()):
            if response.status_code == 200:
                dispatcher._initializing_runs.discard(response.json()["run_id"])


def test_folder_approval_supports_older_receipts_and_new_checkout_creation(tmp_path):
    from distr.core.workflow.draft_launch import approved_draft_folder_matches, project_folder_revision

    checkout = tmp_path / "checkout"
    other = tmp_path / "other"
    project = SimpleNamespace(folder_location=str(checkout))
    session = SimpleNamespace(get=lambda *_: project)
    event = SimpleNamespace(project_id=1, payload=json.dumps({"candidate": {"project_folder": str(checkout)}}))
    planned = {"approved_project_folder_revision": project_folder_revision(checkout)}
    checkout.mkdir()
    assert approved_draft_folder_matches(session, event, planned)
    assert approved_draft_folder_matches(session, event, {})
    reviewed = {"approved_project_folder_revision": project_folder_revision(checkout)}
    assert reviewed != planned
    checkout.rename(tmp_path / "old-checkout")
    checkout.mkdir()
    assert approved_draft_folder_matches(session, event, planned)
    assert not approved_draft_folder_matches(session, event, reviewed)
    project.folder_location = str(other)
    assert not approved_draft_folder_matches(session, event, {})
    assert not approved_draft_folder_matches(session, event, planned)


@pytest.mark.parametrize("step_source", [
    "checklist", "description", "prose", "large_checklist", "outcomes", "assigned", "assigned_drift",
    "adapter_scope", "adapter_definition", "adapter_folder", "adapter_symlink", "adapter_replacement",
])
def test_approved_draft_executes_steps_and_records_result_on_the_same_turn(tmp_path, monkeypatch, step_source):
    import threading
    import time
    from distr.core import orchestrator_proactive as proactive
    from distr.core.db import Chat, ChatTurnEvent
    from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketTodo
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.project_cli_backends.base import BackendTaskResult
    from distr.core.project_cli_backends.harness import HarnessHandle, HarnessStatus
    from distr.core.workflow import dispatcher, draft_launch
    from tests.core.workflow_e2e_harness import cleanup_workflow_run_context, wait_for_terminal_run, workflow_patch_stack

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    provider = lambda: _session_ctx(factory)
    monkeypatch.setattr(proactive, "get_session", provider)
    monkeypatch.setattr(draft_launch, "get_session", provider)
    monkeypatch.setattr("distr.core.chat_turns.get_session", provider)
    monkeypatch.setattr("distr.core.chat.get_session", provider)
    monkeypatch.setattr(proactive, "_external_context", lambda: {})
    monkeypatch.setattr(proactive, "_resolve_route", lambda signal: {"backend": "pi", "model": "fixture-model", "model_provider": "ollama"})
    monkeypatch.setattr("distr.core.project_cli_backends.provider_preflight.preflight_provider_route", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.workflow.dispatcher.should_refresh_auto_model_policy", lambda *a, **k: False)
    monkeypatch.setattr("distr.core.workflow.run_briefing.human_checkpoint_enabled", lambda *a, **k: False)
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.workspace_memory.lifecycle.hook_ensure_workspace", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.workspace_memory.lifecycle.handoff_workflow_step", lambda **_: None)
    monkeypatch.setattr("distr.core.developer_context.DeveloperContextAssembler._fetch_workspace", lambda *a: {})
    monkeypatch.setattr("distr.core.workspace_memory.pickup_handoff.build_pickup_brief", lambda *a, **k: "")
    monkeypatch.setattr("distr.core.workspace_memory.pickup_handoff.load_decisions_json", lambda *a, **k: {})
    terminal_activity_before_artifact = []

    def check_terminal_activity(*, run_id, **_):
        with provider() as db:
            run = db.get(AutoWorkflowRun, run_id)
            turn_id = json.loads(run.run_data)["development_turn_id"]
            events = json.loads(db.get(Chat, run.chat_id).params)["workflow_events"]
            terminal_activity_before_artifact.append(
                any(event["type"] == "completed" and event.get("turn_id") == turn_id for event in events)
            )

    monkeypatch.setattr("distr.core.workspace_memory.demo_artifact.write_demo_artifact", check_terminal_activity)
    monkeypatch.setattr("distr.core.qualification.record_workflow_qualification", lambda **_: None)
    monkeypatch.setattr("distr.core.workflow.skill_provision.provision_workflow_skills", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_deliver_run_reply", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.kanban.whatsapp_work_lifecycle.notify_post_completion", lambda **_: None)
    monkeypatch.setattr("distr.core.kanban.jira_work_lifecycle.prepare_completed_jira_review", lambda **_: None)
    terminal_finished = threading.Event()
    complete_run = dispatcher.complete_run

    def observe_finalization(*args, **kwargs):
        try:
            return complete_run(*args, **kwargs)
        finally:
            terminal_finished.set()

    monkeypatch.setattr(dispatcher, "complete_run", observe_finalization)
    stages = ["Inspect checkout labels", "Update the checkout label", "Check the revised label"]
    if step_source == "large_checklist":
        stages += [f"Check the revised label on storefront {number}" for number in range(1, 13)]
    if step_source == "outcomes":
        stages = [
            "The checkout must keep the basket after a declined payment",
            "Order history needs to show which payment attempts were declined",
            "The support view must show the payment reference",
        ]
    constraint = "The workflow must not contact customers or change the payment gateway."
    calls = []

    async def simulated_worker(context):
        calls.append((context.project.id, context.ticket_id, context.board_id, context.chat_id,
                      context.run_id, context.step_id, context.backend_id, context.model, context.instruction))
        if step_source == "assigned_drift" and len(calls) == 1:
            with provider() as db:
                db.get(AutoWorkflow, assigned_workflow_id).steps[-1].instruction = "Unapproved next step"
        return HarnessHandle(backend_id="pi", status=HarnessStatus.DONE, result=BackendTaskResult(
            success=True, backend_id="pi", engine="fixture", output=f"Checked stage {len(calls)} with fixture evidence.",
        ))

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", simulated_worker)
    if step_source.startswith("adapter_"):
        from pathlib import Path
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import timing

        resolve_timing = timing.resolve_worker_timing
        foreign = _seed_work_signal(factory, tmp_path / "adapter-target")
        if step_source == "adapter_replacement":
            with provider() as db:
                Path(db.get(Project, ids.project_id).folder_location).mkdir(exist_ok=True)
        if step_source == "adapter_symlink":
            checkout = tmp_path / "checkout-link"
            with provider() as db:
                project = db.get(Project, ids.project_id)
                checkout.symlink_to(project.folder_location, target_is_directory=True)
                project.folder_location = str(checkout)

        def drift_during_timing(**kwargs):
            with provider() as db:
                run = db.query(AutoWorkflowRun).filter_by(ticket_id=ids.ticket_id).one()
                if step_source == "adapter_scope":
                    db.get(KanbanTicket, ids.ticket_id).linked_project_id = foreign.project_id
                elif step_source in {"adapter_folder", "adapter_symlink", "adapter_replacement"}:
                    project = db.get(Project, ids.project_id)
                    destination = db.get(Project, foreign.project_id).folder_location
                    if step_source == "adapter_symlink":
                        path = Path(project.folder_location)
                        path.unlink()
                        path.symlink_to(destination, target_is_directory=True)
                    elif step_source == "adapter_replacement":
                        path = Path(project.folder_location)
                        path.rename(tmp_path / "old-checkout")
                        path.mkdir()
                    else:
                        project.folder_location = destination
                else:
                    db.get(AutoWorkflowStep, run.current_step_id).instruction = "Changed while preparing the worker"
            return resolve_timing(**kwargs)

        monkeypatch.setattr(timing, "resolve_worker_timing", drift_during_timing)
    assigned_workflow_id = None
    with provider() as db:
        ticket = db.get(KanbanTicket, ids.ticket_id)
        ticket.title, ticket.description, ticket.priority = "Checkout label work", "Check the local checkout label.", "medium"
        db.get(KanbanLane, ticket.lane_id).name = "Backlog"
        if step_source in {"assigned", "assigned_drift"}:
            workflow = AutoWorkflow(name="Saved checkout review", status="active",
                                    start_step_position=1,
                                    run_settings=json.dumps({"execution_mode": "sequential"}))
            db.add(workflow)
            db.flush()
            ticket.linked_workflow_id = assigned_workflow_id = workflow.id
            ticket.workflow_queue_position = 1
            for position, instruction in enumerate(stages):
                workflow.steps.append(AutoWorkflowStep(position=position, name=instruction,
                    instruction=instruction, action_type="send_to_project_cli", step_type="send_to_project_cli",
                    linked_project_id=ids.project_id))
            db.add(KanbanTicket(lane_id=ticket.lane_id, title="Unapproved next ticket",
                                linked_project_id=ids.project_id, linked_workflow_id=workflow.id,
                                workflow_queue_position=2))
        elif step_source in {"checklist", "large_checklist"} or step_source.startswith("adapter_"):
            for position, instruction in enumerate(stages):
                db.add(KanbanTicketTodo(ticket_id=ticket.id, text=instruction, position=position))
        elif step_source == "description":
            ticket.description += "\n\n## Implementation steps\n"
            ticket.description += "\n".join(f"{i}. {instruction}" for i, instruction in enumerate(stages, 1))
        elif step_source == "outcomes":
            ticket.description += "\n\n" + ". ".join(stages) + f". {constraint}"
        else:
            ticket.description += "\n\n" + ". ".join(
                f"{marker}, {instruction}" for marker, instruction in zip(("First", "Then", "Finally"), stages)
            ) + "."

    with contextlib.ExitStack() as stack:
        for patcher in workflow_patch_stack(factory, tmp_path):
            if patcher.attribute not in {"_run_send_to_project_cli", "record_workflow_chat_event"}:
                stack.enter_context(patcher)
        candidates = proactive.run_proactive_check(board_id=ids.board_id)["candidates"]
        candidate = next(row for row in candidates if row["ticket_id"] == ids.ticket_id)
        assert candidate["recommended_action"] == "review_workflow_draft"
        preview = candidate["workflow_draft"]
        assert [step["instruction"] for step in preview["steps"]] == stages[:12]
        assert preview["omitted_steps"] == max(0, len(stages) - 12)
        review = proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id)
        assert [step["instruction"] for step in review["draft"]["steps"]] == stages
        assert review["draft"]["omitted_steps"] == 0
        assert review["draft"]["checklist_revision"] == preview["checklist_revision"]
        assert not calls
        if step_source == "outcomes":
            dispatch = proactive.dispatch_proactive_candidate(candidate["candidate_id"])
            assert not dispatch["success"] and "workflow draft for review" in dispatch["error"]
        if step_source in {"prose", "large_checklist", "outcomes"}:
            from distr.core.workflow.development_threads import DevelopmentThreadConflict

            with provider() as db:
                if step_source in {"prose", "outcomes"}:
                    db.get(KanbanTicket, ids.ticket_id).description += "\n\nKeep the receipt available for audit."
                else:
                    last = db.query(KanbanTicketTodo).filter_by(ticket_id=ids.ticket_id, position=len(stages) - 1).one()
                    stages[-1] = last.text = "Check the revised label on the trade storefront"
            assert proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id) == {"status": "stale"}
            with pytest.raises(DevelopmentThreadConflict, match="out of date"):
                draft_launch.approve_workflow_draft(
                    board_id=ids.board_id, ticket_id=ids.ticket_id, candidate_id=candidate["candidate_id"],
                    revision=candidate["workflow_draft"]["checklist_revision"],
                )
            assert not calls
            revised = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
            assert revised["candidate_id"] != candidate["candidate_id"]
            if step_source == "outcomes":
                assert revised["workflow_draft"]["checklist_revision"] != preview["checklist_revision"]
            candidate = revised
        receipt = draft_launch.approve_workflow_draft(
            board_id=ids.board_id, ticket_id=ids.ticket_id, candidate_id=candidate["candidate_id"],
            revision=candidate["workflow_draft"]["checklist_revision"],
        )
        run_id, chat_id = receipt["run_id"], receipt["chat_id"]
        try:
            timeout = 180 if step_source == "large_checklist" else 30
            run = wait_for_terminal_run(factory, run_id, auto_continue=False, timeout=timeout)
            if step_source.startswith("adapter_"):
                from distr.core.db.workflow import AutoWorkflowStepResult

                assert calls == []
                assert run.status == "failed"
                scope_changed = step_source == "adapter_scope"
                folder_changed = step_source in {"adapter_folder", "adapter_symlink", "adapter_replacement"}
                if folder_changed:
                    reason = draft_launch.WORKFLOW_APPROVAL_FOLDER_CHANGED
                else:
                    reason = draft_launch.WORKFLOW_APPROVAL_SCOPE_CHANGED if scope_changed else draft_launch.WORKFLOW_DEFINITION_CHANGED
                assert json.loads(run.run_data)["terminal_warning"] == reason
                if step_source == "adapter_definition":
                    assert terminal_finished.wait(5)
                with provider() as db:
                    assert db.query(AutoWorkflowStepResult).filter_by(run_id=run_id).count() == 0
                    if scope_changed:
                        ticket = db.get(KanbanTicket, ids.ticket_id)
                        assert ticket.linked_project_id == foreign.project_id
                        assert ticket.workflow_status == "running"
                    elif folder_changed:
                        assert db.get(KanbanTicket, ids.ticket_id).workflow_status == "failed"
                if folder_changed:
                    review = proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id)
                    assert review["run_id"] == run_id and review["warning"] == reason
                    replacement = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
                    assert replacement["candidate_id"] != candidate["candidate_id"]
                    assert proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id)["status"] == "awaiting_review"
                return
            assert terminal_finished.wait(5), "Terminal workflow finalization did not finish"
            if step_source == "assigned_drift":
                assert run.status == "failed"
                assert len(calls) == 1 and stages[1] in calls[0][-1]
                assert json.loads(run.run_data)["approval_definition_changed"] is True
                assert proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id)["warning"]
                return
            assert run.status == "completed", json.loads(run.run_data).get("result_packet")
            executed_stages = stages[1:] if step_source == "assigned" else stages
            assert len(calls) == len(executed_stages)
            for call, stage in zip(calls, executed_stages):
                assert call[:5] == (ids.project_id, ids.ticket_id, ids.board_id, chat_id, run_id)
                assert call[6:8] == ("pi", "fixture-model")
                assert stage in call[-1]
                if step_source == "outcomes":
                    assert constraint in call[-1]
                    assert "Keep the receipt available for audit." in call[-1]
            turn_id = json.loads(run.run_data)["development_turn_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with provider() as db:
                    events = json.loads(db.get(Chat, chat_id).params)["workflow_events"]
                if terminal_activity_before_artifact and any(
                    event["type"] == "completed" and event.get("turn_id") == turn_id for event in events
                ):
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Terminal workflow activity did not reach its Development turn")
            assert terminal_activity_before_artifact == [True]
            with provider() as db:
                turn_events = db.query(ChatTurnEvent).filter_by(chat_id=chat_id).all()
                assert turn_events and all(row.turn_id == turn_id for row in turn_events)
                if assigned_workflow_id is not None:
                    assert run.workflow_id == assigned_workflow_id
                    assert db.query(AutoWorkflowRun).count() == 1
        finally:
            cleanup_workflow_run_context(run_id)


def test_approved_action_executes_the_checked_snapshot_without_reloading(monkeypatch):
    from distr.core.workflow.step_executor import StepExecutorMixin

    executor = StepExecutorMixin()
    monkeypatch.setattr(executor, "_emit_decisions_action_event", Mock())
    agent = Mock(return_value={"output": "Reviewed", "passed": True})
    monkeypatch.setattr(executor, "_run_agent", agent)
    load = Mock(side_effect=AssertionError("Action must come from the approved step"))
    monkeypatch.setattr("distr.core.workflow.step_executor.get_session", load)
    step = {"action_id": 42, "approved_action": {"id": 42, "title": "Approved Action",
            "is_instruction": True, "instruction_text": "Reviewed instruction", "recording_filename": ""}}
    assert executor._run_decisions_action(step, {})["passed"]
    assert agent.call_args.args[0]["instruction"].endswith("Reviewed instruction")
    load.assert_not_called()


def test_dispatch_proactive_candidate_runs_project_backend_after_approval(tmp_path, monkeypatch):
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.orchestrator_proactive import dispatch_proactive_candidate, run_proactive_check
    from distr.core.project_cli_backends.base import BackendTaskResult

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    with _session_ctx(factory) as session:
        execution = ProjectExecutionSession(ticket_id=ids.ticket_id, project_id=ids.project_id, status="completed")
        session.add(execution)
        session.flush()
        execution_id = execution.id
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator_proactive.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.external_agent_context.build_external_agent_context", lambda limit=8: {})
    monkeypatch.setattr(
        "distr.core.project_cli_backends.get_backend",
        lambda backend_id: SimpleNamespace(setup_status=lambda: SimpleNamespace(ready=True)),
    )
    monkeypatch.setattr(
        "distr.core.orchestrator.inspect_visual_baseline_readiness",
        lambda **kwargs: {"ready": True, "missing_screen_count": 0},
    )

    calls = []

    async def fake_run_project_task(project, instruction, **kwargs):
        calls.append({"project": project.name, "instruction": instruction, "kwargs": kwargs})
        with _session_ctx(factory) as session:
            row = session.get(ProjectExecutionSession, execution_id)
            row.origin = kwargs["origin"]
            row.selected_model = "selected-fixture-model"
            row.input_packet = json.dumps({
                "chat_id": kwargs["chat_id"], "turn_id": kwargs["turn_id"],
                "board_id": kwargs["board_id_override"], "proactive_candidate_id": kwargs["proactive_candidate_id"],
            })
        return BackendTaskResult(
            True,
            kwargs.get("backend_id_override") or "codex",
            "codex",
            output="Status: completed\nSummary: queued",
            execution_session_id=execution_id,
        )

    monkeypatch.setattr("distr.core.orchestrator_proactive.run_project_task", fake_run_project_task)

    scan = run_proactive_check(limit=5)
    candidate_id = scan["candidates"][0]["candidate_id"]
    result = dispatch_proactive_candidate(candidate_id, approved_by="telegram")

    assert result["success"] is True
    # Critical UI repair is deliberately promoted beyond the board's normal
    # frontend preference so Auto mode uses the strongest configured worker.
    assert result["backend_id"] == "codex"
    assert result["execution_session_id"] == execution_id
    assert calls[0]["project"] == "Merrypak"
    assert "checkout page is broken" in calls[0]["instruction"]
    assert calls[0]["kwargs"]["ticket_id"] == ids.ticket_id
    assert calls[0]["kwargs"]["origin"] == "proactive_orchestrator"
    assert calls[0]["kwargs"]["backend_id_override"] == "codex"
    assert result["chat_id"] == calls[0]["kwargs"]["chat_id"]
    assert result["turn_id"] == calls[0]["kwargs"]["turn_id"]

    with _session_ctx(factory) as session:
        ticket = session.get(KanbanTicket, ids.ticket_id)
        root = session.get(Chat, result["chat_id"])
        turn = session.get(Chat, result["turn_id"])
        assert ticket.source_chat_id == root.id
        assert root.project_id == ids.project_id
        assert session.query(DevelopmentWorkItem).filter_by(chat_id=root.id).one().local_ticket_id == ticket.id
        assert turn.parent_id == root.id
        assert turn.response == "Status: completed\nSummary: queued"
        assert session.query(Chat).filter_by(parent_id=root.id).count() == 1
        assert "active_turn_chat_row_id" not in json.loads(root.params)
        route = json.loads(root.params)["development"]["turn_routes"][str(turn.id)]
        assert result["model"] == route["model"] == "selected-fixture-model"
        assert route["backend"] == "codex"
        event_types = [row.event_type for row in session.query(OrchestratorEvent).order_by(OrchestratorEvent.id).all()]
        assert "proactive_work_dispatched" in event_types


def test_proactive_orchestrator_tool_is_voice_first(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    monkeypatch.setattr(
        "distr.core.orchestrator_proactive.run_proactive_check",
        lambda **kwargs: {
            "success": True,
            "spoken_summary": "I found one important work item for Merrypak and I would ask before dispatching it.",
            "summary": {"total_candidates": 1},
            "candidates": [{"candidate_id": 7, "title": "Checkout broken"}],
        },
    )

    result = ProactiveOrchestratorTool()._run(action="scan")

    assert result.startswith("I found one important work item for Merrypak")
    assert "REFERENCE:" in result
    assert "candidate_id" in result
    assert "Hermes" not in result.split("REFERENCE:")[0]


def test_proactive_orchestrator_tool_builds_daily_plan_from_context(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    captured = {}

    def fake_build(self, settings):
        return SimpleNamespace(
            chat_history=[{"role": "user", "content": "What matters today?"}],
            kanban_summary=[{"board_name": "DecisionsAI", "total_tickets": 4}],
            scheduled_sessions=[{"name": "Morning scan"}],
            unfinished_workflows=[{"instruction": "Finish workflow"}],
            skills=[{"id": "ecc"}],
            developer_context={"active_project": {"name": "DecisionsAI"}},
            work_scan={"whatsapp": {"messages": 2}, "gmail": {"messages": 1}},
            memory_user="likes direct, high-signal updates",
            memory_long_term="prefers voice notes",
        )

    def fake_generate(scope, settings, bundle, instruction):
        captured["scope"] = scope
        captured["instruction"] = instruction
        captured["bundle"] = bundle
        return "## Today\n- Handle the DecisionsAI board.\n- Check WhatsApp and Gmail.", {"scope": scope}

    monkeypatch.setattr("distr.core.initiative.context.ContextAssembler.build", fake_build)
    monkeypatch.setattr("distr.core.initiative.planners.generate_planner_markdown", fake_generate)
    monkeypatch.setattr(
        "distr.core.initiative.planners.tts_excerpt_from_markdown",
        lambda markdown, max_len=650: "Handle the DecisionsAI board, then check WhatsApp and Gmail.",
    )

    result = ProactiveOrchestratorTool()._run(action="daily_plan", from_automation_run=True)

    assert result.startswith("Handle the DecisionsAI board")
    assert "REFERENCE:" in result
    assert '"action": "daily_plan"' in result
    assert '"work_scan_sources"' in result
    assert captured["scope"] == "day"


def test_proactive_orchestrator_daily_plan_executes_implied_actions(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    executed = []

    def fake_build(self, settings):
        return SimpleNamespace(
            chat_history=[],
            kanban_summary=[],
            scheduled_sessions=[],
            unfinished_workflows=[],
            skills=[],
            developer_context={},
            work_scan={
                "boards": [
                    {
                        "id": 22,
                        "name": "RelightSA",
                        "lanes": [
                            {"name": "Current", "ticket_count": 0, "tickets": []},
                            {
                                "name": "Backlog",
                                "ticket_count": 1,
                                "tickets": [{"id": 501, "title": "Send quote", "priority": "critical"}],
                            },
                        ],
                    }
                ]
            },
            memory_user="",
            memory_long_term="",
        )

    def fake_execute(**kwargs):
        executed.append(kwargs)
        return {"success": True, "message": "Moved 1 ticket to Current", "ticket_ids": [501]}

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"initiative_allow_ticket_lane_moves": True})
    monkeypatch.setattr("distr.core.initiative.context.ContextAssembler.build", fake_build)
    monkeypatch.setattr(
        "distr.core.initiative.planners.generate_planner_markdown",
        lambda scope, settings, bundle, instruction: ("## Outcome for Today\n- RelightSA: finish Send quote today.", {"period": "day"}),
    )
    monkeypatch.setattr(
        "distr.core.initiative.planners.tts_excerpt_from_markdown",
        lambda markdown, max_len=650: "RelightSA: finish Send quote today.",
    )
    monkeypatch.setattr("distr.core.initiative.action_handlers.execute_initiative_action", fake_execute)

    result_text = ProactiveOrchestratorTool()._run(action="daily_plan", format="json", from_automation_run=True)
    result = json.loads(result_text)

    assert result["orchestration_results"][0]["message"] == "Moved 1 ticket to Current"
    assert executed[0]["action_type"] == "ticket_lane_move"
    assert executed[0]["payload"]["ticket_ids"] == [501]


def test_proactive_orchestrator_daily_plan_respects_lane_move_permission(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    def fake_build(self, settings):
        return SimpleNamespace(
            chat_history=[],
            kanban_summary=[],
            scheduled_sessions=[],
            unfinished_workflows=[],
            skills=[],
            developer_context={},
            work_scan={
                "boards": [
                    {
                        "id": 22,
                        "name": "RelightSA",
                        "lanes": [
                            {"name": "Current", "ticket_count": 0, "tickets": []},
                            {
                                "name": "Backlog",
                                "ticket_count": 1,
                                "tickets": [{"id": 501, "title": "Send quote", "priority": "critical"}],
                            },
                        ],
                    }
                ]
            },
            memory_user="",
            memory_long_term="",
        )

    def fail_execute(**kwargs):
        raise AssertionError("planner should not execute lane moves without permission")

    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"initiative_allow_ticket_lane_moves": False})
    monkeypatch.setattr("distr.core.initiative.context.ContextAssembler.build", fake_build)
    monkeypatch.setattr(
        "distr.core.initiative.planners.generate_planner_markdown",
        lambda scope, settings, bundle, instruction: ("## Outcome for Today\n- RelightSA: finish Send quote today.", {"period": "day"}),
    )
    monkeypatch.setattr(
        "distr.core.initiative.planners.tts_excerpt_from_markdown",
        lambda markdown, max_len=650: "RelightSA: finish Send quote today.",
    )
    monkeypatch.setattr("distr.core.initiative.action_handlers.execute_initiative_action", fail_execute)

    result_text = ProactiveOrchestratorTool()._run(action="daily_plan", format="json", from_automation_run=True)
    result = json.loads(result_text)

    assert result["orchestration_actions"][0]["action_type"] == "ticket_lane_move"
    assert result["orchestration_results"][0]["success"] is False
    assert "need approval" in result["orchestration_results"][0]["message"]


def test_project_activity_includes_project_events_validations_and_rules(tmp_path, monkeypatch):
    from distr.core.orchestrator import (
        emit_event,
        list_project_activity,
        record_learning_signal,
        record_validation,
    )

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))

    emit_event(
        source="proactive_orchestrator",
        event_type="proactive_check_run",
        status="completed",
        project_id=ids.project_id,
        board_id=ids.board_id,
        summary="Checked project work sources.",
    )
    record_validation(
        project_id=ids.project_id,
        board_id=ids.board_id,
        validation_snapshot={"verdict": "pass", "validation_type": "unit", "observed": "ok"},
    )
    record_learning_signal(
        scope="project",
        scope_id=ids.project_id,
        rule_type="routing_hint",
        summary="Frontend tickets for Merrypak are best reviewed in Cursor.",
    )

    activity = list_project_activity(ids.project_id)

    assert activity["project_id"] == ids.project_id
    assert any(event["event_type"] == "proactive_check_run" for event in activity["events"])
    assert any(validation["validation_type"] == "unit" for validation in activity["validations"])
    assert any(rule["rule_type"] == "routing_hint" for rule in activity["learned_rules"])


def test_nested_project_activity_stays_with_its_owner_even_in_parent_only_scan(tmp_path, monkeypatch):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.orchestrator_proactive import run_proactive_check

    factory = _factory(tmp_path)
    parent = _seed_work_signal(factory, tmp_path)
    parent_folder = tmp_path / 'www.merrypak.co.za'
    child_folder = parent_folder / 'fulfillment'
    with _session_ctx(factory) as db:
        child = Project(name='Fulfillment', folder_location=str(child_folder))
        db.add(child)
        db.flush()
        child_id = child.id
        board = KanbanBoard(name='Fulfillment', default_project_id=child_id)
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name='Inbox', position=0)
        db.add(lane)
        db.flush()
        db.add(KanbanTicket(lane_id=lane.id, title='Check packing slips', position=0))

    monkeypatch.setattr('distr.core.orchestrator_proactive._resolve_route', lambda signal: {})
    monkeypatch.setattr('distr.core.orchestrator_proactive.get_session', lambda: _session_ctx(factory))
    monkeypatch.setattr('distr.core.external_agent_context.build_external_agent_context', lambda limit=8: {
        'codex_threads': [
            {'cwd': str(parent_folder / 'checkout'), 'title': 'Checkout review'},
            {'cwd': str(child_folder / 'packing'), 'title': 'Packing review'},
        ],
        'cursor_workspaces': [{'folder': str(child_folder)}],
    })

    expected_parent = {'recent_surfaces': ['Codex'], 'recent_codex_titles': ['Checkout review'],
                       'has_recent_developer_context': True}
    scan = run_proactive_check(emit=False)
    context_by_project = {row['project_id']: row['developer_context'] for row in scan['candidates']}
    assert context_by_project[parent.project_id] == expected_parent
    assert context_by_project[child_id]['recent_codex_titles'] == ['Packing review']
    assert context_by_project[child_id]['recent_surfaces'] == ['Codex', 'Cursor']
    scoped = run_proactive_check(project_id=parent.project_id, emit=False)
    assert [row['developer_context'] for row in scoped['candidates']] == [expected_parent]
