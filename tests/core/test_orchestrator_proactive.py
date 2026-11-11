from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

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


def test_approved_draft_launch_is_atomic_and_duplicate_clicks_return_one_run(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import orchestrator_proactive as proactive
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketTodo
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, DevelopmentWorkItem
    from distr.core.workflow import dispatcher, draft_launch
    from distr.gui.web.routes.development.workflows import register_routes

    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    provider = lambda: _session_ctx(factory)
    monkeypatch.setattr("distr.core.orchestrator.get_session", provider)
    monkeypatch.setattr(proactive, "get_session", provider)
    monkeypatch.setattr(draft_launch, "get_session", provider)
    monkeypatch.setattr(proactive, "_external_context", lambda: {})
    monkeypatch.setattr(proactive, "_resolve_route", lambda signal: {"backend": "pi", "model": "fixture-model"})
    monkeypatch.setattr(dispatcher, "record_workflow_chat_event", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.orchestration_events.emit_orchestration_event", lambda **kw: None)
    workers = []

    class HeldTimer:
        def __init__(self, delay, callback):
            self.callback = callback

        def start(self):
            workers.append(self.callback)

    monkeypatch.setattr(dispatcher, "threading", SimpleNamespace(Timer=HeldTimer))
    with provider() as db:
        for position, text in enumerate(["Reproduce checkout", "Repair checkout", "Verify checkout"]):
            db.add(KanbanTicketTodo(ticket_id=ids.ticket_id, text=text, position=position))
    candidate = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
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
        assert db.query(AutoWorkflow).count() == db.query(AutoWorkflowRun).count() == db.query(Chat).count() == 0
        ticket = db.get(KanbanTicket, ids.ticket_id)
        db.get(KanbanLane, ticket.lane_id).name = "Backlog"

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
            assert run.chat_id == workflow.chat_id == ticket.source_chat_id == item.chat_id == receipt["chat_id"]
            assert item.project_id == ids.project_id and item.workflow_id == workflow.id
            run_data = json.loads(run.run_data)
            assert run_data["development_turn_id"] == turn.id
            assert run_data["execution_route"]["model"] == "fixture-model"
            assert [(step.instruction, step.linked_project_id) for step in workflow.steps] == [
                ("Reproduce checkout", ids.project_id), ("Repair checkout", ids.project_id), ("Verify checkout", ids.project_id)
            ]
            run.status = ticket.workflow_status = "failed"
        proactive.run_proactive_check(board_id=ids.board_id)
        recovered = client.get(path).json()
        assert recovered["run_id"] == receipt["run_id"] and recovered["run_status"] == "failed"
        assert client.post(path + "/approve", json=approval).json() == recovered
        assert len(workers) == 1
    finally:
        for response in locals().get("responses", ()):
            if response.status_code == 200:
                dispatcher._initializing_runs.discard(response.json()["run_id"])


@pytest.mark.parametrize("step_source", ["checklist", "description"])
def test_approved_draft_executes_steps_and_records_result_on_the_same_turn(tmp_path, monkeypatch, step_source):
    import time
    from distr.core import orchestrator_proactive as proactive
    from distr.core.db import Chat, ChatTurnEvent
    from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketTodo
    from distr.core.project_cli_backends.base import BackendTaskResult
    from distr.core.project_cli_backends.harness import HarnessHandle, HarnessStatus
    from distr.core.workflow import draft_launch
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
    monkeypatch.setattr("distr.core.workspace_memory.pickup_handoff.build_pickup_brief", lambda *a, **k: "")
    monkeypatch.setattr("distr.core.workspace_memory.pickup_handoff.load_decisions_json", lambda *a, **k: {})
    monkeypatch.setattr("distr.core.workspace_memory.demo_artifact.write_demo_artifact", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.workflow.skill_provision.provision_workflow_skills", lambda *a, **k: None)
    stages = ["Inspect checkout labels", "Update the checkout label", "Check the revised label"]
    calls = []

    async def simulated_worker(context):
        calls.append((context.project.id, context.ticket_id, context.board_id, context.chat_id,
                      context.run_id, context.step_id, context.backend_id, context.model, context.instruction))
        return HarnessHandle(backend_id="pi", status=HarnessStatus.DONE, result=BackendTaskResult(
            success=True, backend_id="pi", engine="fixture", output=f"Checked stage {len(calls)} with fixture evidence.",
        ))

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", simulated_worker)
    with provider() as db:
        ticket = db.get(KanbanTicket, ids.ticket_id)
        ticket.title, ticket.description, ticket.priority = "Checkout label work", "Check the local checkout label.", "medium"
        db.get(KanbanLane, ticket.lane_id).name = "Backlog"
        if step_source == "checklist":
            for position, instruction in enumerate(stages):
                db.add(KanbanTicketTodo(ticket_id=ticket.id, text=instruction, position=position))
        else:
            ticket.description += "\n\n## Implementation steps\n"
            ticket.description += "\n".join(f"{i}. {instruction}" for i, instruction in enumerate(stages, 1))

    with contextlib.ExitStack() as stack:
        for patcher in workflow_patch_stack(factory, tmp_path):
            if patcher.attribute not in {"_run_send_to_project_cli", "record_workflow_chat_event"}:
                stack.enter_context(patcher)
        candidate = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
        assert candidate["recommended_action"] == "review_workflow_draft"
        assert [step["instruction"] for step in candidate["workflow_draft"]["steps"]] == stages
        assert not calls
        receipt = draft_launch.approve_workflow_draft(
            board_id=ids.board_id, ticket_id=ids.ticket_id, candidate_id=candidate["candidate_id"],
            revision=candidate["workflow_draft"]["checklist_revision"],
        )
        run_id, chat_id = receipt["run_id"], receipt["chat_id"]
        try:
            run = wait_for_terminal_run(factory, run_id, auto_continue=False, timeout=30)
            assert run.status == "completed", json.loads(run.run_data).get("result_packet")
            assert len(calls) == len(stages)
            for call, stage in zip(calls, stages):
                assert call[:5] == (ids.project_id, ids.ticket_id, ids.board_id, chat_id, run_id)
                assert call[6:8] == ("pi", "fixture-model")
                assert stage in call[-1]
            turn_id = json.loads(run.run_data)["development_turn_id"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with provider() as db:
                    events = json.loads(db.get(Chat, chat_id).params)["workflow_events"]
                if any(event["type"] == "completed" and event.get("turn_id") == turn_id for event in events):
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Terminal workflow activity did not reach its Development turn")
            with provider() as db:
                turn_events = db.query(ChatTurnEvent).filter_by(chat_id=chat_id).all()
                assert turn_events and all(row.turn_id == turn_id for row in turn_events)
        finally:
            cleanup_workflow_run_context(run_id)


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
