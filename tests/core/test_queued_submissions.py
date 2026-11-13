import json
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tests.core.test_development_control import development_db
from tests.core.test_development_harness import scoped_direct_ticket  # noqa: F401
from distr.core.workflow import development_control as control, development_harness as harness, queued_submissions as queue
from distr.core.db.workflow import DevelopmentCommand


def submission(db):
    file = db['project_root'] / 'input.txt'
    file.write_text('Transcript and source file content.')
    item = {'name': file.name, 'path': str(file), 'mime_type': 'text/plain', 'size': file.stat().st_size, 'transcript': 'Original attached transcript.'}
    metadata = {'attachments': [item], 'composer': {'text': 'Review this request', 'attachments': [
        {'kind': 'file', 'label': file.name, 'reference': str(file), 'mime_type': 'text/plain', 'transcript': item['transcript']},
        {'kind': 'context', 'label': 'Transcription', 'text': 'A complete transcription. ' * 200}], 'use_playwright': True}}
    return control.enqueue_command(db['chat_id'], 'Review this request\nFocused context', command_type='followup', metadata=metadata)


@pytest.mark.parametrize(("instruction", "complexity", "laya_mode"), [
    ("Fix a typo in the button text.", "low", "off"),
    ("Fix the failing production authentication migration regression.", "high", "off"),
    ("Fix a typo in the button text.", "high", "prefer"),
])
def test_app_view_queue_recovers_receipts_and_starts_only_the_current_head(
    development_db, monkeypatch, instruction, complexity, laya_mode,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from distr.core.db import Chat
    from distr.gui.web.routes.development.thread_controls import register_routes
    from distr.gui.web.routes.development.threads import register_routes as register_thread_routes

    session, chat_id, project_id = (development_db[key] for key in ('session', 'chat_id', 'project_id'))
    for module in (harness, __import__('distr.core.chat', fromlist=['chat']), __import__('distr.core.chat_turns', fromlist=['chat_turns'])):
        monkeypatch.setattr(module, 'get_session', session)
    launched = Event()
    finalized = Event()
    worker_routes = []
    monkeypatch.setattr("distr.core.laya_runtime.get_mode", lambda: laya_mode)
    monkeypatch.setattr("distr.core.laya_runtime.assess_request", lambda _request: {
        "complexity": "high", "confidence": 0.42, "is_sensitive": False,
    })

    from distr.core.turn_runtime import TurnResult

    async def run(request, **kwargs):
        worker_routes.append((request.route.backend_id, request.complexity))
        launched.set()
        return TurnResult(success=False, error="Simulated worker outcome", runtime_id="cli_harness",
                          backend_id=request.route.backend_id, model=request.route.model)

    worker = harness._run_execution
    async def finish(**kwargs):
        try:
            await worker(**kwargs)
        finally:
            finalized.set()

    monkeypatch.setattr(harness, "_run_execution", finish)
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", run)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
                        lambda *a, **k: {"status": {"ready": True}})
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    register_thread_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    path = f'/workflows/studio/tasks/{chat_id}/followups'
    preview = client.post('/workflows/studio/routing-assessment', json={'instruction': instruction}).json()
    assert preview['complexity'] == complexity
    payload = {'content': instruction, 'submission_id': 'a' * 32, 'expected_project_id': project_id}
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda _: client.post(path, json=payload), range(2)))
    assert all(response.status_code == 200 for response in responses)
    first, replay = [response.json() for response in responses]
    assert first['id'] == replay['id']
    assert client.post(path, json={**payload, 'content': 'Different instruction'}).status_code == 409
    assert client.post(path, json={**payload, 'submission_id': 'b' * 32, 'expected_project_id': project_id + 1}).status_code == 409
    second = client.post(path, json={**payload, 'submission_id': 'b' * 32, 'content': 'Review the next item.'}).json()
    body = {'expected_modified_at': first['modified_at']}
    assert client.post(f'{path}/{second["id"]}/start', json={'expected_modified_at': second['modified_at']}).status_code == 409
    assert client.post(f'/workflows/studio/tasks/{chat_id + 1}/followups/{first["id"]}/cancel', json=body).status_code == 404
    harness._update_execution(chat_id, job_id='fixture', status='running')
    assert client.post(f'{path}/{first["id"]}/start', json=body).status_code == 409
    with session() as db:
        assert db.query(Chat).filter(Chat.parent_id == chat_id).count() == 0
    harness._update_execution(chat_id, job_id='fixture', status='completed')
    response = client.post(f'{path}/{first["id"]}/start', json=body)
    assert response.status_code == 200, response.text
    assert response.json().get('starting') or response.json().get('started')
    assert launched.wait(3) and finalized.wait(5)
    assert client.post(f'{path}/{first["id"]}/start', json=body).json()['already_started'] is True
    assert client.post(path, json=payload).json()['status'] == 'delivered'
    assert client.get(f'{path}/{payload["submission_id"]}/receipt').json()['status'] == 'delivered'
    assert len(worker_routes) == 1
    assert worker_routes[0][0] == preview['route']['backend']
    assert worker_routes[0][1] == complexity
    receipt = harness.development_execution_state(chat_id)['route_receipt']
    assert receipt['complexity'] == complexity
    assert receipt['decision_engine'] == preview['decision_engine']
    with session() as db:
        turn, = db.query(Chat).filter(Chat.parent_id == chat_id).all()
        assert turn.id == response.json()['turn_id']
        assert json.loads(turn.params)['development_turn']['queued_command_id'] == first['id']
    assert client.post(f'{path}/{second["id"]}/cancel', json={'expected_modified_at': second['modified_at']}).json()['status'] == 'cancelled'


