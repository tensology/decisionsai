import json
from unittest.mock import Mock

import pytest

from distr.core.incoming import proposals, project_links, proposal_sync, media
from tests.core.test_incoming_project_routing import projects, item


def review():
    return {'id': 'local:whatsapp:shared', 'origin': 'local', 'source': 'whatsapp',
            'source_thread_id': 'shared', 'dedupe_key': 'whatsapp:shared', 'message_ids': ['new'],
            'project_id': None, 'needs_project': True, 'title': 'Enable new billing option',
            'description': 'Original request', 'candidate_projects': [{'id': 3, 'name': 'Correct project'}, {'id': 7, 'name': 'Another project'}],
            'evidence': [{'id': 'new', 'body': 'Enable this option', 'direction': 'inbound', 'transcript': 'Preserved voice transcript'}],
            'attachments': [{'name': 'source.oga', 'mime_type': 'audio/ogg', 'transcript': 'Preserved voice transcript'}]}


def test_infers_from_earlier_exact_thread_context_without_literal_project_name(projects, monkeypatch):
    from distr.core.db import WhatsAppMessage
    with projects() as db:
        db.add_all([
            WhatsAppMessage(jid='shared', message_id='earlier', text='The vehicle telemetry report needs the device billing option.', from_me=True, whatsapp_timestamp=100),
            WhatsAppMessage(jid='shared', message_id='new', text='Please enable that option.', whatsapp_timestamp=200),
            WhatsAppMessage(jid='personal@g.us', message_id='foreign', text='A different client product', whatsapp_timestamp=150),
        ])
        db.commit()
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [
        {'id': 3, 'name': 'Correct project', 'description': 'Vehicle telemetry and device billing'},
        {'id': 7, 'name': 'Another project', 'description': 'Infant monitors'}])
    def analyze(provider, model, messages, settings):
        payload = json.loads(messages[1]['content'])
        assert [row['id'] for row in payload['thread_context']] == ['earlier']
        assert 'Vehicle telemetry' in payload['candidate_projects'][0]['description']
        return json.dumps({'is_actionable': True, 'project_id': 3, 'confidence': .96,
                           'project_evidence_message_ids': ['earlier'], 'title': 'Enable option'})
    row = proposals._triage_conversations([item(source_thread_id='shared', source_message_id='new',
                    text='Please enable that option.', project_ids=[3, 7])], {}, complete=analyze)[0]
    assert row['project_id'] == 3 and row['needs_project'] is False
    assert row['message_ids'] == ['new']
    assert row['source_context_message_ids'] == ['earlier']
    assert any(e['id']=='earlier' and e['context_only'] for e in row['evidence'])


def test_unresolved_review_is_listed_durable_and_retains_evidence_and_transcripts(projects, monkeypatch):
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kwargs: {('whatsapp', 'shared'): {3, 7}})
    row = review()
    proposals._record_local_proposals([row])
    saved = proposals._saved_local_proposals()
    assert len(saved) == 1 and saved[0]['needs_project'] is True
    assert saved[0]['evidence'][0]['transcript'] == 'Preserved voice transcript'
    assert proposals._scoped_proposals(saved)[0]['project_id'] is None
    proposals._store_proposals_list_cache({'proposals': saved, 'counts': {'whatsapp': 1}})
    assert proposals.list_incoming_proposals()['proposals'][0]['needs_project'] is True
    proposals.invalidate_proposals_list_cache()


def test_uncertainty_persists_without_overwriting_saved_evidence_clock(projects):
    from distr.core.db import IncomingProposalSnapshot
    row = review()
    proposals._record_local_proposals([{**row, 'project_id': 3, 'needs_project': False}])
    key = proposals._proposal_handoff_key(row)
    with projects() as db:
        original_time = db.get(IncomingProposalSnapshot, key).created_at
    proposals._record_local_proposals([{**row, 'description': 'Regenerated text must not replace original'}])
    with projects() as db:
        saved = db.get(IncomingProposalSnapshot, key)
        assert saved.created_at == original_time
        data = json.loads(saved.snapshot)
    assert data['project_id'] is None and data['needs_project'] is True
    assert data['description'] == 'Original request'


