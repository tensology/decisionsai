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


def test_foreign_project_step_blocks_assigned_workflow_without_direct_fallback(work, tmp_path):
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    factory, ids, backend = work
    foreign = _seed_work_signal(factory, tmp_path / 'foreign')
    with _session_ctx(factory) as session:
        workflow = AutoWorkflow(name='Other project review')
        workflow.steps.append(AutoWorkflowStep(name='Review', linked_project_id=foreign.project_id))
        session.add(workflow)
        session.flush()
        session.get(KanbanBoard, ids.board_id).default_workflow_id = workflow.id
    candidate = proactive.run_proactive_check(board_id=ids.board_id)['candidates'][0]
    review = proactive.review_workflow_draft(board_id=ids.board_id, ticket_id=ids.ticket_id)
    assert review['status'] == 'blocked'
    assert 'another project' in review['error'] == candidate['approval_question']
    assert not proactive.dispatch_proactive_candidate(candidate['candidate_id'])['success']
    backend.assert_not_called()


@pytest.mark.parametrize('assigned', [False, True])
@pytest.mark.parametrize('change', ['thread_link', 'hidden_project'])
def test_workflow_approval_rechecks_its_scope_after_review(work, tmp_path, monkeypatch, assigned, change):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketTodo
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher, draft_launch
    from distr.core.workflow.development_threads import bind_ticket_development_thread
    from distr.gui.web.routes.development.workflows import register_routes

    factory, affected, backend = work
    independent = _seed_work_signal(factory, tmp_path / 'independent')
    provider = lambda: _session_ctx(factory)
    monkeypatch.setattr(draft_launch, 'get_session', provider)
    monkeypatch.setattr(dispatcher, 'get_session', provider)
    workers = []
    timer = Mock(side_effect=lambda _delay, callback: SimpleNamespace(start=lambda: workers.append(callback)))
    monkeypatch.setattr(dispatcher, 'threading', SimpleNamespace(
        Timer=timer, get_ident=dispatcher.threading.get_ident, Lock=dispatcher.threading.Lock,
    ))
    with provider() as db:
        for scope in (affected, independent):
            ticket = db.get(KanbanTicket, scope.ticket_id)
            db.get(KanbanLane, ticket.lane_id).name = 'Backlog'
            if assigned:
                workflow = AutoWorkflow(name='Approved checkout flow', status='active')
                workflow.steps.append(AutoWorkflowStep(
                    name='Review checkout', instruction='Review checkout', action_type='send_to_project_cli',
                    linked_project_id=scope.project_id,
                ))
                db.add(workflow)
                db.flush()
                ticket.linked_workflow_id = workflow.id
            else:
                for position, text in enumerate(['Inspect checkout', 'Repair checkout', 'Verify checkout']):
                    db.add(KanbanTicketTodo(ticket_id=ticket.id, text=text, position=position))

    candidates = {item['ticket_id']: item for item in proactive.run_proactive_check()['candidates']}
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    api = TestClient(app)

    def path(scope):
        return f'/workflows/studio/boards/{scope.board_id}/tickets/{scope.ticket_id}/workflow-draft'

    def approve(scope, candidate):
        return api.post(path(scope) + '/approve', json={
            'approved': True, 'candidate_id': candidate['candidate_id'],
            'revision': candidate['workflow_draft']['checklist_revision'],
        })

    assert api.get(path(affected)).json()['status'] == 'awaiting_review'
    linked_chat = None
    with provider() as db:
        ticket = db.get(KanbanTicket, affected.ticket_id)
        if change == 'hidden_project':
            db.get(Project, affected.project_id).agent_ignored = True
        else:
            original_date = ticket.modified_date
            root = bind_ticket_development_thread(
                db, ticket, project_id=affected.project_id, board_id=affected.board_id,
                lane_name='Backlog', approved_workflow_id=ticket.linked_workflow_id,
            )
            linked_chat = root.id
            db.flush()
            assert ticket.modified_date == original_date
    review = api.get(path(affected)).json()
    assert review['status'] == ('blocked' if change == 'hidden_project' else 'stale')
    rejected = approve(affected, candidates[affected.ticket_id])
    assert rejected.status_code == 409
    if change == 'hidden_project':
        assert 'hidden from the agent' in review['error'] == rejected.json()['detail']
        assert proactive.run_proactive_check(project_id=affected.project_id)['candidates'] == []
    assert workers == []
    response = approve(independent, candidates[independent.ticket_id])
    assert response.status_code == 200, response.text
    other_run = response.json()['run_id']
    if change == 'hidden_project':
        with provider() as db:
            db.get(Project, affected.project_id).agent_ignored = False
    refreshed = proactive.run_proactive_check(project_id=affected.project_id)['candidates'][0]
    if change == 'thread_link':
        assert refreshed['candidate_id'] != candidates[affected.ticket_id]['candidate_id']
        assert refreshed['workflow_draft']['checklist_revision'] != candidates[affected.ticket_id]['workflow_draft']['checklist_revision']
    response = approve(affected, refreshed)
    assert response.status_code == 200, response.text
    if linked_chat is not None:
        assert response.json()['chat_id'] == linked_chat
    if change == 'hidden_project':
        receipt = response.json()
        with provider() as db:
            db.get(Project, affected.project_id).agent_ignored = True
        assert api.get(path(affected)).json() == receipt
        assert approve(affected, refreshed).json() == receipt
    with provider() as db:
        assert db.get(AutoWorkflowRun, other_run).ticket_id == independent.ticket_id
        assert db.get(AutoWorkflowRun, response.json()['run_id']).ticket_id == affected.ticket_id
    assert len(workers) == 2
    backend.assert_not_called()