def test_queue_roundtrip_holds_during_edit_and_preserves_full_context_and_file(development_db, monkeypatch):
    command = submission(development_db)
    metadata = command['metadata']
    assert len(metadata['composer']['attachments'][1]['text']) > 1000
    assert metadata['attachments'][0]['transcript'] == metadata['composer']['attachments'][0]['transcript'] == 'Original attached transcript.'
    assert queue.command_file(command['id'], 0)['path'] == metadata['attachments'][0]['path']
    held = control.update_command(command['id'], editing=True, expected_modified_at=command['modified_at'])
    assert held['status'] == 'editing'
    control.enqueue_command(development_db['chat_id'], 'Later item must wait', command_type='followup')
    monkeypatch.setattr(harness, 'get_session', development_db['session'])
    dispatch = Mock()
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    harness._start_next_queued_instruction(development_db['chat_id'])
    dispatch.assert_not_called()
    metadata['attachments'] = []
    metadata['composer']['attachments'] = [metadata['composer']['attachments'][1]]
    metadata['composer']['text'] = 'Changed text'
    saved = control.update_command(command['id'], content='Changed text\nTranscription retained', metadata=metadata, editing=False, expected_modified_at=held['modified_at'])
    assert saved['status'] == 'queued'
    assert saved['metadata']['attachments'] == []
    assert saved['metadata']['composer']['attachments'][0]['text'] == ('A complete transcription. ' * 200).strip()
    assert (development_db['project_root'] / 'input.txt').exists()
    with pytest.raises(ValueError, match='changed'):
        control.update_command(command['id'], content='Stale', expected_modified_at=held['modified_at'])


def _finish_simulated_direct_worker(development_db, monkeypatch):
    from threading import Event
    from distr.core.turn_runtime import TurnResult

    for module in (harness, __import__("distr.core.chat", fromlist=["chat"]),
                   __import__("distr.core.chat_turns", fromlist=["chat_turns"])):
        monkeypatch.setattr(module, "get_session", development_db["session"])
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
                        lambda *a, **kw: {"status": {"ready": True}})
    finalized = Event()
    run = harness._run_execution

    async def worker(**kwargs):
        try:
            await run(**kwargs)
        finally:
            finalized.set()

    async def execute(request, **kwargs):
        return TurnResult(success=False, error="Simulated outcome", runtime_id="cli_harness",
                          backend_id=request.route.backend_id, model=request.route.model)

    monkeypatch.setattr(harness, "_run_execution", worker)
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", execute)
    return finalized


def test_deferred_queue_is_never_consumed_by_passive_dispatch_and_automatic_turn_receives_files(development_db, monkeypatch):
    command = submission(development_db)
    assert control.dispatch_pending(development_db['chat_id'])['status'] == 'idle'
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    dispatch = Mock(wraps=harness.dispatch_development_prompt)
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    harness._start_next_queued_instruction(development_db['chat_id'])
    assert dispatch.call_args.kwargs['attachments'] == command['metadata']['attachments']
    assert dispatch.call_args.kwargs['use_playwright'] is True
    assert finalized.wait(5)
    assert control.list_commands_for_id(command['id'])['status'] == 'delivered'
    harness._start_next_queued_instruction(development_db['chat_id'])
    dispatch.assert_called_once()


@pytest.mark.parametrize("changed", ["none", "edited", "newer", "project"])
def test_completed_turn_recovers_only_the_saved_queue_item(development_db, monkeypatch, changed):
    from distr.core.db import Chat, ChatTurnEvent
    from distr.core.db.projects import Project

    session, chat_id = development_db["session"], development_db["chat_id"]
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    first = submission(development_db)
    second = control.enqueue_command(chat_id, "Second follow-up", command_type="followup")
    with session() as db:
        root = db.get(Chat, chat_id)
        original = Chat(parent_id=chat_id, project_id=root.project_id, input="Original work")
        db.add(original)
        db.flush()
        turn_id = original.id
        params = json.loads(root.params)
        params["active_turn_chat_row_id"] = turn_id
        params["development"]["execution"] = {"status": "running", "job_id": "original", "turn_id": turn_id}
        root.params = json.dumps(params)
        db.commit()
    state = harness._update_execution(chat_id, job_id="original", status="completed",
                                      turn_response="Original work completed.", queue_after_completion=True)
    assert state["followup_completion_pending"]["command_id"] == first["id"]
    if changed == "edited":
        control.update_command(first["id"], content="Changed follow-up", expected_modified_at=first["modified_at"])
    elif changed in {"newer", "project"}:
        with session() as db:
            root = db.get(Chat, chat_id)
            if changed == "newer":
                db.add(Chat(parent_id=chat_id, project_id=root.project_id, input="Newer instruction"))
            else:
                project = Project(name="Other project")
                db.add(project)
                db.flush()
                root.project_id = project.id
            db.commit()
    dispatch = Mock(wraps=harness.dispatch_development_prompt)
    monkeypatch.setattr(harness, "dispatch_development_prompt", dispatch)
    queue.recover_completed_followups()
    if changed == "none":
        assert finalized.wait(5)
    queue.recover_completed_followups()
    assert dispatch.call_count == (1 if changed == "none" else 0)
    assert control.list_commands_for_id(first["id"])["status"] == ("delivered" if changed == "none" else "queued")
    assert control.list_commands_for_id(second["id"])["status"] == "queued"
    with session() as db:
        assert db.query(ChatTurnEvent).filter_by(chat_id=chat_id, turn_id=turn_id, event_type="turn_completed").count() == (
            0 if changed in {"newer", "project"} else 1
        )