def test_review_can_sync_but_cannot_create_thread_merge_or_autoaccept(projects, monkeypatch):
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kwargs: {('whatsapp', 'shared'): {3, 7}})
    row = review()
    assert project_links.proposal_in_scope(row, allow_review=True)
    assert not project_links.proposal_in_scope(row)
    assert not project_links.proposal_in_scope({**row, 'project_id': 3})
    engine = Mock()
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', engine)
    with pytest.raises(ValueError, match='Link this source'):
        proposals.create_proposal_thread(row, start_work=True)
    with pytest.raises(ValueError, match='Link both source'):
        proposals.merge_proposals(row, row)
    engine.assert_not_called()
    client = Mock()
    proposal_sync._upload(client, row, {3: 175, 7: 176})
    body = client.post.call_args.args[1]
    assert body['project_id'] is None and body['needs_project'] is True
    assert body['evidence'][0]['transcript'] == 'Preserved voice transcript'


def test_assignment_is_linked_only_and_remains_confirmed_after_reload(projects, monkeypatch):
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kwargs: {('whatsapp', 'shared'): {3, 7}})
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kwargs: None)
    row = review()
    proposals._record_local_proposals([row])
    with pytest.raises(ValueError, match='linked projects'):
        proposals.reassign_proposal_project(row, 16)
    saved = proposals.reassign_proposal_project(row, 3)
    assert saved['project_id'] == 3 and saved['needs_project'] is False
    reloaded = proposals._scoped_proposals(proposals._saved_local_proposals())[0]
    assert reloaded['project_id'] == 3 and reloaded['needs_project'] is False
    assert reloaded['evidence'][0]['transcript'] == 'Preserved voice transcript'


def test_unlinked_source_cannot_claim_review_eligibility(projects, monkeypatch):
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kwargs: {})
    assert proposals._scoped_proposals([review()]) == []
    with pytest.raises(ValueError, match='Link the source'):
        proposal_sync._upload(Mock(), review(), {3: 175})


def test_canonical_review_maps_candidates_without_assigning_a_project():
    row = proposals._from_tensology({'id': 123, 'source': 'gmail', 'source_conversation': {'thread_id': 'mail'},
        'source_message_ids': ['new'], 'needs_project': True, 'project': None,
        'candidate_projects': [{'id': 175, 'name': 'Fleet platform', 'decisions_project_id': 3}],
        'attachments': [{'name': 'voice.oga', 'transcript': 'Retained source', 'mime_type': 'audio/ogg'}]})
    assert row['project_id'] is None and row['needs_project'] is True
    assert row['candidate_projects'] == [{'id': 3, 'name': 'Fleet platform'}]
    assert row['can_push_to_thread'] is False


def test_review_media_and_cached_transcript_survive_assignment(projects, monkeypatch, tmp_path):
    from distr.core.db import WhatsAppMessage
    from distr.core.incoming.transcription import cache_transcript, with_transcript
    monkeypatch.setattr(media.Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(media, 'get_session', projects)
    source = tmp_path / 'source.oga'
    source.write_bytes(b'original audio bytes')
    with projects() as db:
        db.add(WhatsAppMessage(jid='shared', message_id='new', media_type='voice', media_filename='source.oga',
                              media_mime_type='audio/ogg', media_local_path=str(source), whatsapp_timestamp=100))
        db.commit()
    unassigned = media.ensure_proposal_attachments(review(), None)['attachments'][0]
    cache_transcript(unassigned, 'Preserved voice transcript')
    assigned = media.ensure_proposal_attachments({**review(), 'project_id': 3}, 3)['attachments'][0]
    assert media.Path(assigned['path']).read_bytes() == source.read_bytes()
    assert with_transcript(assigned)['transcript'] == 'Preserved voice transcript'
    assert with_transcript(unassigned)['transcript'] == 'Preserved voice transcript'


def test_large_review_snapshot_keeps_confirmation_state(projects):
    row = review()
    row['evidence'] = [{'id': str(i), 'body': 'x' * 4000} for i in range(200)]
    saved = json.loads(proposals.proposal_snapshot_json(row))
    assert saved['needs_project'] is True and saved['project_id'] is None
    assert saved['candidate_projects'] == row['candidate_projects']


@pytest.mark.parametrize('evidence', [5, ['foreign-message'], [{'id': 'prior'}]])
def test_invalid_inference_evidence_keeps_request_visible_for_review(projects, monkeypatch, evidence):
    monkeypatch.setattr('distr.core.incoming.routing_context.earlier_context', lambda _: [
        {'id': 'prior', 'body': 'Earlier conversation context', 'direction': 'outbound'}])
    data = {'is_actionable': True, 'project_id': 3, 'confidence': .99,
            'project_evidence_message_ids': evidence, 'title': 'Retain request for review'}
    result = proposals._triage_conversations([item(project_ids=[3, 7])], {}, complete=lambda *_: json.dumps(data))
    assert result[0]['needs_project'] is True
    assert result[0]['message_ids'] == ['message-1']
