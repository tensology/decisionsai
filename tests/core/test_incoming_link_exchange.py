"""Remote-first deltas preserve other projects and do not claim unconfirmed sync."""
from unittest.mock import Mock
import pytest

from distr.core.incoming import link_exchange, project_links
from distr.core.tensology_client import TensologyApiError


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'tensology_enabled': True})
    monkeypatch.setattr(project_links, '_cache_path', lambda: tmp_path / 'links.json')
    remote = Mock()
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **k: remote)
    return remote


@pytest.mark.parametrize('source', ['gmail', 'mailshot', 'whatsapp'])
@pytest.mark.parametrize('remove', [False, True])
def test_each_channel_uses_confirmed_delta_and_retains_other_projects(client, source, remove):
    email = 'customer@example.test' if source != 'whatsapp' else None
    selected = {'id': 175, 'decisions_project_id': 2}
    other = {'id': 191, 'decisions_project_id': 3}
    current = {'source': source, 'source_thread_id': 'thread', 'projects': [selected, other] if remove else [other],
               'revision': 'conversation-old', 'sender_revision': 'sender-old'}
    rows = [current]
    sender = {'source': 'mail_sender', 'source_thread_id': email, 'projects': current['projects'], 'revision': 'sender-old'}
    if email:
        rows.append(sender)
    result_projects = [other] if remove else [selected, other]
    confirmed = [{**current, 'projects': result_projects}]
    if email:
        confirmed.append({**sender, 'projects': result_projects})
    client.get.side_effect = lambda path: [selected, other] if path == 'projects' else rows
    client.patch.return_value = {**confirmed[0], 'links': confirmed}
    result = link_exchange.sync_project_delta(source, 'thread', 2, sender=email, remove=remove)
    assert result['synced']
    assert result['project_ids'] == ([3] if remove else [2, 3])
    body = client.patch.call_args.args[1]
    assert body['action'] == ('remove' if remove else 'add')
    assert body['project_ids'] == [175]
    assert body['scope'] == ('sender' if email else 'conversation')
    assert body['revision'] == 'conversation-old'
    if email:
        assert body['sender_email'] == email and body['sender_revision'] == 'sender-old'
    else:
        assert 'sender_email' not in body
    assert any(project['id'] == 191 for row in project_links.project_links() for project in row.get('projects') or [])


def test_old_empty_server_is_rejected_before_any_link_write(client):
    client.get.side_effect = lambda path: ([{'id': 175, 'decisions_project_id': 2}] if path == 'projects'
                                         else {'project_links_version': 1} if path == 'identity' else [])
    with pytest.raises(TensologyApiError, match='Update Tensology'):
        link_exchange.sync_project_delta('whatsapp', 'group@g.us', 2)
    client.patch.assert_not_called()
    assert project_links.project_links() == []


def test_empty_modern_server_can_create_first_conversation_link(client):
    selected = {'id': 175, 'decisions_project_id': 2}
    client.get.side_effect = lambda path: ([selected] if path == 'projects'
                                         else {'project_links_version': 2} if path == 'identity' else [])
    client.patch.return_value = {'source': 'whatsapp', 'source_thread_id': 'group@g.us', 'projects': [selected]}
    assert link_exchange.sync_project_delta('whatsapp', 'group@g.us', 2)['synced']


def test_remote_removal_conflict_never_updates_cached_membership(client):
    old = {'source': 'whatsapp', 'source_thread_id': 'group@g.us', 'sender_revision': '', 'revision': 'old',
           'projects': [{'id': 175, 'decisions_project_id': 2}]}
    project_links._store_links([old])
    client.get.side_effect = lambda path: [{'id': 175, 'decisions_project_id': 2}] if path == 'projects' else [old]
    client.patch.side_effect = TensologyApiError('Changed elsewhere', status=409)
    with pytest.raises(TensologyApiError):
        link_exchange.sync_project_delta('whatsapp', 'group@g.us', 2, remove=True)
    assert project_links.project_links() == [old]


