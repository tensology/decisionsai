"""Incoming handoff prepares readable transcripts before the first engine turn."""
from pathlib import Path
from unittest.mock import Mock
import io
import json

import pytest

from tests.core.test_incoming_media import store
from distr.core.incoming import media, proposals, transcription


@pytest.mark.parametrize('surface', ['decisions', 'tensology'])
def test_both_handoff_prompts_keep_full_retained_conversation_and_transcript(surface):
    from distr.core.tensology_client import _execution_prompt

    transcript = 'Voice detail. ' * 1500 + 'Final billing instruction.'
    task = {'title': 'Update billing', 'description': 'Handle allowance minus two.', 'acceptance_criteria': 'Verify the sync button.'}
    pack = {'title': 'Billing change', 'description': 'Original brief', 'tasks': [task], 'source_task_details': [task],
            'evidence': [{'id': 'client-request', 'direction': 'inbound', 'body': 'Please update billing.',
                          'sender_name': 'Client', 'sent_at': '2026-11-10T10:00:00Z'},
                         {'id': 'voice-note', 'direction': 'inbound', 'body': transcript, 'transcript': transcript},
                         {'id': 'credential', 'body': 'password: FixturePassword42'}],
            'context_evidence': [{'id': 'developer-reply', 'direction': 'outbound',
                                  'body': 'Which allowance value should I use?'}]}
    if surface == 'decisions':
        prompt = proposals._proposal_prompt(pack)
    else:
        prompt = _execution_prompt({'reference': 'TEST-1', 'incoming_proposal': pack}, 'low')
    assert 'Please update billing.' in prompt
    assert 'Which allowance value should I use?' in prompt
    assert transcript in prompt
    assert 'developer-reply' in prompt and 'outbound' in prompt
    assert 'Handle allowance minus two.' in prompt and 'Verify the sync button.' in prompt
    assert 'FixturePassword42' not in prompt
    assert 'credential' in prompt


@pytest.fixture(autouse=True)
def linked_media_sources(monkeypatch):
    from distr.core.incoming import project_links
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kwargs: {
        ('gmail', 'first-turn'): {7}, ('gmail', 'first'): {7}, ('gmail', 'second'): {7},
    })


def _original(tmp_path, number, *, transcript=''):
    path = tmp_path / f'.decisions/workspaces/projects/7/attachments/source-{number}.mp4'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f'original media {number}'.encode())
    return media._record(path, path.name, 'video/mp4', str(number), transcript=transcript)


def _handoff(store, monkeypatch, records):
    sessions, chat_id = store
    from distr.core import db
    from distr.core.db.projects import Project
    from distr.core.workflow import development_harness, development_threads

    monkeypatch.setattr(db, 'get_session', sessions)
    with sessions() as session:
        session.add(Project(id=7, name='Media project'))
    monkeypatch.setattr(proposals, 'apply_project_corrections', lambda rows: rows)
    monkeypatch.setattr(proposals, '_existing_proposal_thread', lambda *args: None)
    monkeypatch.setattr(proposals, '_record_proposal_handoff', lambda *args, **kwargs: None)
    monkeypatch.setattr(proposals, 'drop_cached_proposals', lambda **kwargs: None)
    monkeypatch.setattr(development_threads, 'ensure_development_thread', lambda **kwargs: chat_id)
    monkeypatch.setattr('distr.core.incoming.source_context.register_source_context', lambda *args: None)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kwargs: None)
    monkeypatch.setattr(media, 'ensure_proposal_attachments', lambda *args, **kwargs: {'attachments': records, 'failures': []})
    monkeypatch.setattr(media, 'sync_thread_files_to_tickets', lambda chat_id: {})
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda settings: {})
    monkeypatch.setattr(development_harness, 'development_execution_active', lambda chat_id: False)
    dispatched = []

    def dispatch(chat_id, prompt, **kwargs):
        # This is the same collection and scope filter the real dispatch uses.
        inputs = development_harness._safe_turn_attachments(media.required_thread_attachments(chat_id))
        dispatched.append(inputs)
        return {'status': 'running'}

    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', dispatch)
    proposal = {'id': 'local:first-turn', 'source': 'gmail', 'source_thread_id': 'first-turn', 'project_id': 7, 'title': 'Review the media'}
    return chat_id, proposal, dispatched