def test_steer_stops_existing_turn_and_delivers_files_once_failure_keeps_item(development_db, monkeypatch):
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    command = submission(development_db)
    active = [True]
    execution_state = harness.development_execution_state
    monkeypatch.setattr(harness, 'development_execution_state', lambda chat_id: {
        "status": "running", "direct": True, "runtime_id": "native", "turn_id": 101,
    } if active[0] else execution_state(chat_id))

    def stop_worker(chat_id, *, expected_turn_id):
        assert expected_turn_id == 101
        active[0] = False
        return {"stop_requested": True}

    stop = Mock(side_effect=stop_worker)
    monkeypatch.setattr(harness, 'stop_development_execution', stop)
    monkeypatch.setattr('distr.core.turn_runtime.active_turn_registered', lambda chat_id: False)
    dispatch = Mock(side_effect=RuntimeError('Unavailable'), wraps=harness.dispatch_development_prompt)
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    with pytest.raises(RuntimeError):
        queue.run_submission(command['id'])
    assert control.list_commands_for_id(command['id'])['status'] == 'queued'
    dispatch.side_effect = None
    assert queue.run_submission(command['id'])['chat_id'] == development_db['chat_id']
    assert dispatch.call_args.kwargs['attachments'] == command['metadata']['attachments']
    assert finalized.wait(5)
    assert queue.run_submission(command['id'])['already_started'] is True
    assert dispatch.call_count == 2
    stop.assert_called_once_with(development_db["chat_id"], expected_turn_id=101)


def test_steer_keeps_followup_queued_when_the_worker_is_still_active(development_db, monkeypatch):
    command = submission(development_db)
    monkeypatch.setattr(harness, "development_execution_state", lambda _: {"status": "running"})
    stop = Mock()
    monkeypatch.setattr(harness, "stop_development_execution", stop)
    monkeypatch.setattr("distr.core.turn_runtime.active_turn_registered", lambda _: False)
    dispatch = Mock()
    monkeypatch.setattr(harness, "dispatch_development_prompt", dispatch)

    with pytest.raises(ValueError, match="remains queued"):
        queue.run_submission(command["id"])

    assert control.list_commands_for_id(command["id"])["status"] == "queued"
    dispatch.assert_not_called()
    stop.assert_not_called()


def test_proactive_marker_keeps_steering_and_followup_queued(development_db, monkeypatch):
    from distr.core.db import Chat

    chat_id = development_db["chat_id"]
    command = submission(development_db)
    steer = control.enqueue_command(chat_id, "Keep the existing layout")
    with development_db["session"]() as db:
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["development"]["proactive_dispatch"] = {"candidate_id": 8, "turn_id": 9}
        root.params = json.dumps(params)
        db.commit()
    interrupt, native = Mock(), Mock()
    monkeypatch.setattr(harness, "stop_development_execution", interrupt)
    monkeypatch.setattr("distr.core.turn_runtime.steer_active_turn", native)
    assert control.dispatch_command(steer["id"])["reason"] == "followup_only"
    with pytest.raises(ValueError, match="stays queued"):
        queue.run_submission(command["id"])
    assert control.list_commands_for_id(command["id"])["status"] == "queued"
    assert control.list_commands_for_id(steer["id"])["status"] == "queued"
    interrupt.assert_not_called()
    native.assert_not_called()


def test_command_endpoint_keeps_followup_queued_and_cancel_preserves_files(development_db, monkeypatch):
    from distr.gui.web.routes.development.thread_controls import register_routes
    monkeypatch.setattr('distr.core.skills.catalog.filter_known_skill_ids', lambda ids: ids)
    monkeypatch.setattr(control, 'is_duplicate_instruction', lambda *args: False)
    dispatch = Mock(side_effect=AssertionError('must not auto-steer'))
    monkeypatch.setattr(control, 'dispatch_command', dispatch)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    response = client.post(f"/workflows/studio/tasks/{development_db['chat_id']}/commands", json={'content': 'Wait here', 'command_type': 'followup', 'metadata': {'composer': {'text': 'Wait here', 'attachments': []}}})
    assert response.status_code == 201, response.text
    assert response.json()['status'] == 'queued'
    dispatch.assert_not_called()
    command = submission(development_db)
    assert client.get(f"/workflows/studio/commands/{command['id']}/attachments/0").text == 'Transcript and source file content.'
    assert client.get(f"/workflows/studio/commands/{command['id']}/attachments/1").status_code == 404
    assert client.patch(f"/workflows/studio/commands/{command['id']}", json={'cancel': True}).json()['status'] == 'cancelled'
    assert (development_db['project_root'] / 'input.txt').is_file()