@pytest.mark.parametrize('receipt_kind', ['ticket', 'candidate'])
@pytest.mark.parametrize('payload', ['[]', '{}', '"unexpected"', '{"status":"unrecognized"}',
                                     '{"status":"dispatching","chat_id":42}'])
def test_malformed_dispatch_receipt_holds_only_its_ticket(work, tmp_path, receipt_kind, payload):
    from distr.core.db.orchestrator import OrchestratorMaintenanceState

    factory, affected, backend = work
    unaffected = _seed_work_signal(factory, tmp_path / 'other-project')
    candidate = proactive.run_proactive_check(project_id=affected.project_id)['candidates'][0]
    key = (f'proactive-ticket:{affected.ticket_id}' if receipt_kind == 'ticket'
           else f'proactive-dispatch:{proactive._candidate_identity(candidate)}')
    with _session_ctx(factory) as session:
        session.add(OrchestratorMaintenanceState(key=key, value_json=payload))

    candidates = {row['ticket_id']: row for row in proactive.run_proactive_check()['candidates']}
    held = candidates[affected.ticket_id]
    ready = candidates[unaffected.ticket_id]
    assert held['recommended_action'] == 'review_dispatch_outcome'
    assert held['dispatch_status'] == 'unknown'
    assert 'unconfirmed' in held['approval_question']
    assert ready['recommended_action'] == 'ask_approval_to_dispatch'
    rejected = proactive.dispatch_proactive_candidate(held['candidate_id'])
    assert not rejected['success']
    assert rejected['spoken_summary'] == rejected['error'] == held['approval_question']
    backend.assert_not_called()
    assert proactive.dispatch_proactive_candidate(ready['candidate_id'])['success']
    assert backend.call_args.args[0].id == unaffected.project_id
    assert backend.call_args.kwargs['ticket_id'] == unaffected.ticket_id
    with _session_ctx(factory) as session:
        assert session.query(OrchestratorMaintenanceState).filter_by(key=key).one().value_json == payload


def test_malformed_candidate_history_does_not_abort_other_project_scan(work, tmp_path):
    from distr.core.db.orchestrator import OrchestratorEvent

    factory, affected, _ = work
    unaffected = _seed_work_signal(factory, tmp_path / 'other-project')
    with _session_ctx(factory) as session:
        session.add(OrchestratorEvent(
            event_uid='fixture-old-candidate',
            source='proactive_orchestrator', event_type='proactive_work_candidate',
            status='approval_required', ticket_id=affected.ticket_id, project_id=affected.project_id,
            summary='Invalid historical candidate', payload='{"candidate":["old payload"]}',
        ))

    candidates = proactive.run_proactive_check()['candidates']
    assert {row['ticket_id'] for row in candidates if row['candidate_id']} == {
        affected.ticket_id, unaffected.ticket_id,
    }