def test_first_dispatch_contains_both_originals_and_generated_and_existing_transcripts(store, tmp_path, monkeypatch):
    records = [_original(tmp_path, 1), _original(tmp_path, 2, transcript='Existing source transcript.')]
    image = tmp_path / '.decisions/workspaces/projects/7/attachments/screenshot.png'
    image.write_bytes(b'original image fixture')
    records.append(media._record(image, image.name, 'image/png', 'image'))
    chat_id, proposal, dispatched = _handoff(store, monkeypatch, records)
    proposal['evidence'] = [{'id': 'context-reply', 'direction': 'outbound', 'body': 'Use the new billing allowance.'}]
    from distr.core.workflow import development_harness
    dispatch = development_harness.dispatch_development_prompt
    def inspect_dispatch(cid, prompt, **kwargs):
        assert 'Use the new billing allowance.' in prompt
        return dispatch(cid, prompt, **kwargs)
    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', inspect_dispatch)
    transcribe = Mock(return_value='Generated before dispatch.')
    monkeypatch.setattr(transcription, '_transcribe', transcribe)
    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id
    assert len(dispatched) == 1
    originals = [item for item in dispatched[0] if item['mime_type'] == 'video/mp4']
    companions = [item for item in dispatched[0] if item['mime_type'] == 'text/plain']
    assert {Path(item['path']).read_bytes() for item in originals} == {b'original media 1', b'original media 2'}
    assert [Path(item['path']).read_bytes() for item in dispatched[0] if item['mime_type'] == 'image/png'] == [b'original image fixture']
    assert {Path(item['path']).read_text() for item in companions} == {'Generated before dispatch.', 'Existing source transcript.'}
    assert {item['name'] for item in companions} == {'source-1.mp4.transcript.txt', 'source-2.mp4.transcript.txt'}
    transcribe.assert_called_once()


def test_failed_transcription_still_submits_original_and_continues_other_files(store, tmp_path, monkeypatch):
    records = [_original(tmp_path, 1), _original(tmp_path, 2)]
    chat_id, proposal, dispatched = _handoff(store, monkeypatch, records)
    monkeypatch.setattr(transcription, '_transcribe', Mock(side_effect=[RuntimeError('Decoder unavailable'), 'Second file transcript.']))
    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id
    assert len([item for item in dispatched[0] if item['mime_type'] == 'video/mp4']) == 2
    companions = [item for item in dispatched[0] if item['mime_type'] == 'text/plain']
    assert [Path(item['path']).read_text() for item in companions] == ['Second file transcript.']


def test_create_without_start_also_prepares_transcripts_before_returning(store, tmp_path, monkeypatch):
    records = [_original(tmp_path, 1)]
    chat_id, proposal, dispatched = _handoff(store, monkeypatch, records)
    monkeypatch.setattr(transcription, '_transcribe', Mock(return_value='Ready when thread opens.'))
    assert proposals.create_proposal_thread(proposal, start_work=False) == chat_id
    assert media.public_thread_attachments(chat_id)[0]['transcript'] == 'Ready when thread opens.'
    assert dispatched == []


