import json
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tests.core.test_development_control import development_db
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


def test_deferred_queue_is_never_consumed_by_passive_dispatch_and_automatic_turn_receives_files(development_db, monkeypatch):
    command = submission(development_db)
    assert control.dispatch_pending(development_db['chat_id'])['status'] == 'idle'
    monkeypatch.setattr(harness, 'get_session', development_db['session'])
    monkeypatch.setattr('distr.core.chat.ChatService.add_user_message', lambda *args, **kwargs: None)
    dispatch = Mock()
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    harness._start_next_queued_instruction(development_db['chat_id'])
    assert dispatch.call_args.kwargs['attachments'] == command['metadata']['attachments']
    assert dispatch.call_args.kwargs['use_playwright'] is True
    assert control.list_commands_for_id(command['id'])['status'] == 'delivered'
    harness._start_next_queued_instruction(development_db['chat_id'])
    dispatch.assert_called_once()


def test_steer_stops_existing_turn_and_delivers_files_once_failure_keeps_item(development_db, monkeypatch):
    command = submission(development_db)
    active = [True]
    monkeypatch.setattr(harness, 'development_execution_active', lambda chat_id: active[0])
    stop = Mock(side_effect=lambda chat_id: active.__setitem__(0, False))
    monkeypatch.setattr(harness, 'stop_development_execution', stop)
    monkeypatch.setattr('distr.core.turn_runtime.active_turn_registered', lambda chat_id: False)
    monkeypatch.setattr('distr.core.chat.ChatService.add_user_message', lambda *args, **kwargs: None)
    dispatch = Mock(side_effect=RuntimeError('Unavailable'))
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    with pytest.raises(RuntimeError):
        queue.run_submission(command['id'])
    assert control.list_commands_for_id(command['id'])['status'] == 'queued'
    dispatch.side_effect = None
    assert queue.run_submission(command['id'])['chat_id'] == development_db['chat_id']
    assert dispatch.call_args.kwargs['attachments'] == command['metadata']['attachments']
    assert queue.run_submission(command['id'])['already_started'] is True
    assert dispatch.call_count == 2
    stop.assert_called_once()


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


def test_new_thread_retains_project_model_files_and_retry_identity(development_db, monkeypatch):
    from distr.core.db import Chat
    from distr.core.workflow import development_threads
    from distr.core.db.workflow import DevelopmentWorkItem
    monkeypatch.setattr(development_threads, 'get_session', development_db['session'])
    monkeypatch.setattr('distr.core.chat_turns.get_session', development_db['session'])
    monkeypatch.setattr('distr.core.chat.record_chat_audit_event', Mock())
    monkeypatch.setattr(harness, 'development_execution_active', lambda chat_id: False)
    with development_db['session']() as db:
        root = db.get(Chat, development_db['chat_id'])
        root.provider, root.model_name = 'ollama', 'local-model'
        root.params = json.dumps({'development': {'model_route': {'route_mode': 'manual', 'backend': 'pi', 'provider': 'ollama', 'model_name': 'local-model'}, 'permission_profile': {'mode': 'careful'}}})
        db.commit()
    command = submission(development_db)
    dispatch = Mock(side_effect=RuntimeError('Temporarily unavailable'))
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    with pytest.raises(RuntimeError):
        queue.run_submission(command['id'], new_thread=True)
    target = control.list_commands_for_id(command['id'])['metadata']['started_chat_id']
    dispatch.side_effect = None
    assert queue.run_submission(command['id'], new_thread=True)['chat_id'] == target
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
    monkeypatch.setattr(harness, 'get_session', development_db['session'])
    monkeypatch.setattr('distr.core.chat.get_session', development_db['session'])
    monkeypatch.setattr('distr.core.chat_turns.get_session', development_db['session'])
    monkeypatch.setattr('distr.core.chat.record_chat_audit_event', Mock())
    command = submission(development_db)
    dispatch = Mock(side_effect=RuntimeError('Worker unavailable'))
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    with pytest.raises(RuntimeError):
        harness._start_next_queued_instruction(development_db['chat_id'])
    assert control.list_commands_for_id(command['id'])['status'] == 'queued'
    dispatch.side_effect = None
    harness._start_next_queued_instruction(development_db['chat_id'])
    with development_db['session']() as db:
        assert db.query(Chat).filter_by(parent_id=development_db['chat_id']).count() == 1
    assert control.list_commands_for_id(command['id'])['status'] == 'delivered'


def test_steer_cancels_active_workflow_before_dispatching_with_files(development_db, monkeypatch):
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
    with development_db['session']() as db:
        workflow = AutoWorkflow(name='Active workflow')
        db.add(workflow)
        db.flush()
        db.add(AutoWorkflowRun(workflow_id=workflow.id, chat_id=development_db['chat_id'], status='running'))
        db.commit()
    monkeypatch.setattr(harness, 'development_execution_active', lambda chat_id: False)
    monkeypatch.setattr('distr.core.turn_runtime.active_turn_registered', lambda chat_id: False)
    monkeypatch.setattr('distr.core.chat.ChatService.add_user_message', lambda *args, **kwargs: None)
    events = []
    monkeypatch.setattr(harness, 'stop_development_execution', lambda chat_id: events.append('stop'))
    monkeypatch.setattr(harness, 'dispatch_development_prompt', lambda *args, **kwargs: events.append(('dispatch', kwargs['attachments'])))
    command = submission(development_db)
    queue.run_submission(command['id'])
    assert events == ['stop', ('dispatch', command['metadata']['attachments'])]
