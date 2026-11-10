import json
from unittest.mock import Mock

from distr.core.incoming import proposals, project_links
from tests.core.test_incoming_project_routing import projects


def test_imported_merged_sources_keep_original_references_and_transcripts():
    evidence = [{'id': 'merged:1', 'body': 'First request', 'transcript': 'First transcript.',
                 'original_source': {'channel': 'whatsapp', 'source_thread_id': 'original-wa', 'source_message_id': 'same-id'}},
                {'id': 'merged:2', 'body': 'Second request', 'transcript': 'Second transcript.',
                 'original_source': {'channel': 'gmail', 'source_thread_id': 'original-mail', 'source_message_id': 'same-id'}}]
    row = proposals._from_tensology({'id': 123, 'project': {'decisions_project_id': 7},
        'source': 'whatsapp', 'source_conversation': {'thread_id': 'merged:collection'},
        'source_message_ids': ['merged:1', 'merged:2'], 'evidence': evidence})
    assert [item['transcript'] for item in row['evidence']] == ['First transcript.', 'Second transcript.']
    assert [item['original_source'] for item in row['evidence']] == [item['original_source'] for item in evidence]


def test_linked_group_restricts_model_candidates_and_cannot_route_elsewhere(monkeypatch):
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [{'id': 2, 'name': 'Merrypak'}, {'id': 19, 'name': 'Multisnack'}])
    item = {'source': 'whatsapp', 'source_thread_id': 'group', 'source_message_id': 'm1',
            'text': 'Please fix checkout', 'sender': 'Client', 'project_id': 2}
    def complete(provider, model, messages, settings):
        payload = json.loads(messages[1]['content'])
        assert [row['id'] for row in payload['candidate_projects']] == [2]
        return json.dumps({'is_actionable': True, 'project_id': 19, 'title': 'Fix checkout'})
    result = proposals._triage_conversations([item], {}, complete=complete)
    assert result[0]['project_id'] == 2


def test_shared_contact_is_routed_by_context_and_outgoing_reply_is_context(monkeypatch):
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [{'id': 8, 'name': 'Snuza'}, {'id': 10, 'name': 'Spoortrack'}, {'id': 4, 'name': 'Player1Sport'}, {'id': 2, 'name': 'Merrypak'}])
    base = {'source': 'whatsapp', 'source_thread_id': 'greg', 'project_ids': [8, 10, 4]}
    items = [{**base, 'source_message_id': 'm1', 'text': 'Please fix the tracking report', 'sender': 'Greg', 'created_at': '2026-11-10T08:00:00'},
             {**base, 'source_message_id': 'm2', 'text': 'This is for Spoortrack', 'sender': 'Paul', 'direction': 'outbound', 'from_me': True, 'created_at': '2026-11-10T08:01:00'}]
    def complete(provider, model, messages, settings):
        payload = json.loads(messages[1]['content'])
        assert {row['id'] for row in payload['candidate_projects']} == {8, 10, 4}
        assert payload['messages'][-1]['direction'] == 'outbound'
        return json.dumps({'is_actionable': True, 'project_id': 10, 'title': 'Fix tracking report'})
    result = proposals._triage_conversations(items, {}, complete=complete)
    assert result[0]['project_id'] == 10
    assert result[0]['message_ids'] == ['m1']
    assert result[0]['source_context_message_ids'] == ['m2']
    assert proposals._triage_conversations(items[1:], {}, complete=Mock(side_effect=AssertionError('outgoing-only'))) == []


def test_canonical_unmapped_links_never_fall_back_to_an_unrelated_project(monkeypatch):
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [{'id': 2, 'name': 'Merrypak'}])
    item = {'source': 'whatsapp', 'source_thread_id': 'contact', 'text': 'Please fix this',
            'sender': 'Client', 'project_links_authoritative': True, 'project_ids': []}
    assert proposals._triage_conversations([item], {}, complete=Mock(side_effect=AssertionError('unmapped'))) == []


def test_shared_contact_split_preserves_exact_sources_and_rejects_overlap(monkeypatch):
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [{'id': 8, 'name': 'Snuza'}, {'id': 10, 'name': 'Spoortrack'}])
    base = {'source': 'whatsapp', 'source_thread_id': 'greg', 'sender': 'Greg', 'project_ids': [8, 10]}
    items = [{**base, 'source_message_id': 'm1', 'text': 'Fix the monitor'},
             {**base, 'source_message_id': 'm2', 'text': 'Fix the tracking report'}]
    rows = [{'is_actionable': True, 'project_id': 8, 'title': 'Fix monitor', 'source_message_ids': ['m1']},
            {'is_actionable': True, 'project_id': 10, 'title': 'Fix report', 'source_message_ids': ['m2']}]
    complete = lambda *args: json.dumps({'proposals': rows})
    result = proposals._triage_conversations(items, {}, complete=complete)
    assert {row['project_id']: row['message_ids'] for row in result} == {8: ['m1'], 10: ['m2']}
    assert len({row['id'] for row in result}) == 2
    rows[1]['source_message_ids'] = ['m1']
    assert proposals._triage_conversations(items, {}, complete=complete) == []


def test_canonical_project_link_cache_survives_offline_and_empty_success_clears(monkeypatch, tmp_path):
    monkeypatch.setattr(project_links, '_cache_path', lambda: tmp_path / 'links.json')
    monkeypatch.setattr(project_links, '_checked_at', 0)
    fake = Mock()
    fake.get.return_value = [{'source': 'whatsapp', 'source_thread_id': 'greg', 'projects': [{'decisions_project_id': 10}]}]
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: fake)
    assert project_links.project_links(refresh=True)[0]['source_thread_id'] == 'greg'
    fake.get.side_effect = RuntimeError('offline')
    monkeypatch.setattr(project_links, '_checked_at', 0)
    assert project_links.project_links(refresh=True)[0]['projects'][0]['decisions_project_id'] == 10
    fake.get.side_effect = None
    fake.get.return_value = []
    monkeypatch.setattr(project_links, '_checked_at', 0)
    assert project_links.project_links(refresh=True) == []


def test_canonical_candidates_project_into_local_messages_by_stable_id(projects, monkeypatch):
    monkeypatch.setattr(project_links, 'project_links', lambda **kwargs: [{
        'source': 'whatsapp', 'source_thread_id': 'shared',
        'projects': [{'id': 175, 'decisions_project_id': 3}, {'id': 169, 'decisions_project_id': 16},
                     {'id': 3, 'decisions_project_id': 999}],
    }])
    items = [{'source': 'whatsapp', 'source_thread_id': 'shared'},
             {'source': 'telegram', 'source_thread_id': 'shared'}]
    project_links.apply_project_links(items)
    assert items[0]['project_ids'] == [3, 16]
    assert items[0]['project_links_authoritative'] is True
    assert items[1]['project_ids'] == []
    assert items[1]['project_links_authoritative'] is True