def test_merge_retains_both_files_and_transcripts_in_thread_and_returned_proposal(store, tmp_path, monkeypatch):
    _, chat_id = store
    records = [_original(tmp_path, 1, transcript='First source transcript.'), _original(tmp_path, 2)]
    monkeypatch.setattr(media, 'ensure_proposal_attachments', lambda proposal, *args, **kwargs: {'attachments': [records[proposal['file_index']]], 'failures': []})
    monkeypatch.setattr(media, 'sync_thread_files_to_tickets', lambda chat_id: {})
    monkeypatch.setattr(proposals, 'apply_project_corrections', lambda rows: rows)
    monkeypatch.setattr(proposals, '_persist_proposal_dismissal', lambda proposal: proposal['id'])
    monkeypatch.setattr(proposals, 'drop_cached_proposals', lambda **kwargs: None)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {})
    monkeypatch.setattr('distr.core.chat.ChatService.create_new_chat', lambda **kwargs: (chat_id, None))
    monkeypatch.setattr('distr.core.tensology_notify.notify_proposal_created', lambda *args: None)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kwargs: None)
    transcribe = Mock(return_value='Second generated transcript.')
    monkeypatch.setattr(transcription, '_transcribe', transcribe)
    first = {'id': 'local:first', 'source': 'gmail', 'source_thread_id': 'first', 'file_index': 0, 'project_id': 7, 'title': 'First'}
    second = {'id': 'local:second', 'source': 'gmail', 'source_thread_id': 'second', 'file_index': 1, 'project_id': 7, 'title': 'Second'}
    result = proposals.merge_proposals(first, second, complete=lambda *args: '{}')
    assert [item['transcript'] for item in result['merged']['attachments']] == ['First source transcript.', 'Second generated transcript.']
    inputs = media.required_thread_attachments(chat_id)
    assert len(inputs) == 4
    assert {Path(item['path']).read_bytes() for item in inputs[:2]} == {b'original media 1', b'original media 2'}
    assert {Path(item['path']).read_text() for item in inputs[2:]} == {'First source transcript.', 'Second generated transcript.'}
    transcribe.assert_called_once()


@pytest.mark.parametrize('decoder_fails', [False, True])
def test_tensology_task_endpoint_prepares_media_before_dispatch_and_reuses_supplied_transcript(store, tmp_path, monkeypatch, decoder_fails):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.threads import register_routes
    from distr.core.workflow import development_threads, development_control
    from distr.core import tensology_client

    records = [_original(tmp_path, 1, transcript='Preserved upstream transcript.'), _original(tmp_path, 2)]
    chat_id, _, dispatched = _handoff(store, monkeypatch, records)
    monkeypatch.setattr('distr.core.chat.ChatService.create_new_chat', lambda **kwargs: (chat_id, None))
    monkeypatch.setattr(development_threads, 'mark_development_thread', lambda *args, **kwargs: None)
    monkeypatch.setattr(development_threads, 'update_development_model_route', lambda *args, **kwargs: None)
    monkeypatch.setattr(development_control, 'update_thread_controls', lambda *args, **kwargs: None)
    monkeypatch.setattr(development_control, 'resume_thread_time', lambda *args, **kwargs: None)
    decoder = Mock(side_effect=RuntimeError('Decoder unavailable')) if decoder_fails else Mock(return_value='Prepared in task endpoint.')
    monkeypatch.setattr(transcription, '_transcribe', decoder)
    ticket = {'attachments': [{'name': row['name'], 'mime_type': row['mime_type'], 'local_path': row['path'], 'transcript': row['transcript']} for row in records],
              'incoming_proposal': {'evidence': [{'id': 'reply', 'direction': 'outbound', 'body': 'Use the new billing allowance.'}]}}
    from distr.core.workflow import development_harness
    registered = []
    monkeypatch.setattr('distr.core.incoming.source_context.register_source_context', lambda cid, context: registered.append((cid, context)))
    dispatch = development_harness.dispatch_development_prompt
    def inspect_dispatch(cid, prompt, **kwargs):
        assert 'Use the new billing allowance.' in prompt
        assert registered == [(chat_id, {'source': 'whatsapp', 'source_thread_id': 'scoped-contact', 'message_ids': ['client-source'], 'project_id': 7})]
        return dispatch(cid, prompt, **kwargs)
    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', inspect_dispatch)
    attachments = tensology_client._tensology_thread_files(ticket)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    response = TestClient(app).post('/workflows/studio/tasks', json={'prompt': tensology_client._execution_prompt(ticket, 'low'), 'project_id': 7, 'attachments': attachments,
        'incoming_source_context': {'source': 'whatsapp', 'source_thread_id': 'scoped-contact', 'message_ids': ['client-source']}})
    assert response.status_code == 200, response.text
    assert len(dispatched) == 1
    assert len([item for item in dispatched[0] if item['mime_type'] == 'video/mp4']) == 2
    texts = {Path(item['path']).read_text() for item in dispatched[0] if item['mime_type'] == 'text/plain'}
    assert texts == ({'Preserved upstream transcript.'} if decoder_fails else {'Preserved upstream transcript.', 'Prepared in task endpoint.'})
    decoder.assert_called_once()