def test_worker_outcome_survives_malformed_related_history(work):
    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorMaintenanceState

    factory, ids, backend = work
    cid = candidate_id()
    ticket_key = f'proactive-ticket:{ids.ticket_id}'

    def finish_worker(*args, **kwargs):
        with _session_ctx(factory) as session:
            session.get(OrchestratorEvent, cid).payload = '[]'
            session.query(OrchestratorMaintenanceState).filter_by(key=ticket_key).one().value_json = '[]'
        return backend.return_value

    backend.side_effect = finish_worker
    result = proactive.dispatch_proactive_candidate(cid)
    assert result['success'] and result['outcome_recorded']
    with _session_ctx(factory) as session:
        event = session.get(OrchestratorEvent, cid)
        assert json.loads(event.payload)['candidate']['ticket_id'] == ids.ticket_id
        assert json.loads(event.payload)['dispatch_result']['status'] == 'dispatched'
        assert session.query(OrchestratorMaintenanceState).filter_by(key=ticket_key).one().value_json == '[]'


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
            stop_path = f"/workflows/studio/tasks/{chat_id}/execution/stop-exact"
            assert client.post(stop_path, json={"execution_session_id": foreign_id}).status_code == 409
            process.terminate.assert_not_called()
            response = client.post(stop_path, json={"execution_session_id": own_id})
            assert response.status_code == 200, response.text
            assert response.json()["stop_requested"] is True
            assert response.json()["stopped"] is False
            from distr.core.db import Chat
            with _session_ctx(factory) as session:
                assert json.loads(session.get(Chat, chat_id).params)["development"]["proactive_stop_turn_id"] == turn_id
            stopped_state = client.get(stop_path.removesuffix("/stop-exact")).json()
            assert stopped_state["status"] == "running" and stopped_state["stop_pending"] is True
            assert stopped_state["exact_stop"] is None
            assert client.post(stop_path, json={"execution_session_id": own_id}).status_code == 409
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
    "held_scope", "reassigned_recovery", "revised_brief",
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
                   "distr.core.workflow.development_control", "distr.core.incoming.media", "distr.core.planning.prerequisites"):
        monkeypatch.setattr(f"{module}.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(harness, "_notify", lambda: None)
    def route(_project, _metadata, _assessment, *, board_id):
        assert board_id == ids.board_id
        return "pi", "auto", {}, "low"

    monkeypatch.setattr(harness, "_route", route)
    calls = []

    async def followup(request, **kwargs):
        calls.append(request)
        return TurnResult(success=False, backend_id="pi", model="auto", runtime_id="cli_harness",
                          error="Fixture follow-up finished.")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", followup)

    def complete_worker(*args, **kwargs):
        with _session_ctx(factory) as session:
            if outcome == "held_scope":
                session.add(DevelopmentCommand(
                    chat_id=kwargs["chat_id"], command_type="followup", source="app_view",
                    content="Earlier project follow-up", position=0,
                    metadata_json=json.dumps({"app_view_scope": [-1, "", "", None, ""]}),
                ))
            command = DevelopmentCommand(chat_id=kwargs["chat_id"], command_type="followup", content="Check the result")
            if outcome == "held_scope":
                command.position = 1
            command.metadata_json = json.dumps({
                "queued_scope": [ids.project_id, f"decisions:{ids.board_id}", "local", ids.ticket_id, str(ids.ticket_id)],
                "attachments": [{"name": attachment.name, "path": str(attachment), "mime_type": "text/plain"}],
                "composer": {"use_playwright": True},
            })
            if outcome == "missing_file":
                attachment.unlink()
            if outcome == "late_queue":
                command.created_at = datetime.now() + timedelta(minutes=1)
            if outcome in {"reassigned", "reassigned_recovery"}:
                project = Project(name="Other project")
                session.add(project)
                session.flush()
                session.get(KanbanTicket, ids.ticket_id).linked_project_id = project.id
            if outcome == "revised_brief":
                session.get(KanbanTicket, ids.ticket_id).description = "Replacement request."
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
    if outcome in {"recovered", "reassigned_recovery"}:
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
    if outcome in {"reassigned", "reassigned_recovery", "revised_brief"}:
        from distr.core.db.orchestrator import OrchestratorEvent

        assert not result["success"]
        assert "held for review" in result["error"]
        assert "Approved fixture outcome." not in json.dumps(result)
        with _session_ctx(factory) as session:
            turn = session.get(Chat, result["turn_id"])
            assert turn.response == result["error"]
            execution = session.get(ProjectExecutionSession, result["execution_session_id"])
            assert json.loads(execution.output_packet)["output"] == "Approved fixture outcome."
            candidate = session.get(OrchestratorEvent, cid)
            assert candidate.status == "held"
            audit = session.query(OrchestratorEvent).filter_by(parent_event_id=cid).all()
            assert all("Approved fixture outcome." not in (row.evidence or "") for row in audit)
        replay = proactive.dispatch_proactive_candidate(cid)
        assert not replay["success"] and replay["error"] == result["error"]
        assert replay["execution_session_id"] == result["execution_session_id"]
        revised = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
        assert revised["recommended_action"] == "review_dispatch_outcome"
        assert not proactive.dispatch_proactive_candidate(revised["candidate_id"])["success"]
        backend.assert_called_once()
    if outcome not in {"completed", "recovered", "concurrent", "held_scope"}:
        with _session_ctx(factory) as session:
            assert session.query(DevelopmentCommand).one().status == "queued"
            assert session.query(Chat).filter_by(parent_id=result["chat_id"]).count() == 1
        assert not calls
        return
    assert len(calls) == 1
    assert calls[0].metadata["use_playwright"] is True
    assert [file["path"] for file in calls[0].metadata["attachments"]] == [str(attachment.resolve())]
    with _session_ctx(factory) as session:
        command = session.query(DevelopmentCommand).filter_by(content="Check the result").one()
        if outcome == "held_scope":
            assert session.query(DevelopmentCommand).filter_by(content="Earlier project follow-up").one().status == "queued"
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


@pytest.mark.parametrize('change', ['done','running','waiting','removed_project','hidden_project','removed_ticket','archived_board','moved_project','changed_description','moved_board','custom_done'])
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
        elif change=='hidden_project': session.get(Project,ids.project_id).agent_ignored=True
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


def test_project_hidden_during_direct_approval_is_checked_before_the_claim(work, monkeypatch):
    from distr.core.db import Chat
    from distr.core.db.orchestrator import OrchestratorMaintenanceState
    from distr.core.db.projects import Project

    factory, ids, backend = work
    cid = candidate_id()
    claim = proactive._claim_dispatch

    def hide_before_claim(*args, **kwargs):
        with _session_ctx(factory) as session:
            session.get(Project, ids.project_id).agent_ignored = True
        return claim(*args, **kwargs)

    monkeypatch.setattr(proactive, '_claim_dispatch', hide_before_claim)
    result = proactive.dispatch_proactive_candidate(cid)
    assert not result['success'] and result['error'] == proactive.PROJECT_HIDDEN_FROM_AGENT
    backend.assert_not_called()
    with _session_ctx(factory) as session:
        assert session.query(Chat).count() == 0
        assert session.query(OrchestratorMaintenanceState).filter(
            OrchestratorMaintenanceState.key.like('proactive-dispatch:%')).count() == 0


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


@pytest.mark.parametrize("change", ["before_approval", "before_claim"])
def test_direct_approval_rejects_replaced_checkout_without_losing_existing_receipt(work, tmp_path, monkeypatch, change):
    from pathlib import Path
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool
    from distr.core.db import Chat
    from distr.core.db.orchestrator import OrchestratorMaintenanceState
    from distr.core.db.projects import Project

    factory, ids, backend = work
    with _session_ctx(factory) as session:
        checkout = Path(session.get(Project, ids.project_id).folder_location)
    checkout.mkdir()
    first = candidate_id()
    (checkout / "notes.txt").write_text("Ordinary edit.", encoding="utf-8")
    assert candidate_id() == first

    def replace_checkout():
        checkout.rename(tmp_path / "original-checkout")
        checkout.mkdir()

    if change == "before_claim":
        claim = proactive._claim_dispatch

        def change_before_claim(*args, **kwargs):
            replace_checkout()
            return claim(*args, **kwargs)

        monkeypatch.setattr(proactive, "_claim_dispatch", change_before_claim)
    else:
        replace_checkout()

    response = ProactiveOrchestratorTool()._run(action="approve", candidate_id=first)
    spoken, _, reference = response.partition("REFERENCE:")
    assert spoken.strip() == proactive.PROJECT_FOLDER_CHANGED
    blocked = json.loads(reference)
    assert not blocked["success"]
    assert "project folder changed" in blocked["error"].lower()
    backend.assert_not_called()
    with _session_ctx(factory) as session:
        assert session.query(Chat).count() == 0
        assert session.query(OrchestratorMaintenanceState).filter(
            OrchestratorMaintenanceState.key.like("proactive-dispatch:%")).count() == 0

    if change == "before_claim":
        monkeypatch.setattr(proactive, "_claim_dispatch", claim)
    replacement = candidate_id()
    assert replacement != first
    accepted = proactive.dispatch_proactive_candidate(replacement)
    assert accepted["success"]
    backend.assert_called_once()

    checkout.rename(tmp_path / "reviewed-checkout")
    checkout.mkdir()
    reopened = proactive.dispatch_proactive_candidate(replacement)
    assert not reopened["success"] and reopened["chat_id"] == accepted["chat_id"]
    assert candidate_id() == replacement
    backend.assert_called_once()


@pytest.mark.parametrize("change", [
    "replace_directory", "change_setting", "recover_receipt", "pi_timing", "codex_command", "ide_availability",
    "ticket_moved", "brief_edited", "project_hidden", "workflow_added", "thread_unlinked", "prompt_edited",
])
def test_direct_worker_rechecks_scope_after_setup(work, tmp_path, monkeypatch, change):
    from pathlib import Path
    from unittest.mock import AsyncMock

    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanBoard, KanbanTicket, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow
    from distr.core.kanban import project_execution
    from distr.core.project_cli_backends import registry
    from distr.core.project_cli_backends.base import BackendTaskResult

    factory, ids, _ = work
    session_provider = lambda: _session_ctx(factory)
    monkeypatch.setattr("distr.core.db.get_session", session_provider)
    monkeypatch.setattr(project_execution, "get_session", session_provider)
    monkeypatch.setattr(project_execution, "ensure_project_execution_tables", lambda: None)
    monkeypatch.setattr("distr.core.terminal.get_project_runtime_snapshot", lambda _id: {})
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync.persist_worker_feedback", Mock())
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.any_live_session_running", lambda *a, **kw: False)
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.mark_live_session_presence", Mock())
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.set_live_session_running", Mock())
    monkeypatch.setattr(proactive, "_run_backend_task", lambda *a, **kw: asyncio.run(registry.run_project_task(*a, **kw)))
    with session_provider() as session:
        checkout = Path(session.get(Project, ids.project_id).folder_location)
    checkout.mkdir()
    sender = AsyncMock(return_value=BackendTaskResult(True, "pi", "pi", output="Worker result."))
    backend = SimpleNamespace(name="Pi", setup_status=lambda: SimpleNamespace(ready=True), send_task=sender)
    changed = False
    previous_values = []
    scope_changes = {
        "ticket_moved": "changed project or board",
        "brief_edited": "ticket changed",
        "project_hidden": "hidden from the agent",
        "workflow_added": "current workflow",
        "thread_unlinked": "development thread",
        "prompt_edited": "approved development prompt changed",
    }
    other = _seed_work_signal(factory, tmp_path / "independent") if change in scope_changes else None

    def change_scope():
        from sqlalchemy.orm.attributes import flag_modified

        with session_provider() as session:
            ticket = session.get(KanbanTicket, ids.ticket_id)
            if change == "workflow_added":
                workflow = AutoWorkflow(name="Newly assigned", status="active")
                session.add(workflow)
                session.flush()
                row, field, value = session.get(KanbanBoard, ids.board_id), "default_workflow_id", workflow.id
            elif change == "project_hidden":
                row, field, value = session.get(Project, ids.project_id), "agent_ignored", True
            elif change == "ticket_moved":
                row, field, value = ticket, "linked_project_id", other.project_id
            elif change == "thread_unlinked":
                row, field, value = ticket, "source_chat_id", None
            elif change == "prompt_edited":
                turn = session.query(Chat).filter_by(parent_id=ticket.source_chat_id).one()
                row, field, value = turn, "input", "Unreviewed prompt text"
            else:
                row, field, value = ticket, "description", "The client supplied a revised brief."
            if change not in {"brief_edited", "prompt_edited"}:
                previous_values.append((type(row), row.id, field, getattr(row, field)))
            setattr(row, field, value)
            if row is ticket:
                flag_modified(ticket, "modified_date")

    def change_during_setup():
        nonlocal changed
        changed = True
        if change in scope_changes:
            change_scope()
        elif change == "change_setting":
            new_checkout = tmp_path / "different-folder"
            new_checkout.mkdir()
            with session_provider() as session:
                session.get(Project, ids.project_id).folder_location = str(new_checkout)
        else:
            checkout.rename(tmp_path / "previous-directory")
            checkout.mkdir()

    subprocess_launch = AsyncMock(side_effect=RuntimeError("Unexpected process launch"))
    monkeypatch.setattr(registry.asyncio, "create_subprocess_exec", subprocess_launch)
    write_packet = Mock(side_effect=RuntimeError("Unexpected IDE handoff"))
    monkeypatch.setattr("distr.core.project_cli_backends.ide_handoff.write_ide_work_packet", write_packet)
    if change == "pi_timing":
        adapter = registry.PiBackend()
        monkeypatch.setattr(adapter, "setup_status", backend.setup_status)
        monkeypatch.setattr("distr.core.pi_rpc.PiRpcSession.find_pi", lambda: "/mock/pi")
        timing = registry.resolve_worker_timing

        def change_at_timing(**kwargs):
            change_during_setup()
            return timing(**kwargs)

        monkeypatch.setattr(registry, "resolve_worker_timing", change_at_timing)
    elif change == "codex_command":
        adapter = registry.CodexBackend()
        monkeypatch.setattr(adapter, "setup_status", lambda: SimpleNamespace(ready=True, path="/mock/codex"))

        def change_at_command(executable, _task):
            change_during_setup()
            return [executable, "test"]

        monkeypatch.setattr(adapter, "_build_command", change_at_command)
        monkeypatch.setattr(proactive, "_resolve_route", lambda _signal: {"backend": "codex", "model": ""})
    elif change == "ide_availability":
        adapter = registry.CodexIdeBackend()
        monkeypatch.setattr(adapter, "setup_status", backend.setup_status)

        def change_at_availability():
            change_during_setup()
            return SimpleNamespace(ready=True)

        monkeypatch.setattr(adapter, "check_availability", change_at_availability)
        monkeypatch.setattr(proactive, "_resolve_route", lambda _signal: {"backend": "codex_ide", "model": ""})
    else:
        adapter = backend
        append_event = project_execution.append_execution_event

        def change_at_start(session_id, event, **kwargs):
            result = append_event(session_id, event, **kwargs)
            if event == "executor_start" and not changed:
                change_during_setup()
            return result

        monkeypatch.setattr(project_execution, "append_execution_event", change_at_start)
    monkeypatch.setattr(registry, "get_backend", lambda _: adapter)
    first = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]["candidate_id"]
    if change == "recover_receipt":
        with monkeypatch.context() as missed:
            missed.setattr(proactive, "_record_dispatch_result", lambda *_a, **_kw: None)
            assert not proactive.dispatch_proactive_candidate(first)["outcome_recorded"]
        stopped = proactive.dispatch_proactive_candidate(first)
        assert stopped["outcome_recovered"]
    else:
        stopped = proactive.dispatch_proactive_candidate(first)
    assert changed
    sender.assert_not_awaited()
    subprocess_launch.assert_not_awaited()
    write_packet.assert_not_called()
    assert stopped["outcome_recorded"] and not stopped["success"]
    assert scope_changes.get(change, proactive.PROJECT_FOLDER_CHANGED.lower()) in stopped["error"].lower()
    with session_provider() as session:
        execution = session.get(ProjectExecutionSession, stopped["execution_session_id"])
        assert execution.status == "failed" and execution.completed_at is not None
        assert json.loads(execution.output_packet)["proactive_scope_changed"] is True
        assert session.get(Chat, stopped["turn_id"]).response == stopped["error"]
        assert session.get(OrchestratorEvent, first).execution_session_id == execution.id
        handoffs = session.query(OrchestratorEvent).filter_by(execution_session_id=execution.id).all()
        assert any(event.event_type == "backend_handoff_created" and event.status == "prepared" for event in handoffs)
        assert any(event.event_type == "backend_handoff_updated" and event.status == "failed" for event in handoffs)

    if change == "replace_directory" or change in scope_changes:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from distr.gui.web.routes import chat

        monkeypatch.setattr(chat, "get_session", session_provider)
        monkeypatch.setattr("distr.core.chat_turns.get_session", session_provider)
        monkeypatch.setattr(chat, "load_settings_from_db", lambda: {})
        app = FastAPI()
        app.include_router(chat.create_routes(tmp_path))
        with TestClient(app) as client:
            transcript = client.get(f"/chats/{stopped['chat_id']}?surface=development")
        assert transcript.status_code == 200
        assert any(message.get("content") == stopped["error"]
                   and message.get("chat_row_id") == stopped["turn_id"]
                   for message in transcript.json()["messages"] if message["role"] == "assistant")

    if change == "recover_receipt":
        checkout.rmdir()
        (tmp_path / "previous-directory").rename(checkout)
        assert not proactive.dispatch_proactive_candidate(first)["success"]
        sender.assert_not_awaited()
    if other:
        unrelated = proactive.run_proactive_check(board_id=other.board_id)["candidates"][0]
        assert proactive.dispatch_proactive_candidate(unrelated["candidate_id"])["success"]
        sender.assert_awaited_once()
        assert sender.await_args.args[0].project_id == other.project_id
        sender.reset_mock()
    if change == "workflow_added":
        revised = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
        assert revised["recommended_action"] == "review_workflow_draft"
        assert not proactive.dispatch_proactive_candidate(revised["candidate_id"])["success"]
        sender.assert_not_awaited()
    with session_provider() as session:
        for model, row_id, field, value in previous_values:
            setattr(session.get(model, row_id), field, value)
    fresh = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]["candidate_id"]
    assert fresh != first
    monkeypatch.setattr(registry, "get_backend", lambda _: backend)
    launched = proactive.dispatch_proactive_candidate(fresh)
    assert launched["success"] and launched["chat_id"] == stopped["chat_id"]
    sender.assert_awaited_once()
    if change == "brief_edited":
        assert "The client supplied a revised brief." in sender.await_args.args[0].instruction
    if change == "prompt_edited":
        assert "Unreviewed prompt text" not in sender.await_args.args[0].instruction
    reopened = proactive.dispatch_proactive_candidate(first)
    assert not reopened["success"] and reopened["turn_id"] == stopped["turn_id"]
    assert reopened["execution_session_id"] == stopped["execution_session_id"]
    assert not proactive.dispatch_proactive_candidate(fresh)["success"]
    sender.assert_awaited_once()


