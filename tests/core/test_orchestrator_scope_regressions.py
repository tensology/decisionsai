"""Orchestration decisions use current exact project scope and durable claims."""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from distr.core import orchestrator_proactive as proactive
from distr.core import orchestrator_daily_triage as triage
from tests.core.test_orchestrator_proactive import _factory, _session_ctx, _seed_work_signal


@pytest.fixture
def work(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)
    monkeypatch.setattr(proactive, 'get_session', lambda:_session_ctx(factory))
    monkeypatch.setattr('distr.core.orchestrator.get_session', lambda:_session_ctx(factory))
    monkeypatch.setattr(proactive, '_external_context', lambda:{})
    monkeypatch.setattr(proactive, '_resolve_route', lambda signal:{'backend':'pi', 'model':''})
    backend = Mock(return_value=SimpleNamespace(success=True, backend_id='pi', engine='pi', execution_session_id=None, output='accepted', error=''))
    monkeypatch.setattr(proactive, '_run_backend_task', backend)
    return factory, ids, backend


def candidate_id():
    return proactive.run_proactive_check()['candidates'][0]['candidate_id']


def test_active_proactive_status_and_stop_use_only_the_owned_session(work, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.project_cli_backends import registry
    from distr.core.workflow import development_harness as harness
    from distr.gui.web.routes.development.threads import register_routes

    factory, ids, backend = work
    monkeypatch.setattr(harness, "get_session", lambda: _session_ctx(factory))
    cid = candidate_id()
    process = Mock(returncode=None)
    foreign_process = Mock(returncode=None)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)

    def live_worker(*args, **kwargs):
        chat_id, turn_id = kwargs["chat_id"], kwargs["turn_id"]
        initial = harness.development_execution_state(chat_id)
        assert initial["status"] == "initializing"
        assert initial["can_stop"] is False
        with _session_ctx(factory) as session:
            packet = {"proactive_candidate_id": cid, "chat_id": chat_id, "turn_id": turn_id,
                      "board_id": ids.board_id, "backend_id": "pi"}
            own = ProjectExecutionSession(project_id=ids.project_id, ticket_id=ids.ticket_id,
                                          origin="proactive_orchestrator", status="running",
                                          route_backend="pi", selected_model="fixture-model", input_packet=json.dumps(packet))
            foreign = ProjectExecutionSession(project_id=ids.project_id, ticket_id=ids.ticket_id,
                                              origin="proactive_orchestrator", status="running",
                                              route_backend="pi", input_packet=json.dumps({**packet, "turn_id": turn_id + 1}))
            session.add_all([own, foreign])
            session.flush()
            session.add(ProjectExecutionEvent(session_id=own.id, status="running", message="Checking project files."))
            own_id, foreign_id = own.id, foreign.id
        options = {"board_id": ids.board_id, "execution_kind": "proactive"}
        registry._register_oneshot_process(ids.project_id, "pi", process, execution_id=own_id, **options)
        registry._register_oneshot_process(ids.project_id, "pi", foreign_process, execution_id=foreign_id, **options)
        try:
            state = harness.development_execution_state(chat_id)
            assert state["status"] == "running"
            assert state["execution_session_id"] == own_id
            assert state["model"] == "fixture-model"
            assert state["summary"] == "Checking project files."
            assert state["can_stop"] is True
            stop_path = f"/workflows/studio/tasks/{chat_id}/execution/stop"
            assert client.post(stop_path, json={"execution_session_id": foreign_id}).status_code == 409
            process.terminate.assert_not_called()
            response = client.post(stop_path, json={"execution_session_id": own_id})
            assert response.status_code == 200, response.text
            assert response.json()["stop_requested"] is True
            assert response.json()["stopped"] is False
            from distr.core.db import Chat
            with _session_ctx(factory) as session:
                assert json.loads(session.get(Chat, chat_id).params)["development"]["proactive_stop_turn_id"] == turn_id
            assert harness.development_execution_state(chat_id)["status"] == "running"
            process.terminate.assert_called_once()
            foreign_process.terminate.assert_not_called()
        finally:
            registry._clear_oneshot_process(ids.project_id, "pi", execution_id=own_id, **options)
            registry._clear_oneshot_process(ids.project_id, "pi", execution_id=foreign_id, **options)
        return SimpleNamespace(success=False, backend_id="pi", engine="pi", output="", error="Worker exited.",
                               execution_session_id=own_id)

    errors = []

    def record_assertions(*args, **kwargs):
        try:
            return live_worker(*args, **kwargs)
        except Exception as exc:
            errors.append(exc)
            raise

    backend.side_effect = record_assertions
    result = proactive.dispatch_proactive_candidate(cid)
    if errors:
        raise errors[0]
    assert result["outcome_recorded"] is True
    assert harness.development_execution_state(result["chat_id"]).get("origin") != "proactive_orchestrator"