@pytest.mark.parametrize('remove', [False, True])
def test_private_alias_deltas_are_atomic_and_confirmed_for_every_known_alias(client, remove):
    selected = {'id': 175, 'decisions_project_id': 2}
    lid, phone = '987654321@lid', '123456789@s.whatsapp.net'
    rows = [{'source': 'whatsapp', 'source_thread_id': thread, 'sender_revision': '', 'revision': thread,
             'projects': [selected] if remove else [], 'link_explicit': True} for thread in [lid, phone]]
    client.get.side_effect = lambda path: [selected] if path == 'projects' else {'project_links_version': 3} if path == 'identity' else rows
    confirmed = [{**row, 'projects': [] if remove else [selected]} for row in rows]
    client.patch.return_value = {**confirmed[0], 'links': confirmed}
    assert link_exchange.sync_project_delta('whatsapp', lid, 2, aliases={lid, phone}, remove=remove)['synced']
    assert client.patch.call_args.args[1]['alias_revisions'] == {phone: phone}


def test_legacy_alias_api_is_rejected_before_partial_mutation(client):
    selected = {'id': 175, 'decisions_project_id': 2}
    phone = '123456789@s.whatsapp.net'
    rows = [{'source': 'whatsapp', 'source_thread_id': phone, 'sender_revision': '', 'revision': 'old', 'projects': [], 'link_explicit': True}]
    client.get.side_effect = lambda path: [selected] if path == 'projects' else {'project_links_version': 2} if path == 'identity' else rows
    with pytest.raises(TensologyApiError, match='atomic WhatsApp alias'):
        link_exchange.sync_project_delta('whatsapp', '987654321@lid', 2, aliases={phone})
    client.patch.assert_not_called()


def test_project_link_push_refreshes_scope_without_starting_or_creating_work(monkeypatch):
    from distr.core import tensology_stream
    refresh, invalidate, publish, create = Mock(return_value=[]), Mock(), Mock(), Mock()
    monkeypatch.setattr(project_links, 'project_links', refresh)
    monkeypatch.setattr('distr.core.incoming.proposals.invalidate_proposals_list_cache', invalidate)
    monkeypatch.setattr('distr.core.incoming.proposals.create_proposal_thread', create)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', publish)
    tensology_stream.handle_work_request_signal({'event': 'project_links_changed'})
    refresh.assert_called_once_with(force=True, require_fresh=True)
    invalidate.assert_called_once_with()
    publish.assert_called_once_with(status='project_links_changed')
    create.assert_not_called()


def test_project_link_push_failure_is_recorded_without_launching_work(monkeypatch):
    from distr.core import tensology_stream
    monkeypatch.setattr(tensology_stream, 'STREAM_STATE', {})
    monkeypatch.setattr(project_links, 'project_links', lambda **k: None)
    publish, create = Mock(), Mock()
    monkeypatch.setattr('distr.core.incoming.proposals.create_proposal_thread', create)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', publish)
    tensology_stream.handle_work_request_signal({'event': 'project_links_changed'})
    assert 'could not be refreshed' in tensology_stream.STREAM_STATE['last_error']
    publish.assert_called_once_with(status='project_links_sync_failed')
    create.assert_not_called()


def test_sender_remove_all_is_atomic_and_confirmation_covers_all_selected_projects(client):
    projects = [{'id': 175, 'decisions_project_id': 2}, {'id': 191, 'decisions_project_id': 3}]
    current = {'source': 'gmail', 'source_thread_id': 'known', 'sender_email': 'customer@example.test',
               'sender_revision': 'sender', 'revision': 'conversation', 'projects': projects}
    sender = {'source': 'mail_sender', 'source_thread_id': 'customer@example.test', 'revision': 'sender', 'projects': projects}
    client.get.side_effect = lambda path: projects if path == 'projects' else [current, sender]
    confirmed = [{**current, 'projects': []}, {**sender, 'projects': []}]
    client.patch.return_value = {**confirmed[0], 'links': confirmed}
    assert link_exchange.sync_project_deltas('gmail', 'known', [2, 3], sender='customer@example.test', remove=True)['synced']
    client.patch.assert_called_once()
    assert client.patch.call_args.args[1]['project_ids'] == [175, 191]
    client.patch.return_value = {**current, 'links': [current, {**sender, 'projects': [projects[1]]}]}
    with pytest.raises(TensologyApiError, match='confirm'):
        link_exchange.sync_project_deltas('gmail', 'known', [2, 3], sender='customer@example.test', remove=True)