@pytest.mark.parametrize("change", ["none", "ticket_project", "thread_project"])
def test_real_proactive_worker_publishes_feedback_only_after_scope_review(work, tmp_path, monkeypatch, change):
    from unittest.mock import AsyncMock
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.kanban import project_execution
    from distr.core.project_cli_backends import registry
    from distr.core.project_cli_backends.base import BackendTaskResult

    factory, ids, _ = work
    other = _seed_work_signal(factory, tmp_path / "another-project")
    session_provider = lambda: _session_ctx(factory)
    monkeypatch.setattr("distr.core.db.get_session", session_provider)
    monkeypatch.setattr(project_execution, "get_session", session_provider)
    monkeypatch.setattr(project_execution, "ensure_project_execution_tables", lambda: None)
    monkeypatch.setattr("distr.core.terminal.get_project_runtime_snapshot", lambda _id: {})
    feedback = Mock()
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync.persist_worker_feedback", feedback)
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.any_live_session_running", lambda *a, **kw: False)
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.mark_live_session_presence", Mock())
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.set_live_session_running", Mock())
    monkeypatch.setattr(proactive, "_run_backend_task", lambda *a, **kw: asyncio.run(registry.run_project_task(*a, **kw)))

    raw_result = "Isolated worker evidence from the approved project."

    async def complete(task, **_kwargs):
        with session_provider() as db:
            if change == "ticket_project":
                db.get(KanbanTicket, ids.ticket_id).linked_project_id = other.project_id
            elif change == "thread_project":
                db.get(Chat, task.chat_id).project_id = other.project_id
        return BackendTaskResult(True, "pi", "pi", output=raw_result)

    sender = AsyncMock(side_effect=complete)
    backend = SimpleNamespace(name="Pi", setup_status=lambda: SimpleNamespace(ready=True), send_task=sender)
    monkeypatch.setattr(registry, "get_backend", lambda _: backend)
    finish = proactive._record_dispatch_result

    def observe_before_review(candidate, **kwargs):
        if not kwargs.get("recover"):
            feedback.assert_not_called()
            with session_provider() as db:
                session = db.get(ProjectExecutionSession, kwargs["execution_session_id"])
                preview = project_execution.serialize_execution_session(session, include_events=True)
                assert preview["status"] == "pending_review"
                assert raw_result not in json.dumps(preview)
        return finish(candidate, **kwargs)

    monkeypatch.setattr(proactive, "_record_dispatch_result", observe_before_review)
    cid = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]["candidate_id"]
    result = proactive.dispatch_proactive_candidate(cid)
    sender.assert_awaited_once()
    with session_provider() as db:
        session = db.get(ProjectExecutionSession, result["execution_session_id"])
        task = sender.await_args.args[0]
        turn = db.get(Chat, task.turn_id)
        assert json.loads(session.output_packet)["output"] == raw_result
        audit = db.query(OrchestratorEvent).filter_by(execution_session_id=session.id).all()
        backend_updates = [event for event in audit if event.event_type == "backend_handoff_updated"]
        assert backend_updates and all(raw_result not in (event.evidence or "") for event in backend_updates)
        if change == "none":
            assert result["success"] and turn.response == raw_result
            assert project_execution.list_execution_sessions_for_ticket(ids.ticket_id)[0]["status"] == "completed"
            feedback.assert_called_once()
            assert feedback.call_args.kwargs["ticket_id"] == ids.ticket_id
        else:
            assert not result["success"] and raw_result not in json.dumps(result)
            assert raw_result not in json.dumps(project_execution.serialize_execution_session(session, include_events=True))
            history = project_execution.list_execution_sessions_for_ticket(ids.ticket_id)
            if change == "thread_project":
                assert "chat_id" not in result and turn.response is None
                assert history[0]["status"] == "held"
            else:
                assert turn.response == proactive.PROACTIVE_RESULT_HELD and not history
            feedback.assert_not_called()
    replay = proactive.dispatch_proactive_candidate(cid)
    assert not replay["success"]
    if change == "thread_project":
        assert "chat_id" not in replay
    assert feedback.call_count == (1 if change == "none" else 0)
    sender.assert_awaited_once()

    if change == "ticket_project":
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from distr.gui.web.routes import chat

        monkeypatch.setattr(chat, "get_session", session_provider)
        monkeypatch.setattr("distr.core.chat_turns.get_session", session_provider)
        monkeypatch.setattr(chat, "load_settings_from_db", lambda: {})
        app = FastAPI()
        app.include_router(chat.create_routes(tmp_path))
        with TestClient(app) as client:
            transcript = client.get(f"/chats/{result['chat_id']}?surface=development")
        assert transcript.status_code == 200
        assert raw_result not in transcript.text
        assert any(message.get("content") == proactive.PROACTIVE_RESULT_HELD
                   for message in transcript.json()["messages"] if message["role"] == "assistant")