def test_unconfirmed_proactive_claim_is_blocked_in_development(work, monkeypatch):
    from distr.core.workflow import development_harness as harness

    factory, _, backend = work
    monkeypatch.setattr(harness, "get_session", lambda: _session_ctx(factory))
    backend.side_effect = RuntimeError("Worker status unavailable")
    result = proactive.dispatch_proactive_candidate(candidate_id())
    state = harness.development_execution_state(result["chat_id"])
    assert state["status"] == "blocked"
    assert state["can_stop"] is False
    assert "recover its outcome" in state["summary"]


def test_two_active_projects_keep_progress_and_stop_in_their_own_threads(work, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.project_cli_backends import registry
    from distr.core.workflow import development_harness as harness

    factory, first, backend = work
    second = _seed_work_signal(factory, tmp_path)
    projects = {item.ticket_id: item for item in (first, second)}
    ready = {ticket: Event() for ticket in projects}
    release = {ticket: Event() for ticket in projects}
    processes = {ticket: Mock(returncode=None) for ticket in projects}
    ownership = {}
    monkeypatch.setattr(harness, "get_session", lambda: _session_ctx(factory))

    def run_worker(*args, **kwargs):
        ticket_id = kwargs["ticket_id"]
        scope = projects[ticket_id]
        chat_id, turn_id = kwargs["chat_id"], kwargs["turn_id"]
        with _session_ctx(factory) as session:
            row = ProjectExecutionSession(
                project_id=scope.project_id, ticket_id=ticket_id, origin=kwargs["origin"],
                status="running", route_backend="pi",
                input_packet=json.dumps({
                    "board_id": scope.board_id, "chat_id": chat_id, "turn_id": turn_id,
                    "proactive_candidate_id": kwargs["proactive_candidate_id"], "backend_id": "pi",
                }),
            )
            session.add(row)
            session.flush()
            execution_id = row.id
            session.add(ProjectExecutionEvent(session_id=execution_id, message=f"Project {scope.project_id} progress"))
        key = {"board_id": scope.board_id, "execution_id": execution_id, "execution_kind": "proactive"}
        registry._register_oneshot_process(scope.project_id, "pi", processes[ticket_id], **key)
        ownership[ticket_id] = (chat_id, execution_id)
        ready[ticket_id].set()
        try:
            assert release[ticket_id].wait(8)
        finally:
            registry._clear_oneshot_process(scope.project_id, "pi", **key)
        with _session_ctx(factory) as session:
            row = session.get(ProjectExecutionSession, execution_id)
            row.status = "completed"
            row.completed_at = datetime.now()
        return SimpleNamespace(success=True, backend_id="pi", engine="pi",
                               execution_session_id=execution_id, output=f"Project {scope.project_id} result", error="")

    backend.side_effect = run_worker
    candidates = {
        item.ticket_id: proactive.run_proactive_check(project_id=item.project_id)["candidates"][0]["candidate_id"]
        for item in (first, second)
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {ticket: pool.submit(proactive.dispatch_proactive_candidate, cid) for ticket, cid in candidates.items()}
        try:
            assert all(event.wait(6) for event in ready.values())
            for ticket, (chat_id, execution_id) in ownership.items():
                state = harness.development_execution_state(chat_id)
                assert state["execution_session_id"] == execution_id
                assert state["summary"] == f"Project {projects[ticket].project_id} progress"
            first_chat, first_execution = ownership[first.ticket_id]
            other_chat, other_execution = ownership[second.ticket_id]
            assert not harness.stop_development_execution(first_chat, expected_session_id=other_execution)["stop_requested"]
            assert harness.stop_development_execution(first_chat, expected_session_id=first_execution)["stop_requested"]
            processes[first.ticket_id].terminate.assert_called_once()
            processes[second.ticket_id].terminate.assert_not_called()
            release[first.ticket_id].set()
            assert pending[first.ticket_id].result(timeout=6)["outcome_recorded"]
            assert not pending[second.ticket_id].done()
            assert harness.development_execution_state(other_chat)["status"] == "running"
        finally:
            for event in release.values():
                event.set()
        results = {ticket: run.result(timeout=6) for ticket, run in pending.items()}
    assert all(result["outcome_recorded"] for result in results.values())
    assert {result["chat_id"] for result in results.values()} == {first_chat, other_chat}


@pytest.mark.parametrize("outcome", [
    "completed", "recovered", "concurrent", "failed", "waiting", "stopped", "edited", "missing_file", "late_queue", "reassigned",
])
def test_proactive_completion_advances_the_exact_queued_followup(work, monkeypatch, tmp_path, outcome):
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
    from distr.core.db.projects import Project
    from distr.core.db.workflow import DevelopmentCommand
    from distr.core.turn_runtime import TurnResult
    from distr.core.workflow import development_harness as harness
    from tests.core.test_proposal_continuity_integration import _join_new_workers
    import threading

    factory, ids, backend = work
    folder = tmp_path / "project"
    folder.mkdir()
    attachment = folder / "details.txt"
    attachment.write_text("Follow-up fixture attachment.")
    with _session_ctx(factory) as session:
        session.get(Project, ids.project_id).folder_location = str(folder)
    for module in ("distr.core.db", "distr.core.chat", "distr.core.chat_turns", "distr.core.workflow.development_harness",
                   "distr.core.workflow.development_control", "distr.core.incoming.media"):
        monkeypatch.setattr(f"{module}.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(harness, "_notify", lambda: None)
    monkeypatch.setattr(harness, "_route", lambda *_: ("pi", "auto", {}, "low"))
    calls = []

    async def followup(request, **kwargs):
        calls.append(request)
        return TurnResult(success=False, backend_id="pi", model="auto", runtime_id="cli_harness",
                          error="Fixture follow-up finished.")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", followup)

    def complete_worker(*args, **kwargs):
        with _session_ctx(factory) as session:
            command = DevelopmentCommand(chat_id=kwargs["chat_id"], command_type="followup", content="Check the result")
            command.metadata_json = json.dumps({
                "attachments": [{"name": attachment.name, "path": str(attachment), "mime_type": "text/plain"}],
                "composer": {"use_playwright": True},
            })
            if outcome == "missing_file":
                attachment.unlink()
            if outcome == "late_queue":
                command.created_at = datetime.now() + timedelta(minutes=1)
            if outcome == "reassigned":
                project = Project(name="Other project")
                session.add(project)
                session.flush()
                session.get(KanbanTicket, ids.ticket_id).linked_project_id = project.id
            status = "failed" if outcome == "failed" else "blocked" if outcome == "waiting" else "completed"
            execution = ProjectExecutionSession(
                project_id=ids.project_id, ticket_id=ids.ticket_id, origin=kwargs["origin"], status=status,
                route_backend="pi", completed_at=datetime.now(),
                input_packet=json.dumps({"proactive_candidate_id": kwargs["proactive_candidate_id"],
                                         "chat_id": kwargs["chat_id"], "turn_id": kwargs["turn_id"],
                                         "board_id": ids.board_id}),
                output_packet=json.dumps({"output": "Approved fixture outcome."}),
            )
            session.add_all([command, execution])
            session.flush()
            if outcome == "stopped":
                root = session.get(Chat, kwargs["chat_id"])
                params = json.loads(root.params)
                params["development"]["proactive_stop_turn_id"] = kwargs["turn_id"]
                root.params = json.dumps(params)
            return SimpleNamespace(success=status == "completed", waits_for_human=status == "blocked",
                                   backend_id="pi", engine="pi", execution_session_id=execution.id,
                                   output="Approved fixture outcome.", error="")

    backend.side_effect = complete_worker
    cid = candidate_id()
    previous = set(threading.enumerate())
    if outcome == "edited":
        dispatch = harness.dispatch_development_prompt

        def edit_during_preparation(*args, **kwargs):
            with _session_ctx(factory) as session:
                command = session.query(DevelopmentCommand).one()
                command.content = "Revised queue entry"
                command.modified_at = datetime.now() + timedelta(seconds=1)
            return dispatch(*args, **kwargs)

        monkeypatch.setattr(harness, "dispatch_development_prompt", edit_during_preparation)
    if outcome == "recovered":
        with monkeypatch.context() as patch:
            patch.setattr(proactive, "_finish_dispatch_claim", lambda *a, **k: None)
            attempt = proactive.dispatch_proactive_candidate(cid)
        assert attempt["outcome_recorded"] is False
        assert not calls
        result = proactive.dispatch_proactive_candidate(cid)
        assert result["outcome_recovered"] is True
    elif outcome == "concurrent":
        from concurrent.futures import ThreadPoolExecutor

        dispatch = harness.dispatch_development_prompt
        first_prepared = threading.Event()
        contenders = threading.Barrier(2)

        def race_after_read(*args, **kwargs):
            first_prepared.set()
            contenders.wait(timeout=8)
            return dispatch(*args, **kwargs)

        monkeypatch.setattr(harness, "dispatch_development_prompt", race_after_read)
        with ThreadPoolExecutor(max_workers=2) as pool:
            original = pool.submit(proactive.dispatch_proactive_candidate, cid)
            assert first_prepared.wait(timeout=8)
            replay = pool.submit(proactive.dispatch_proactive_candidate, cid)
            result = original.result(timeout=10)
            assert replay.result(timeout=10)["success"] is False
        monkeypatch.setattr(harness, "dispatch_development_prompt", dispatch)
    else:
        result = proactive.dispatch_proactive_candidate(cid)
    _join_new_workers(result["chat_id"], previous)
    if outcome not in {"completed", "recovered", "concurrent"}:
        with _session_ctx(factory) as session:
            assert session.query(DevelopmentCommand).one().status == "queued"
            assert session.query(Chat).filter_by(parent_id=result["chat_id"]).count() == 1
        assert not calls
        return
    assert len(calls) == 1
    assert calls[0].metadata["use_playwright"] is True
    assert [file["path"] for file in calls[0].metadata["attachments"]] == [str(attachment.resolve())]
    with _session_ctx(factory) as session:
        command = session.query(DevelopmentCommand).one()
        turn = session.get(Chat, calls[0].scope.turn_id)
        assert command.status == "delivered"
        assert turn.input == command.content and turn.parent_id == result["chat_id"]
        assert json.loads(turn.params)["development_turn"]["queued_command_id"] == command.id
    assert proactive.dispatch_proactive_candidate(cid)["success"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("scope", ["matching", "foreign", "approval", "workflow"])
def test_proactive_handoff_respects_the_tickets_existing_development_thread(work, monkeypatch, scope):
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, DevelopmentWorkItem
    from distr.gui.web.routes.api_extensions import turn_status

    factory, ids, backend = work
    monkeypatch.setattr("distr.core.db.get_session", lambda: _session_ctx(factory))
    with _session_ctx(factory) as session:
        project_id = ids.project_id
        if scope == "foreign":
            other = Project(name="Other project")
            session.add(other)
            session.flush()
            project_id = other.id
        ticket = session.get(KanbanTicket, ids.ticket_id)
        root = Chat(project_id=project_id, title="Existing Development thread",
                    autonomy_level="approval" if scope == "approval" else "full",
                    params=json.dumps({"development": {"ticket_id": ticket.id}, "retained": "context"}))
        session.add(root)
        session.flush()
        chat_id = root.id
        ticket.source_chat_id = chat_id
        if scope == "workflow":
            workflow = AutoWorkflow(name="Selected workflow")
            session.add(workflow)
            session.flush()
            ticket.linked_workflow_id = workflow.id

    cid = candidate_id()
    result = proactive.dispatch_proactive_candidate(cid)
    if scope == "matching":
        backend.assert_called_once()
        assert result["chat_id"] == backend.call_args.kwargs["chat_id"] == chat_id
        assert result["chat_url"] == f"/development/threads/{chat_id}/"
        status = turn_status(chat_id, result["turn_id"])
        assert status["done"] is True
        assert status["assistant_text"] == "accepted"
        replay = proactive.dispatch_proactive_candidate(cid)
        assert replay["turn_id"] == result["turn_id"]
        assert "already has a Development turn" in proactive.run_proactive_check()["spoken_summary"]
        backend.assert_called_once()
    else:
        assert result["success"] is False
        assert proactive._dispatch_state(proactive._candidate_from_event(cid)) is None
        backend.assert_not_called()
    with _session_ctx(factory) as session:
        assert session.query(Chat).filter_by(parent_id=None).count() == 1
        assert json.loads(session.get(Chat, chat_id).params)["retained"] == "context"
        assert session.query(DevelopmentWorkItem).count() == (1 if scope == "matching" else 0)


def test_explicit_ticket_project_overrides_board_default(work):
    from distr.core.db.projects import Project
    from distr.core.db.kanban import KanbanTicket
    factory, ids, _ = work
    with _session_ctx(factory) as session:
        other = Project(name='Different project')
        session.add(other); session.flush()
        session.get(KanbanTicket, ids.ticket_id).linked_project_id = other.id
        other_id=other.id
    assert proactive.run_proactive_check(project_id=ids.project_id)['candidates'] == []
    assert proactive.run_proactive_check(project_id=other_id)['candidates'][0]['project_id'] == other_id


@pytest.mark.parametrize('change', ['done','running','waiting','removed_project','removed_ticket','archived_board','moved_project','changed_description','moved_board','custom_done'])
def test_stale_or_inactive_approval_never_dispatches(work, change):
    from distr.core.db.projects import Project
    from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard
    factory, ids, backend = work
    cid = candidate_id()
    with _session_ctx(factory) as session:
        ticket=session.get(KanbanTicket, ids.ticket_id)
        board=session.get(KanbanBoard, ids.board_id)
        if change=='done': session.get(KanbanLane, ticket.lane_id).name='Done'
        elif change=='running': ticket.workflow_status='running'
        elif change=='waiting': ticket.workflow_status='waiting'
        elif change=='removed_project': session.get(Project,ids.project_id).deleted_at=datetime.utcnow()
        elif change=='removed_ticket': session.delete(ticket)
        elif change=='archived_board': board.archived=True
        elif change=='changed_description': ticket.description='New scope not yet approved'
        elif change=='moved_project':
            other=Project(name='Other');session.add(other);session.flush();ticket.linked_project_id=other.id
        elif change=='moved_board':
            other=KanbanBoard(name='Other board',default_project_id=ids.project_id);session.add(other);session.flush()
            lane=KanbanLane(board_id=other.id,name='Inbox');session.add(lane);session.flush();ticket.lane_id=lane.id
        elif change=='custom_done': board.agent_done_lane='Shipped';session.get(KanbanLane,ticket.lane_id).name='Shipped'
    assert proactive.dispatch_proactive_candidate(cid)['success'] is False
    backend.assert_not_called()


def test_exact_candidate_approval_is_dispatched_once_across_scans(work):
    from distr.core.db.orchestrator import OrchestratorEvent
    factory, _, backend=work
    first=candidate_id()
    assert candidate_id()==first
    assert proactive.dispatch_proactive_candidate(first)['success'] is True
    assert proactive.dispatch_proactive_candidate(first)['success'] is False
    backend.assert_called_once()
    with _session_ctx(factory) as session:
        assert session.get(OrchestratorEvent,first).status=='dispatched'


def test_proactive_finalization_retries_sqlite_lock_without_relaunch(work, monkeypatch):
    from sqlalchemy import event

    from distr.core.db import orm_compat
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent

    factory, ids, backend = work
    engine = factory.kw["bind"]

    @event.listens_for(engine, "checkout")
    def disable_busy_wait(connection, *_):
        connection.execute("PRAGMA busy_timeout=0")

    with _session_ctx(factory) as session:
        execution = ProjectExecutionSession(ticket_id=ids.ticket_id, project_id=ids.project_id, status="completed")
        session.add(execution)
        session.flush()
        execution_id = execution.id

    cid = candidate_id()
    writer = sqlite3.connect(engine.url.database)
    delays = []

    def accepted(*args, **kwargs):
        with _session_ctx(factory) as session:
            row = session.get(ProjectExecutionSession, execution_id)
            row.origin = kwargs["origin"]
            row.input_packet = json.dumps({
                "chat_id": kwargs["chat_id"], "turn_id": kwargs["turn_id"],
                "board_id": kwargs["board_id_override"], "proactive_candidate_id": kwargs["proactive_candidate_id"],
            })
        writer.execute("BEGIN IMMEDIATE")
        return SimpleNamespace(success=True, backend_id="pi", engine="pi", execution_session_id=execution_id,
                               output="Completed approved work", error="")

    def release_lock(delay):
        delays.append(delay)
        writer.rollback()

    backend.side_effect = accepted
    monkeypatch.setattr(orm_compat, "time", SimpleNamespace(sleep=release_lock))
    emit = proactive._emit
    monkeypatch.setattr(proactive, "_emit", lambda **kwargs:
                        None if kwargs["event_type"] == "proactive_work_dispatched" else emit(**kwargs))
    try:
        result = proactive.dispatch_proactive_candidate(cid, approved_by="telegram")
    finally:
        writer.close()
    assert result["success"] is True
    assert result["outcome_recorded"] is True
    assert result["execution_session_id"] == execution_id
    assert delays == [0.05]
    assert proactive.dispatch_proactive_candidate(cid)["success"] is False
    backend.assert_called_once()

    candidate = proactive.run_proactive_check()["candidates"][0]
    assert candidate["dispatch_status"] == "dispatched"
    assert candidate["dispatch_execution_session_id"] == execution_id
    with _session_ctx(factory) as session:
        receipt = session.get(OrchestratorEvent, cid)
        assert receipt.status == "dispatched"
        assert receipt.execution_session_id == execution_id
        assert json.loads(receipt.payload)["dispatch_result"]["backend_id"] == "pi"
        assert json.loads(receipt.payload)["dispatch_result"]["approved_by"] == "telegram"


def test_persistent_finalization_failure_does_not_allow_relaunch(work, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from distr.core.db import orm_compat

    _, _, backend = work
    cid = candidate_id()
    delays = []
    monkeypatch.setattr(orm_compat, "time", SimpleNamespace(sleep=delays.append))
    with monkeypatch.context() as patch:
        patch.setattr(proactive, "_finish_dispatch_claim",
                      Mock(side_effect=OperationalError("UPDATE", (), sqlite3.OperationalError("database is locked"))))
        result = proactive.dispatch_proactive_candidate(cid)
    assert result["outcome_recorded"] is False
    assert "could not save its outcome" in result["spoken_summary"]
    assert delays == [0.05, 0.1]
    assert proactive.dispatch_proactive_candidate(cid)["success"] is False
    backend.assert_called_once()
    assert proactive.run_proactive_check()["candidates"][0]["dispatch_status"] == "dispatching"


def test_dispatch_evidence_does_not_link_another_tickets_execution(work):
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent

    factory, ids, backend = work
    with _session_ctx(factory) as session:
        ticket = session.get(KanbanTicket, ids.ticket_id)
        other = KanbanTicket(lane_id=ticket.lane_id, linked_project_id=ids.project_id, title="Different ticket")
        session.add(other)
        session.flush()
        execution = ProjectExecutionSession(ticket_id=other.id, project_id=ids.project_id, status="completed")
        session.add(execution)
        session.flush()
        backend.return_value.execution_session_id = execution.id

    cid = candidate_id()
    result = proactive.dispatch_proactive_candidate(cid)
    assert result["outcome_recorded"] is True
    assert result["execution_session_id"] is None
    with _session_ctx(factory) as session:
        event = session.get(OrchestratorEvent, cid)
        dispatch_event = session.query(OrchestratorEvent).filter_by(parent_event_id=cid).one()
        assert event.execution_session_id is None
        assert dispatch_event.execution_session_id is None
        assert json.loads(event.payload)["dispatch_result"]["execution_session_id"] is None


@pytest.mark.parametrize(
    ("status", "variation", "expected"),
    [("completed", "", "dispatched"), ("failed", "", "failed"), ("blocked", "", "waiting"),
     ("running", "", None), ("completed", "wrong board", None), ("completed", "wrong turn", None),
     ("completed", "duplicate", None)],
)
def test_retry_recovers_only_the_exact_saved_proactive_execution(work, monkeypatch, status, variation, expected):
    from sqlalchemy.exc import OperationalError

    from distr.core.db import Chat
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent

    factory, ids, backend = work
    cid = candidate_id()
    candidate = proactive._candidate_from_event(cid)
    def save_execution(*args, **kwargs):
        with _session_ctx(factory) as session:
            for _ in range(2 if variation == "duplicate" else 1):
                execution = ProjectExecutionSession(
                    ticket_id=ids.ticket_id, project_id=ids.project_id, origin="proactive_orchestrator",
                    route_backend="pi", status=status, completed_at=None if status == "running" else datetime.now(),
                    input_packet=json.dumps({"proactive_candidate_id": cid,
                                             "chat_id": kwargs["chat_id"],
                                             "turn_id": kwargs["turn_id"] + (variation == "wrong turn"),
                                             "board_id": candidate["board_id"] + (variation == "wrong board")}),
                    output_packet=json.dumps({"output": "Saved worker outcome"}),
                )
                session.add(execution)
            session.flush()
            backend.return_value.execution_session_id = execution.id
        return backend.return_value

    backend.side_effect = save_execution
    with monkeypatch.context() as patch:
        patch.setattr(proactive, "_finish_dispatch_claim",
                      Mock(side_effect=OperationalError("UPDATE", (), sqlite3.OperationalError("disk I/O error"))))
        assert proactive.dispatch_proactive_candidate(cid, approved_by="reviewer")["outcome_recorded"] is False

    response = proactive.dispatch_proactive_candidate(cid)
    execution_id = backend.return_value.execution_session_id
    backend.assert_called_once()
    with _session_ctx(factory) as session:
        event = session.get(OrchestratorEvent, cid)
        if expected is None:
            assert response["success"] is False
            assert event.execution_session_id is None
            assert session.get(Chat, response["turn_id"]).response is None
        else:
            assert response["outcome_recorded"] is True
            assert response["outcome_recovered"] is True
            assert response["execution_session_id"] == event.execution_session_id == execution_id
            assert event.status == expected
            assert response["chat_id"] == backend.call_args.kwargs["chat_id"]
            assert response["turn_id"] == backend.call_args.kwargs["turn_id"]
            assert session.get(Chat, response["turn_id"]).response == "Saved worker outcome"
            assert json.loads(event.payload)["dispatch_result"]["approved_by"] == "reviewer"
            assert proactive.run_proactive_check()["candidates"][0]["dispatch_status"] == expected
    assert backend.call_args.kwargs["proactive_candidate_id"] == cid


def test_worker_waiting_for_review_keeps_the_existing_dispatch(work):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent

    factory, ids, backend = work
    backend.return_value.waits_for_human = True
    cid = candidate_id()
    result = proactive.dispatch_proactive_candidate(cid)
    assert result["outcome_recorded"] is True
    assert result["success"] is False
    assert "paused for human review" in result["spoken_summary"]
    assert proactive.dispatch_proactive_candidate(cid)["success"] is False
    with _session_ctx(factory) as session:
        candidate = session.get(OrchestratorEvent, cid)
        activity = session.query(OrchestratorEvent).filter_by(parent_event_id=cid).one()
        assert candidate.status == activity.status == "waiting"
        assert "waiting for human review" in activity.summary
        session.get(KanbanTicket, ids.ticket_id).title = "Revised while waiting"
    revised = proactive.run_proactive_check()["candidates"][0]
    assert revised["dispatch_status"] == "waiting"
    assert proactive.dispatch_proactive_candidate(revised["candidate_id"])["success"] is False
    backend.assert_called_once()


def test_backend_runtime_error_does_not_repeat_side_effects(monkeypatch):
    calls=[]
    async def run(*args,**kwargs):
        calls.append('accepted before error')
        raise RuntimeError('backend post-accept failure')
    monkeypatch.setattr(proactive,'run_project_task',run)
    with pytest.raises(RuntimeError,match='post-accept'):
        proactive._run_backend_task(SimpleNamespace(),'instruction')
    assert len(calls)==1


def test_backend_exception_remains_failed_and_is_not_redispatched(work):
    _, _, backend=work
    backend.side_effect=RuntimeError('transport unknown after acceptance')
    cid=candidate_id()
    assert proactive.dispatch_proactive_candidate(cid)['success'] is False
    assert proactive.dispatch_proactive_candidate(cid)['success'] is False
    backend.assert_called_once()
    candidate=proactive.run_proactive_check()['candidates'][0]
    assert candidate['dispatch_status']=='unknown'
    assert candidate['recommended_action']=='review_dispatch_outcome'


def test_failed_backend_event_does_not_claim_dispatched(work):
    from distr.core.db.orchestrator import OrchestratorEvent
    factory, _, backend=work
    backend.return_value=SimpleNamespace(success=False,backend_id='pi',engine='pi',execution_session_id=None,output='',error='not ready')
    assert proactive.dispatch_proactive_candidate(candidate_id())['success'] is False
    with _session_ctx(factory) as session:
        event=session.query(OrchestratorEvent).filter_by(event_type='proactive_work_dispatched').one()
        assert event.status=='failed'
        assert 'could not be dispatched' in event.summary


def test_existing_execution_session_blocks_scan_and_approval(work):
    from distr.core.db.kanban import ProjectExecutionSession
    factory, ids, backend=work
    cid=candidate_id()
    with _session_ctx(factory) as session:
        session.add(ProjectExecutionSession(ticket_id=ids.ticket_id,project_id=ids.project_id,status='running'))
    assert proactive.run_proactive_check()['candidates']==[]
    assert proactive.dispatch_proactive_candidate(cid)['success'] is False
    backend.assert_not_called()


def test_ticket_claim_blocks_new_revision_until_prior_outcome_is_known(work):
    from distr.core.db.kanban import KanbanTicket
    factory, ids, _=work
    old=proactive.run_proactive_check()['candidates'][0]
    assert proactive._claim_dispatch(old)
    with _session_ctx(factory) as session:
        session.get(KanbanTicket,ids.ticket_id).title='New revised scope'
    revised=proactive.run_proactive_check()['candidates'][0]
    assert revised['recommended_action']=='review_dispatch_outcome'
    assert not proactive._claim_dispatch(revised)
    proactive._finish_dispatch_claim(old,success=False,error='unknown transport',unknown=True)
    assert not proactive._claim_dispatch(revised)
    # An explicitly reconciled finished result permits genuinely new work.
    proactive._finish_dispatch_claim(old,success=True)
    assert proactive._claim_dispatch(revised)


def test_old_blocker_is_not_hidden_by_two_hundred_recent_low_items(work):
    from distr.core.db.kanban import KanbanTicket
    factory, ids, _=work
    with _session_ctx(factory) as session:
        original=session.get(KanbanTicket,ids.ticket_id)
        original.modified_date=datetime.utcnow()-timedelta(days=20)
        for index in range(205):
            session.add(KanbanTicket(lane_id=original.lane_id,title=f'Low task {index}',priority='low',linked_project_id=ids.project_id))
    assert proactive.run_proactive_check(limit=1)['candidates'][0]['ticket_id']==ids.ticket_id


def test_developer_context_uses_resolved_path_not_shared_leaf_or_name(tmp_path):
    from distr.core.ide_bridge import project_for_cwd

    folder = tmp_path / 'one' / 'web'
    project = SimpleNamespace(folder_location=str(folder))
    assert project_for_cwd(str(folder / 'src'), [project]) is project
    assert project_for_cwd(str(tmp_path / 'two' / 'web'), [project]) is None
    assert project_for_cwd(str(folder) + '-old', [project]) is None
    assert project_for_cwd('Web project mentioned in unrelated title', [project]) is None


def test_daily_triage_never_combines_projects_or_conversations():
    messages=[{'id':1,'project_id':10,'source_thread_id':'first','text_preview':'First request'},
              {'id':2,'project_id':20,'source_thread_id':'second','text_preview':'Second request'},
              {'id':3,'project_id':10,'source_thread_id':'third','text_preview':'Third request'}]
    candidates=triage.build_daily_triage(work_scan={'messages':{'whatsapp':messages}})['candidates']
    assert len(candidates)==3
    assert {tuple(c['payload']['message_ids']) for c in candidates}=={(1,),(2,),(3,)}


def test_low_confidence_real_risk_precedes_high_confidence_context():
    result=triage.build_daily_triage(work_scan={'proposals':[{'action_type':'project_cli_task','description':'Production blocker','confidence':0.1,'risk_level':'critical'}]},developer_context={'active':'Recent editor'},max_candidates=1)
    assert result['candidates'][0]['risk_level']=='critical'


def test_enqueue_does_not_starve_new_decision_behind_duplicates(tmp_path):
    from distr.core.initiative.draft_queue import DraftQueue
    queue=DraftQueue(path=str(tmp_path/'drafts.json'))
    candidates=[{'id':'same-prefix-1234567890-a','title':'First'},{'id':'same-prefix-1234567890-b','title':'Second'}]
    assert triage.enqueue_triage_candidates(queue,candidates[:1],limit=1)==1
    assert triage.enqueue_triage_candidates(queue,candidates,limit=1)==1
    assert len({entry.id for entry in queue.get_all()})==2


def test_message_scope_keeps_all_references_and_stable_order():
    messages=[{'id':index,'source_thread_id':'thread'} for index in range(1,13)]
    first=triage._candidates_from_messages('whatsapp',messages)[0]
    second=triage._candidates_from_messages('whatsapp',list(reversed(messages)))[0]
    assert set(first['payload']['message_ids'])==set(range(1,13))
    assert first['id']==second['id']


@pytest.mark.parametrize('value',['invalid',float('nan'),float('inf'),None,0])
def test_bad_confidence_never_loses_other_decisions(value):
    result=triage.build_daily_triage(work_scan={'proposals':[
        {'action_type':'message_triage','description':'Needs review','confidence':value},
        {'action_type':'project_cli_task','description':'Other project','confidence':0.7}]})
    assert len(result['candidates'])==2
    if value==0:
        assert next(c for c in result['candidates'] if c['title']=='Needs review')['confidence']==0


def test_oversight_provenance_survives_triage_and_approval_queue(tmp_path):
    from distr.core.initiative.draft_queue import DraftQueue
    candidates=triage.build_daily_triage(work_scan={'proposals':[{
        'action_type':'project_cli_task','description':'Oversight suggestion',
        'payload':{'provenance':'project_oversight'}}]})['candidates']
    assert candidates[0]['payload']['provenance']=='project_oversight'
    queue=DraftQueue(path=str(tmp_path/'drafts.json'))
    triage.enqueue_triage_candidates(queue,candidates)
    assert queue.get_all()[0].execute_payload['provenance']=='project_oversight'