def test_unscoped_files_are_rejected_before_queueing(development_db, tmp_path):
    unrelated = tmp_path / 'unrelated.txt'
    unrelated.write_text('Private')
    with pytest.raises(ValueError, match='outside this project'):
        control.enqueue_command(development_db['chat_id'], 'Read it', command_type='followup', metadata={'attachments': [{'path': str(unrelated)}]})


def test_app_view_draft_and_saved_followup_keep_the_reviewed_scope(development_db, monkeypatch):
    from distr.core.db import Chat
    from distr.gui.web.routes.development.thread_controls import register_routes

    session, chat_id, project_id = (development_db[key] for key in ('session', 'chat_id', 'project_id'))
    dispatch = Mock(side_effect=AssertionError("A stale follow-up must not start"))
    monkeypatch.setattr(harness, "dispatch_development_prompt", dispatch)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    path = f"/workflows/studio/tasks/{chat_id}/followups"
    previous_scope = [project_id, "", "", None, ""]
    payload = {"content": "Review the original scope.", "submission_id": "a" * 32,
               "expected_project_id": project_id, "expected_scope": previous_scope}
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    original = response.json()

    with session() as db:
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["development"].update(board_key="external-board", board_provider="tensology", board_ticket_key="ticket-b")
        root.params = json.dumps(params)
        db.commit()

    assert client.post(path, json=payload).json()["id"] == original["id"]
    current_scope = [project_id, "external-board", "tensology", None, "ticket-b"]
    assert client.post(path, json={**payload, "expected_scope": current_scope}).status_code == 409
    pending = {**payload, "submission_id": "b" * 32}
    assert client.post(path, json=pending).status_code == 409
    result = client.post(f"{path}/{original['id']}/start", json={"expected_modified_at": original["modified_at"]})
    assert result.status_code == 409
    assert "earlier project or board" in result.json()["detail"]
    for new_thread in [False, True]:
        result = client.post(f"/workflows/studio/commands/{original['id']}/run", json={"new_thread": new_thread})
        assert result.status_code == 409
        assert "earlier project or board" in result.json()["detail"]
    dispatch.assert_not_called()
    assert [row["id"] for row in control.list_commands(chat_id)] == [original["id"]]
    assert control.list_commands_for_id(original["id"])["status"] == "queued"
    with session() as db:
        assert db.query(Chat).count() == 1
    reviewed = client.post(path, json={**pending, "expected_scope": current_scope})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["metadata"]["app_view_scope"] == current_scope