@pytest.mark.parametrize("case", ["matching", "moved", "early", "moved_during_open"])
@pytest.mark.parametrize("source", ["codex", "cursor"])
def test_proactive_ide_callback_finalizes_the_exact_approved_turn(work, tmp_path, monkeypatch, case, source):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
    from distr.core.db.projects import Project
    from distr.core.kanban import project_execution
    from distr.core.project_cli_backends import registry
    from distr.core.project_cli_backends.base import BackendTaskResult
    from distr.gui.web.routes import chat
    from distr.gui.web.routes.development.threads import register_routes as register_threads
    from distr.gui.web.routes.ide_bridge import create_routes

    factory, ids, _ = work
    provider = lambda: _session_ctx(factory)
    monkeypatch.setattr("distr.core.db.get_session", provider)
    monkeypatch.setattr("distr.core.ide_bridge.get_session", provider)
    monkeypatch.setattr("distr.core.chat.get_session", provider)
    monkeypatch.setattr("distr.core.chat_turns.get_session", provider)
    monkeypatch.setattr("distr.core.workflow.development_harness.get_session", provider)
    monkeypatch.setattr(chat, "get_session", provider)
    monkeypatch.setattr(chat, "load_settings_from_db", lambda: {})
    monkeypatch.setattr(project_execution, "get_session", provider)
    monkeypatch.setattr(project_execution, "ensure_project_execution_tables", lambda: None)
    monkeypatch.setattr("distr.core.terminal.get_project_runtime_snapshot", lambda _: {})
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.any_live_session_running", lambda *a, **kw: False)
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.mark_live_session_presence", Mock())
    monkeypatch.setattr("distr.core.project_cli_backends.live_sessions.set_live_session_running", Mock())
    feedback = Mock()
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync.persist_worker_feedback", feedback)
    monkeypatch.setattr(proactive, "_run_backend_task", lambda *a, **kw: asyncio.run(registry.run_project_task(*a, **kw)))
    backend_id = f"{source}_ide"
    monkeypatch.setattr(proactive, "_resolve_route", lambda _: {"backend": backend_id, "model": ""})
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    other = _seed_work_signal(factory, tmp_path / "other")
    with provider() as db:
        db.get(Project, ids.project_id).folder_location = str(checkout)
    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    router = APIRouter()
    register_threads(router, None)
    app.include_router(router, prefix="/api")
    app.include_router(chat.create_routes(tmp_path), prefix="/api")
    client = TestClient(app)
    output = "Result from the exact IDE worker."
    held = case in {"moved", "moved_during_open"}
    callback_results = []
    tasks = []

    def report(task, **overrides):
        return client.post("/api/ide/sessions/event", json={
            "source": source, "event_type": f"{source}_completed", "status": "completed",
            "cwd": str(checkout), "project_id": ids.project_id, "session_id": task.execution_session_id,
            "ticket_id": ids.ticket_id,
            "output": output, **overrides,
        })

    async def launch(task, **_kwargs):
        tasks.append(task)
        if case == "early":
            callback_results.append(report(task))
        if case == "moved_during_open":
            with provider() as db:
                db.get(KanbanTicket, ids.ticket_id).linked_project_id = other.project_id
        return BackendTaskResult(True, backend_id, backend_id, output="Opened IDE.", waits_for_human=True)

    adapter = SimpleNamespace(name="Codex IDE", setup_status=lambda: SimpleNamespace(ready=True), send_task=launch)
    monkeypatch.setattr(registry, "get_backend", lambda _: adapter)
    cid = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]["candidate_id"]
    approval = proactive.dispatch_proactive_candidate(cid)
    task, = tasks
    assert approval["outcome_recorded"] and approval["success"] == (case == "early")
    if case in {"matching", "moved"}:
        with provider() as db:
            assert db.get(Chat, task.turn_id).response is None
        pending = client.get(f"/api/workflows/studio/tasks/{task.chat_id}/execution")
        assert pending.status_code == 200
        assert pending.json()["waiting_kind"] == "ide_handoff"
        assert pending.json()["exact_stop"] is None
        assert pending.json().get("last_proactive_result_turn_id") is None
        progress = report(task, status="observed", event_type=f"{source}_progress", output="Unreviewed progress.")
        assert progress.status_code == 200 and progress.json()["session"]["status"] == "waiting"
        if case == "moved":
            with provider() as db:
                db.get(KanbanTicket, ids.ticket_id).linked_project_id = other.project_id
        assert report(task, source="cursor" if source == "codex" else "codex").status_code == 404
        assert report(task, cwd=str(tmp_path)).status_code == 404
        assert report(task, ticket_id=other.ticket_id).status_code == 404
    if case != "early":
        callback_results.append(report(task))
    reply, = callback_results
    assert reply.status_code == 200
    assert reply.json()["session"]["status"] == ("held" if held else "completed")
    assert report(task, output="Conflicting duplicate.").status_code == 200
    with provider() as db:
        execution = db.get(ProjectExecutionSession, task.execution_session_id)
        assert execution.status == "completed" and execution.completed_at
        assert json.loads(execution.output_packet)["output"] == output
        assert db.get(Chat, task.turn_id).response == (proactive.PROACTIVE_RESULT_HELD if held else output)
        assert db.query(Chat).filter_by(parent_id=task.chat_id).count() == 1
    assert feedback.call_count == (0 if held else 1)
    transcript = client.get(f"/api/chats/{task.chat_id}?surface=development")
    assert transcript.status_code == 200
    assert (output in transcript.text) == (not held)
    message = next(message for message in transcript.json()["messages"]
                   if message["role"] == "assistant" and message.get("chat_row_id") == task.turn_id)
    assert (message.get("source_reply_eligible") is False) == held
    current = client.get(f"/api/workflows/studio/tasks/{task.chat_id}/execution")
    assert current.json()["last_proactive_result_turn_id"] == task.turn_id
    assert transcript.json()["development"]["last_proactive_result_turn_id"] == task.turn_id
    assert not proactive.dispatch_proactive_candidate(cid)["success"]
    assert len(tasks) == 1


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


