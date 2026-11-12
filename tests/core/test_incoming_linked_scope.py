import json
from unittest.mock import Mock

import pytest

from distr.core.incoming import proposals, project_links, proposal_sync
from distr.core.workflow import development_threads
from tests.core.test_incoming_project_routing import projects, item


def test_telegram_group_snapshot_requires_a_fresh_source_and_live_project(projects, monkeypatch, tmp_path):
    cache = tmp_path / 'links.json'
    monkeypatch.setattr(project_links, '_cache_path', lambda: cache)
    rows = [
        {'source': source, 'source_thread_id': chat, 'projects': [{'decisions_project_id': project}]}
        for source, chat, project in [('telegram', '-1001234567', 3), ('telegram', '-1009999999', 999),
                                      ('telegram', '12345', 3), ('whatsapp', '-1009876543', 3)]
    ]
    client = Mock()
    client.get.side_effect = [{'data': rows}, OSError('offline'), {'data': []}]
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **_: client)
    assert project_links.telegram_group_snapshot() == [-1001234567]
    assert project_links.telegram_group_snapshot() is None
    assert project_links.project_links() == rows
    assert project_links.telegram_group_snapshot() == []


@pytest.mark.parametrize('source,thread', [('whatsapp', 'personal-group@g.us'), ('whatsapp', 'unknown-person'), ('gmail', 'unknown-mail'), ('mailshot', 'unknown-email')])
def test_unlinked_messages_never_reach_the_model(projects, monkeypatch, source, thread):
    monkeypatch.setattr(project_links, 'project_links', lambda **kw: [])
    rows = [item(source=source, source_thread_id=thread, sender='Greg', text='Please change the billing type',
                 project_id=3, sender_phone='linked-member', is_work=True)]
    project_links.apply_project_links(rows)
    assert rows[0]['project_ids'] == []
    assert 'project_id' not in rows[0]
    model = Mock(side_effect=AssertionError('Unlinked personal text must never be sent to AI'))
    assert proposals._triage_conversations(rows, {}, complete=model) == []
    model.assert_not_called()


def test_linked_person_does_not_link_their_unlinked_group(projects, monkeypatch):
    monkeypatch.setattr(project_links, 'project_links', lambda **kw: [{'source': 'whatsapp', 'source_thread_id': 'greg', 'projects': [{'decisions_project_id': 3}]}])
    rows = [item(source_thread_id='greg'), item(source_thread_id='personal-group@g.us', sender='Greg')]
    project_links.apply_project_links(rows)
    assert rows[0]['project_ids'] == [3]
    assert rows[1]['project_ids'] == []


def test_explicit_group_link_resolves_board_to_project_without_using_member(projects, monkeypatch):
    from distr.core.db import WhatsAppPhoneLink
    monkeypatch.setattr(project_links, 'project_links', lambda **kw: [])
    with projects() as db:
        db.add(WhatsAppPhoneLink(board_id=16, phone_jid='work-group@g.us', contact_name='Work group'))
        db.commit()
    rows = [item(source_thread_id='work-group@g.us')]
    project_links.apply_project_links(rows)
    assert rows[0]['project_ids'] == [3]
    model = Mock(return_value=json.dumps({'is_actionable': True, 'project_id': 3, 'title': 'Fix work'}))
    assert proposals._triage_conversations(rows, {}, complete=model)[0]['project_id'] == 3


def test_cached_cards_are_scoped_and_exact_source_duplicates_collapse(projects, monkeypatch):
    scope = {('whatsapp', 'greg'): {3}}
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kw: scope)
    base = {'id': 'local:whatsapp:greg', 'origin': 'local', 'source': 'whatsapp', 'source_thread_id': 'greg',
            'dedupe_key': 'whatsapp:greg', 'message_ids': ['m1'], 'project_id': 3, 'title': 'Billing request'}
    duplicate = {**base, 'id': base['id'] + ':split-hash'}
    unlinked = {**base, 'id': 'personal', 'source_thread_id': 'personal-group@g.us'}
    wrong = {**base, 'id': 'wrong', 'project_id': 16}
    assert proposals._scoped_proposals([base, duplicate, unlinked, wrong]) == [base]
    shared = {**base, 'id': 'tensology:17', 'origin': 'tensology'}
    assert proposals._scoped_proposals([base, shared, duplicate]) == [shared]
    proposals._store_proposals_list_cache({'proposals': [base, duplicate, unlinked], 'counts': {'whatsapp': 3}})
    assert [row['id'] for row in proposals.list_incoming_proposals()['proposals']] == [base['id']]
    scope.clear()
    assert proposals.list_incoming_proposals()['proposals'] == []
    proposals.invalidate_proposals_list_cache()


def test_stale_or_forged_card_cannot_submit_merge_or_sync(projects, monkeypatch):
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kw: {})
    row = {'id': 'personal', 'source': 'whatsapp', 'source_thread_id': 'unlinked-group@g.us',
           'project_id': 3, 'project_ids': [3], 'title': 'Stale personal proposal'}
    engine = Mock()
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', engine)
    with pytest.raises(ValueError, match='Link this source'):
        proposals.create_proposal_thread(row, start_work=True)
    with pytest.raises(ValueError, match='Link both source'):
        proposals.merge_proposals(row, row)
    client = Mock()
    with pytest.raises(ValueError, match='Link the source'):
        proposal_sync._upload(client, row, {3: 168})
    engine.assert_not_called()
    client.post.assert_not_called()


def test_merged_proposal_requires_every_original_source_link(projects, monkeypatch):
    scope = {('whatsapp', 'work'): {3}, ('gmail', 'mail'): {3}}
    row = {'project_id': 3, 'source': 'whatsapp', 'source_thread_id': 'work', 'source_scope': [{'source': 'whatsapp', 'source_thread_id': 'work'}, {'source': 'gmail', 'source_thread_id': 'mail'}]}
    assert project_links.proposal_in_scope(row, scope)
    del scope[('gmail', 'mail')]
    assert not project_links.proposal_in_scope(row, scope)


def test_claimed_merged_scope_cannot_override_an_unlinked_actual_source():
    scope = {('whatsapp', 'work'): {3}}
    row = {'project_id': 3, 'source': 'whatsapp', 'source_thread_id': 'personal@g.us',
           'source_scope': [{'source': 'whatsapp', 'source_thread_id': 'work'}]}
    assert not project_links.proposal_in_scope(row, scope)


def test_saved_human_assignment_is_applied_before_rejecting_legacy_wrong_project(projects, monkeypatch):
    from distr.core.db.projects import IncomingProposalProject
    row = {'id': 'local:whatsapp:shared', 'origin': 'local', 'source': 'whatsapp', 'source_thread_id': 'shared',
           'message_ids': ['m1'], 'project_id': 16, 'title': 'Corrected billing request'}
    with projects() as db:
        db.add(IncomingProposalProject(key=proposals._proposal_handoff_key(row), project_id=3))
        db.commit()
    monkeypatch.setattr(project_links, 'source_project_index', lambda **kwargs: {('whatsapp', 'shared'): {3}})
    result = proposals._scoped_proposals([row])
    assert result[0]['project_id'] == 3
    assert result[0]['project_corrected'] is True