@pytest.mark.parametrize("legacy", [False, True])
def test_followup_receipt_survives_desktop_edit_and_cancellation(development_db, legacy):
    from distr.gui.web.routes.development.thread_controls import register_routes

    chat_id, project_id = (development_db[key] for key in ("chat_id", "project_id"))
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    path = f"/workflows/studio/tasks/{chat_id}/followups"
    submission_id = "c" * 32
    payload = {"content": "Original instruction.", "expected_project_id": project_id, "submission_id": submission_id}
    command = client.post(path, json=payload).json()
    if legacy:
        with development_db["session"]() as db:
            row = db.get(DevelopmentCommand, command["id"])
            metadata = json.loads(row.metadata_json)
            metadata.pop("app_view_submission_digest", None)
            row.metadata_json = json.dumps(metadata)
            db.commit()
    edited = control.update_command(command["id"], content="Edited on desktop.", metadata={})
    replay = client.post(path, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == edited["id"] and replay.json()["content"] == "Edited on desktop."
    assert client.post(path, json={**payload, "content": edited["content"]}).status_code == 409
    control.update_command(command["id"], cancel=True)
    receipt = client.get(f"{path}/{submission_id}/receipt")
    assert receipt.json() == {"id": command["id"], "chat_id": chat_id, "source_ref": submission_id, "status": "cancelled"}
    assert client.get(f"{path}/{'d' * 32}/receipt").status_code == 404
    assert client.get(f"/workflows/studio/tasks/{chat_id + 1}/followups/{submission_id}/receipt").status_code == 404
    assert len(control.list_commands(chat_id)) == 1


def test_followup_retry_checks_thread_identity_even_when_local_ids_match(development_db):
    from datetime import timedelta
    from distr.core.db import Chat
    from distr.core.workflow.development_threads import development_thread_receipt_identity
    from distr.gui.web.routes.development.thread_controls import register_routes

    session, chat_id, project_id = (development_db[key] for key in ("session", "chat_id", "project_id"))
    with session() as db:
        root = db.get(Chat, chat_id)
        original_identity = development_thread_receipt_identity(root)
        root.created_date += timedelta(seconds=1)
        current_identity = development_thread_receipt_identity(root)
        db.commit()
    assert current_identity != original_identity
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    path = f"/workflows/studio/tasks/{chat_id}/followups"
    payload = {"content": "Verify the example.", "expected_project_id": project_id, "submission_id": "e" * 32}
    rejected = client.post(path, json={**payload, "expected_thread_identity": original_identity})
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "followup_thread_changed"
    assert "different Development thread" in rejected.json()["detail"]
    assert control.list_commands(chat_id) == []
    accepted = client.post(path, json={**payload, "expected_thread_identity": current_identity})
    assert accepted.status_code == 200
    assert len(control.list_commands(chat_id)) == 1


@pytest.mark.parametrize("legacy", [False, True, "malformed"])
def test_desktop_queue_keeps_unverified_items_out_of_a_new_project(development_db, monkeypatch, legacy):
    from distr.core.db import Chat
    from distr.core.db.projects import Project
    from distr.gui.web.routes.development.thread_controls import register_routes

    session, chat_id, old_project = (development_db[key] for key in ("session", "chat_id", "project_id"))
    monkeypatch.setattr(harness, "get_session", session)
    dispatch = Mock(side_effect=AssertionError("Old work cannot start in the new project"))
    monkeypatch.setattr(harness, "dispatch_development_prompt", dispatch)
    command = control.enqueue_command(chat_id, "Check the old project.", command_type="followup")
    with session() as db:
        if legacy:
            db.get(DevelopmentCommand, command["id"]).metadata_json = "[]" if legacy == "malformed" else "{}"
        second = Project(name="Other project")
        db.add(second)
        db.flush()
        new_project = second.id
        db.commit()
    control.update_thread_controls(chat_id, project_id=new_project)

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    api = TestClient(app)
    path = f"/workflows/studio/tasks/{chat_id}/commands"
    stale = api.post(path, json={
        "content": "I wrote this before the move.", "command_type": "followup",
        "expected_scope": [old_project, "", "", None, ""],
    })
    assert stale.status_code == 409, stale.text
    assert not control.is_duplicate_instruction(chat_id, command["content"])
    command = control.list_commands_for_id(command["id"])
    run = f"/workflows/studio/commands/{command['id']}/run"
    for new_thread in [False, True]:
        response = api.post(run, json={"new_thread": new_thread, "expected_modified_at": command["modified_at"]})
        assert response.status_code == 409, response.text
        detail = "could not be verified" if legacy else "earlier project or board"
        assert detail in response.json()["detail"]
    dispatch.assert_not_called()
    with session() as db:
        assert db.query(Chat).count() == 1
        assert db.get(Chat, chat_id).project_id != old_project
    current = control.enqueue_command(chat_id, "Check the new project.", command_type="followup")
    stale_duplicate = api.post(path, json={
        "content": current["content"], "command_type": "followup",
        "expected_scope": [old_project, "", "", None, ""],
    })
    assert stale_duplicate.status_code == 409, stale_duplicate.text
    rows = api.get(path, params={"include_terminal": False}).json()["items"]
    assert [(row["id"], row["scope_status"]) for row in rows] == [
        (current["id"], "current"), (command["id"], "unknown" if legacy else "earlier"),
    ]
    edited = api.patch(f"/workflows/studio/commands/{command['id']}", json={
        "content": "Check the old project again.",
        "metadata": {"queued_scope": [new_project, "", "", None, ""]},
        "expected_modified_at": command["modified_at"],
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["scope_status"] == ("unknown" if legacy else "earlier")
    control.update_thread_controls(chat_id, project_id=old_project)
    restored = {row["id"]: row["scope_status"] for row in control.list_commands(chat_id, include_terminal=False)}
    assert restored == {command["id"]: "unknown" if legacy else "current", current["id"]: "earlier"}


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("surface", ["app_view", "web"])
def test_current_followups_advance_past_held_scope_in_order(development_db, monkeypatch, automatic, surface):
    from threading import Event
    from distr.core.db import Chat

    session, chat_id, project_id = (development_db[key] for key in ('session', 'chat_id', 'project_id'))
    monkeypatch.setattr(harness, "get_session", session)
    monkeypatch.setattr("distr.core.chat.get_session", session)
    monkeypatch.setattr("distr.core.chat_turns.get_session", session)
    launched = Event()
    finalized = Event()
    calls = []

    from distr.core.turn_runtime import TurnResult

    async def run(request, **kwargs):
        calls.append(request.instruction)
        launched.set()
        return TurnResult(success=False, error="Simulated worker outcome", runtime_id="cli_harness",
                          backend_id=request.route.backend_id, model=request.route.model)

    worker = harness._run_execution
    async def finish(**kwargs):
        try:
            await worker(**kwargs)
        finally:
            finalized.set()

    monkeypatch.setattr(harness, "_run_execution", finish)
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", run)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
                        lambda *a, **k: {"status": {"ready": True}})

    def submit(content, ref):
        return control.enqueue_command(
            chat_id, content, source=surface, source_ref=ref * 32, command_type="followup",
            idempotent=surface == "app_view", expected_project_id=project_id,
        )

    held = submit("Use the previous board.", "a")
    with session() as db:
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["development"].update(board_key="reassigned", board_provider="tensology")
        root.params = json.dumps(params)
        db.commit()
    first = submit("Check the new board first.", "b")
    second = submit("Check the new board second.", "c")
    assert [row["id"] for row in control.list_commands(chat_id, include_terminal=False, limit=1)] == [first["id"]]
    assert [row["id"] for row in control.list_commands(chat_id, include_terminal=False)] == [
        first["id"], second["id"], held["id"],
    ]
    control.update_command(first["id"], editing=True, expected_modified_at=first["modified_at"])
    with pytest.raises(ValueError, match="next queued item changed"):
        queue.start_next_followup(chat_id, second["id"], second["modified_at"])
    harness._start_next_queued_instruction(chat_id)
    assert calls == []
    assert control.list_commands_for_id(first["id"])["status"] == "editing"
    first = control.update_command(first["id"], editing=False)
    if automatic:
        harness._start_next_queued_instruction(chat_id)
    else:
        receipt = queue.start_next_followup(chat_id, first["id"], first["modified_at"])
        assert receipt.get("starting") or receipt.get("started")
    assert launched.wait(3) and finalized.wait(5)
    assert len(calls) == 1 and first["content"] in calls[0]
    assert [row["status"] for row in control.list_commands(chat_id)] == ["queued", "delivered", "queued"]
    with session() as db:
        assert [row.input for row in db.query(Chat).filter_by(parent_id=chat_id).all()] == [first["content"]]


def test_new_thread_retains_project_model_files_and_retry_identity(development_db, monkeypatch):
    from distr.core.db import Chat
    from distr.core.db.workflow import DevelopmentWorkItem
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    monkeypatch.setattr(harness, 'development_execution_active', lambda chat_id: False)
    with development_db['session']() as db:
        root = db.get(Chat, development_db['chat_id'])
        root.provider, root.model_name = 'ollama', 'local-model'
        root.params = json.dumps({'development': {'model_route': {'route_mode': 'manual', 'backend': 'pi', 'provider': 'ollama', 'model_name': 'local-model'}, 'permission_profile': {'mode': 'careful'}}})
        db.commit()
    command = submission(development_db)
    dispatch = Mock(side_effect=RuntimeError('Temporarily unavailable'), wraps=harness.dispatch_development_prompt)
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    with pytest.raises(RuntimeError):
        queue.run_submission(command['id'], new_thread=True)
    target = control.list_commands_for_id(command['id'])['metadata']['started_chat_id']
    dispatch.side_effect = None
    assert queue.run_submission(command['id'], new_thread=True)['chat_id'] == target
    assert finalized.wait(5)
    with development_db['session']() as db:
        fork = db.get(Chat, target)
        assert target != development_db['chat_id']
        assert fork.project_id == development_db['project_id']
        assert (fork.provider, fork.model_name) == ('ollama', 'local-model')
        assert db.query(Chat).filter_by(parent_id=target).one().input == command['content']
        assert json.loads(fork.params)['development']['model_route']['backend'] == 'pi'
        assert json.loads(fork.params)['development']['permission_profile'] == {'mode': 'careful'}
        assert db.query(DevelopmentWorkItem).filter_by(source_type='queued_instruction').count() == 1
    assert dispatch.call_args.kwargs['attachments'] == command['metadata']['attachments']
    assert dispatch.call_args.kwargs['use_playwright'] is True


def test_failed_automatic_delivery_retries_without_duplicating_user_message(development_db, monkeypatch):
    from distr.core.db import Chat
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    command = submission(development_db)
    dispatch = Mock(side_effect=RuntimeError('Worker unavailable'), wraps=harness.dispatch_development_prompt)
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    with pytest.raises(RuntimeError):
        harness._start_next_queued_instruction(development_db['chat_id'])
    assert control.list_commands_for_id(command['id'])['status'] == 'queued'
    with development_db['session']() as db:
        assert db.query(Chat).filter_by(parent_id=development_db['chat_id']).count() == 0
    dispatch.side_effect = None
    harness._start_next_queued_instruction(development_db['chat_id'])
    assert finalized.wait(5)
    with development_db['session']() as db:
        assert db.query(Chat).filter_by(parent_id=development_db['chat_id']).count() == 1
    assert control.list_commands_for_id(command['id'])['status'] == 'delivered'


def test_steer_cancels_active_workflow_before_dispatching_with_files(development_db, monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    with development_db['session']() as db:
        workflow = AutoWorkflow(name='Active workflow')
        db.add(workflow)
        db.flush()
        db.add(AutoWorkflowRun(workflow_id=workflow.id, chat_id=development_db['chat_id'], status='running'))
        db.commit()
    monkeypatch.setattr(harness, 'development_execution_active', lambda chat_id: False)
    monkeypatch.setattr('distr.core.turn_runtime.active_turn_registered', lambda chat_id: False)
    events = []
    def stop(chat_id, *, expected_workflow_run_id):
        events.append('stop')
        with development_db['session']() as db:
            run = db.query(AutoWorkflowRun).filter_by(chat_id=chat_id).one()
            assert run.id == expected_workflow_run_id
            run.status = 'cancelled'
            db.commit()
        return {"stop_requested": True}

    dispatch = harness.dispatch_development_prompt

    def start(*args, **kwargs):
        events.append(('dispatch', kwargs['attachments']))
        return dispatch(*args, **kwargs)

    monkeypatch.setattr(harness, 'stop_development_execution', stop)
    monkeypatch.setattr(harness, 'dispatch_development_prompt', start)
    command = submission(development_db)
    queue.run_submission(command['id'])
    assert finalized.wait(5)
    assert events == ['stop', ('dispatch', command['metadata']['attachments'])]


@pytest.mark.parametrize("failure", ["moved", "unready", "restart", "runtime"])
def test_claimed_followup_retains_one_receipt_and_recovers_only_before_runtime(
    scoped_direct_ticket, monkeypatch, failure,
):
    from threading import Event
    from distr.core.db import Chat
    from distr.core.turn_runtime import TurnResult

    scope = scoped_direct_ticket
    monkeypatch.setattr(control, "get_session", scope.session)
    queued = control.enqueue_command(
        scope.chat_id, "Review the queued evidence", source="app_view", source_ref="c" * 32,
        command_type="followup", idempotent=True, expected_project_id=scope.project_id,
    )
    probing, release, finished = Event(), Event(), Event()
    actual_worker = harness._run_execution
    runtime_calls = []
    retrying = False

    def probe(*args, **kwargs):
        if retrying:
            return {"status": {"ready": True}}
        probing.set()
        assert release.wait(10)
        return {"status": {"ready": failure != "unready"}, "next_step": "Fixture worker unavailable"}

    async def run(**kwargs):
        try:
            await actual_worker(**kwargs)
        finally:
            finished.set()

    async def execute(request, **kwargs):
        runtime_calls.append(request.scope.chat_id)
        assert control.list_commands_for_id(queued["id"])["status"] == "delivered"
        return TurnResult(success=retrying, backend_id="pi", runtime_id="cli_harness",
                          error="" if retrying else "Worker returned an unconfirmed failure.")

    monkeypatch.setattr(harness, "_run_execution", run)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", probe)
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", execute)
    try:
        starting = queue.start_next_followup(scope.chat_id, queued["id"], queued["modified_at"])
        assert probing.wait(5)
        assert control.list_commands_for_id(queued["id"])["status"] == "dispatching"
        assert starting["starting"] is True
        replay = queue.start_next_followup(scope.chat_id, queued["id"], queued["modified_at"])
        assert replay["starting"] is True and replay["turn_id"] == starting["turn_id"]
        if failure == "moved":
            scope.move("board")
        elif failure == "restart":
            monkeypatch.setattr(harness, "_FOLLOWUP_STARTUP_PROCESS", "restarted")
            assert harness.development_execution_state(scope.chat_id)["status"] == "failed"
    finally:
        release.set()
        assert finished.wait(5)

    saved = control.list_commands_for_id(queued["id"])
    if failure == "runtime":
        assert saved["status"] == "delivered"
        assert runtime_calls == [scope.chat_id]
        assert queue.start_next_followup(scope.chat_id, queued["id"], queued["modified_at"])["already_started"]
        return
    assert saved["status"] == "queued" and saved["delivered_at"] is None
    assert saved["content"] == queued["content"]
    assert "remains queued" in saved["result_summary"]
    assert runtime_calls == []
    if failure == "moved":
        with scope.session() as db:
            from distr.core.db.kanban import KanbanTicket
            db.get(KanbanTicket, scope.ticket_id).lane_id = scope.lane_id
            db.commit()

    harness._start_next_queued_instruction(scope.chat_id)
    assert runtime_calls == []
    retrying = True
    finished.clear()
    queue.start_next_followup(scope.chat_id, saved["id"], saved["modified_at"])
    assert finished.wait(5)
    assert runtime_calls == [scope.chat_id]
    assert control.list_commands_for_id(queued["id"])["status"] == "delivered"
    with scope.session() as db:
        assert [row.input for row in db.query(Chat).filter_by(parent_id=scope.chat_id).order_by(Chat.id)] == [
            "Implement the feature", queued["content"], queued["content"],
        ]


def test_followup_recovers_when_turn_activity_cannot_start(development_db, monkeypatch):
    from distr.core.db import Chat

    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    item = submission(development_db)
    activity = Mock(side_effect=RuntimeError("Could not write activity"))
    monkeypatch.setattr("distr.core.chat_turns.start_tool", activity)

    with pytest.raises(harness.DevelopmentExecutionBusy, match="Could not start"):
        queue.start_next_followup(item["chat_id"], item["id"], item["modified_at"])
    saved = control.list_commands_for_id(item["id"])
    assert saved["status"] == "queued" and saved["delivered_at"] is None
    assert saved["metadata"]["attachments"] == item["metadata"]["attachments"]
    assert saved["position"] == item["position"]
    assert not finalized.is_set()
    with development_db["session"]() as db:
        turn = db.query(Chat).filter_by(parent_id=item["chat_id"]).one()
        assert turn.response == "Could not start the Development worker."


@pytest.mark.parametrize("new_thread", [False, True])
def test_desktop_submission_returns_same_item_after_preflight_rejection(development_db, monkeypatch, new_thread):
    from threading import Event

    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    item = submission(development_db)
    probing, release = Event(), Event()
    retry = False

    def probe(*args, **kwargs):
        if not retry:
            probing.set()
            assert release.wait(5)
        return {"status": {"ready": retry}, "next_step": "Fixture unavailable"}

    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", probe)
    try:
        receipt = queue.run_submission(item["id"], new_thread=new_thread)
        assert probing.wait(5)
        assert control.list_commands_for_id(item["id"])["status"] == "dispatching"
        assert receipt["starting"] is True
        same = queue.run_submission(item["id"], new_thread=new_thread)
        assert same["chat_id"] == receipt["chat_id"] and same["starting"]
    finally:
        release.set()
        assert finalized.wait(5)

    saved = control.list_commands_for_id(item["id"])
    assert saved["status"] == "queued" and saved["delivered_at"] is None
    assert saved["position"] == item["position"]
    assert saved["metadata"]["attachments"] == item["metadata"]["attachments"]
    retry = True
    finalized.clear()
    resumed = queue.run_submission(item["id"], new_thread=new_thread)
    assert resumed["chat_id"] == receipt["chat_id"]
    assert finalized.wait(5)
    assert control.list_commands_for_id(item["id"])["status"] == "delivered"
    assert queue.run_submission(item["id"], new_thread=new_thread)["already_started"] is True


@pytest.mark.parametrize("change", ["moved", "restart"])
def test_reserved_fork_stops_if_source_scope_or_process_changes(scoped_direct_ticket, monkeypatch, change):
    from threading import Event

    scope = scoped_direct_ticket
    monkeypatch.setattr(control, "get_session", scope.session)
    root = {"session": scope.session}
    finalized = _finish_simulated_direct_worker(root, monkeypatch)
    item = control.enqueue_command(scope.chat_id, "Review the proposal", command_type="followup")
    probing, release = Event(), Event()
    launched = Mock()
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", launched)

    def probe(*args, **kwargs):
        probing.set()
        assert release.wait(8)
        return {"status": {"ready": True}}

    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", probe)
    receipt = queue.run_submission(item["id"], new_thread=True)
    target = receipt["chat_id"]
    try:
        assert probing.wait(5)
        if change == "moved":
            scope.move("board")
        else:
            monkeypatch.setattr(harness, "_FOLLOWUP_STARTUP_PROCESS", "restarted")
            assert control.list_commands(scope.chat_id)[0]["status"] == "queued"
            assert harness.development_execution_state(target)["status"] == "failed"
    finally:
        release.set()
        assert finalized.wait(5)
    saved = control.list_commands_for_id(item["id"])
    assert saved["status"] == "queued" and saved["metadata"]["started_chat_id"] == target
    launched.assert_not_called()
    if change == "moved":
        with pytest.raises(ValueError, match="changed before work started|reserved new thread"):
            queue.run_submission(item["id"], new_thread=True)


@pytest.mark.parametrize("phase", ["reviewed", "preparing", "after_stop"])
def test_steer_never_cancels_a_replacement_worker_during_preparation(development_db, monkeypatch, phase):
    from distr.core import turn_runtime
    from distr.core.project_cli_backends import registry

    chat_id = development_db["chat_id"]
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    item = submission(development_db)
    active = {"direct": True, "status": "running", "runtime_id": "native", "turn_id": 101}
    cancelled = []
    metadata = queue.submission_metadata

    def prepare(*args, **kwargs):
        result = metadata(*args, **kwargs)
        if phase == "preparing":
            active["turn_id"] = 202
        return result

    def cancel(target, *, expected_turn_id=None):
        assert target == chat_id
        if expected_turn_id is None or expected_turn_id == active["turn_id"]:
            cancelled.append(active["turn_id"])
            if phase == "after_stop":
                active["turn_id"] = 202
            return True
        return False

    monkeypatch.setattr(queue, "submission_metadata", prepare)
    monkeypatch.setattr(harness, "development_execution_state", lambda _: dict(active))
    monkeypatch.setattr(turn_runtime, "cancel_active_turn", cancel)
    monkeypatch.setattr(turn_runtime, "active_turn_registered", lambda _: False)
    monkeypatch.setattr(registry, "terminate_backend_process", Mock())
    reviewed = {"expected_stop": {"turn_id": 101}} if phase == "reviewed" else {}
    if phase == "reviewed":
        active["turn_id"] = 202
    with pytest.raises(ValueError, match="changed|replacement run"):
        queue.run_submission(item["id"], **reviewed)
    assert cancelled == ([101] if phase == "after_stop" else [])
    assert control.list_commands_for_id(item["id"])["status"] == "queued"
    assert not finalized.is_set()