def test_worker_waiting_for_review_keeps_the_existing_dispatch(work, monkeypatch):
    from distr.core.db import Chat, ChatTurnEvent
    from distr.core.incoming.source_reply import _reply_response
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow import development_harness as harness

    factory, ids, backend = work
    monkeypatch.setattr(harness, "get_session", lambda: _session_ctx(factory))
    backend.return_value.waits_for_human = True
    cid = candidate_id()
    result = proactive.dispatch_proactive_candidate(cid)
    assert result["outcome_recorded"] is True
    assert result["success"] is False
    assert "paused for human review" in result["spoken_summary"]
    assert proactive.dispatch_proactive_candidate(cid)["success"] is False
    state = harness.development_execution_state(result["chat_id"])
    assert state["waiting_kind"] == "human_review"
    assert state["status"] == "blocked" and not state["can_stop"]
    assert state["turn_id"] == result["turn_id"]
    with _session_ctx(factory) as session:
        root = session.get(Chat, result["chat_id"])
        assert session.get(Chat, result["turn_id"]).response
        assert _reply_response(session, root, result["turn_id"]) is None
        terminal = session.query(ChatTurnEvent).filter_by(turn_id=result["turn_id"], event_type="turn_completed").one()
        assert terminal.status == "waiting"
        terminal.status = "completed"  # Existing waiting approvals used this status.
        session.flush()
        assert _reply_response(session, root, result["turn_id"]) is None
        candidate = session.get(OrchestratorEvent, cid)
        activity = session.query(OrchestratorEvent).filter_by(parent_event_id=cid).one()
        assert candidate.status == activity.status == "waiting"
        assert "waiting for human review" in activity.summary
        session.get(KanbanTicket, ids.ticket_id).title = "Revised while waiting"
    revised = proactive.run_proactive_check()["candidates"][0]
    assert revised["dispatch_status"] == "waiting"
    assert proactive.dispatch_proactive_candidate(revised["candidate_id"])["success"] is False
    backend.assert_called_once()
    assert harness.development_execution_state(result["chat_id"])["waiting_kind"] == "human_review"
    with _session_ctx(factory) as session:
        ticket = session.get(KanbanTicket, ids.ticket_id)
        linked_project = ticket.linked_project_id
        ticket.linked_project_id = ids.project_id + 100
    assert harness.development_execution_state(result["chat_id"]).get("waiting_kind") != "human_review"
    with _session_ctx(factory) as session:
        session.get(KanbanTicket, ids.ticket_id).linked_project_id = linked_project
        session.add(Chat(parent_id=result["chat_id"], input="I reviewed the earlier response."))
    assert harness.development_execution_state(result["chat_id"]).get("waiting_kind") != "human_review"


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