def test_tensology_handoff_rejects_missing_origin_pack_before_creating_anything(monkeypatch):
    from distr.core import tensology_client as tc
    create = Mock()
    monkeypatch.setattr(tc, '_ensure_tensology_kanban_ticket', create)
    with pytest.raises(tc.TensologyApiError, match='proposal pack is missing'):
        tc._create_tensology_thread({'source_metadata': {'incoming_proposal_id': 99}}, {'id': 7})
    create.assert_not_called()


def test_tensology_bridge_creation_allows_bounded_transcription_without_changing_other_http_calls(tmp_path, monkeypatch):
    from distr.core import tensology_client as tc
    records = [_original(tmp_path, 1), _original(tmp_path, 2)]
    ticket = {'id': 17, 'reference': 'MEDIA-17', 'project_id': 90, 'title': 'Media handoff',
              'source_metadata': {'incoming_proposal_id': 99},
              'incoming_proposal': {'source': 'whatsapp', 'source_conversation': {'thread_id': 'scoped-contact'},
                                    'source_message_ids': ['source-1', 'source-2'],
                                    'evidence': [{'id': 'source-1', 'body': 'Original request context.'}]},
              'attachments': [{'name': row['name'], 'mime_type': row['mime_type'], 'local_path': row['path']} for row in records]}
    monkeypatch.setattr(tc, '_tensology_project_route', lambda project: {'backend': 'pi', 'provider': 'fixture', 'model': 'fixture'})
    monkeypatch.setattr(tc, '_ensure_tensology_kanban_ticket', lambda *args: 123)
    monkeypatch.setattr(tc, '_execution_skill_ids', lambda *args: ([], False))
    calls = []
    def request(request, *, timeout):
        calls.append((request.full_url, timeout))
        if request.full_url.endswith('workflows/studio/tasks'):
            payload = json.loads(request.data)
            assert payload['incoming_source_context'] == {'source': 'whatsapp', 'source_thread_id': 'scoped-contact', 'message_ids': ['source-1', 'source-2']}
            assert 'Original request context.' in payload['prompt']
        return io.BytesIO(json.dumps({'task': {'id': 77}}).encode())
    monkeypatch.setattr(tc.urllib.request, 'urlopen', request)
    assert tc._create_tensology_thread(ticket, {'id': 7, 'name': 'Media project'})[0] == 77
    assert calls[-1][1] == 30 + 2 * (transcription.TRANSCRIPTION_TIMEOUT_SECONDS + transcription.TRANSCRIPTION_LOCK_TIMEOUT_SECONDS)
    assert calls[-1][1] > 15
    tc._local_api_post('unrelated/action', {})
    assert calls[-1][1] == 15


def test_native_incoming_handoff_uses_manifest_transcription_budget(monkeypatch):
    from distr.gui.development_hot_corner import start_desktop_proposal
    from distr.core import tensology_client as tc
    manifest = [{'name': 'one.mp4', 'mime_type': 'video/mp4'}, {'name': 'two.ogg', 'mime_type': 'audio/ogg'}, {'name': 'image.jpg', 'mime_type': 'image/jpeg'}]
    monkeypatch.setattr(media, 'list_proposal_attachment_manifest', lambda proposal: manifest)
    post = Mock(return_value={'chat_id': 349})
    monkeypatch.setattr(tc, '_local_api_post', post)
    proposal = {'id': 'local:native', 'source': 'whatsapp'}
    assert start_desktop_proposal(proposal) == 349
    assert post.call_args.kwargs['timeout'] == transcription.thread_preparation_timeout(manifest)
    assert post.call_args.kwargs['timeout'] == 330
