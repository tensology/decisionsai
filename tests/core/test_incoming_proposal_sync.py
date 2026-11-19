"""Cross-app source identity, project revision and cached task round trips."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from distr.core.incoming import proposal_sync as sync
from distr.core.incoming import proposals as incoming


def remote():
    return {'id': 87, 'status': 'pending', 'updated_at': '2026-11-09T08:00:00+00:00',
            'project': {'id': 175, 'name': 'Renamed display', 'decisions_project_id': 2},
            'source': 'gmail', 'source_conversation': {'thread_id': 'thread'},
            'source_message_ids': ['m1'], 'title': 'Original', 'tasks': ['Inspect'], 'task_completed_indexes': []}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr('distr.core.incoming.project_links.project_links', lambda **kwargs: [])
    monkeypatch.setattr('distr.core.incoming.project_links.source_project_index', lambda **kwargs: {('gmail', 'thread'): {2, 3}})
    fake = Mock()
    fake.get.return_value = [{'id': 175, 'decisions_project_id': 2}, {'id': 191, 'decisions_project_id': 3}]
    fake.post.return_value = {}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: fake)
    monkeypatch.setattr(sync, '_corrections', lambda: {})
    monkeypatch.setattr(incoming, '_accepted_proposal_keys', lambda: set())
    monkeypatch.setattr(incoming, '_undismissed_proposals', lambda rows: rows)
    return fake


def test_identity_ignores_brief_and_project_but_not_source_window():
    first = remote()
    changed = {**first, 'title': 'Edited', 'project': {'id': 99}}
    assert sync.source_identity(first) == sync.source_identity(changed)
    assert sync.source_identity(first) != sync.source_identity({**changed, 'source_message_ids': ['m2']})


def test_explicit_project_identity_beats_display_name():
    mapped = incoming._from_tensology(remote(), {'renamed display': 999})
    assert mapped['project_id'] == 2
    assert mapped['updated_at'] == remote()['updated_at']
    assert mapped['task_completed_indexes'] == []


def test_connected_refresh_only_lists_and_acknowledges_canonical_proposals(client, monkeypatch):
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {
        'tensology_enabled': True, 'tensology_key': 'test-key',
    })
    monkeypatch.setattr(incoming, '_project_map', lambda: {})
    fetch = Mock(return_value=[remote()])
    monkeypatch.setattr(sync, 'fetch_remote_proposals', fetch)
    triage = Mock(side_effect=AssertionError('Local proposal triage must stay idle'))
    legacy = Mock(side_effect=AssertionError('Local drafts must not be imported or mixed into the feed'))
    monkeypatch.setattr(incoming, '_local_proposals', triage)
    monkeypatch.setattr(incoming, '_saved_local_proposals', legacy)
    monkeypatch.setattr(sync, 'reconcile', legacy)
    incoming._store_proposals_list_cache({'proposals': [{'id': 'local', 'origin': 'local'}]})
    read = Mock(return_value=[remote()])
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', read)

    initial = incoming.list_incoming_proposals()
    assert [row['id'] for row in initial['proposals']] == ['tensology:87']
    read.assert_called_once()
    refreshed = incoming.list_incoming_proposals(force=True, reconcile_request_id='check-1')
    assert [row['id'] for row in refreshed['proposals']] == ['tensology:87']
    assert refreshed['sync']['state'] == 'complete'
    client.post.assert_called_once_with(
        'work-requests/reconciliation', refreshed['sync'], idempotency_key='reconcile-check-1',
    )
    fetch.side_effect = RuntimeError('Tensology is offline')
    offline = incoming.list_incoming_proposals(force=True)
    assert offline['sync']['state'] == 'partial'
    assert offline['proposals'] == []
    triage.assert_not_called()
    legacy.assert_not_called()


def test_stale_local_card_cannot_import_or_create_work_when_tensology_owns_proposals(client, monkeypatch):
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {
        'tensology_enabled': True, 'tensology_key': 'test-key',
    })
    local = {'id': 'local:gmail:thread', 'source': 'gmail', 'project_id': 2}
    with pytest.raises(ValueError, match='saved local proposal'):
        sync.update_proposal(local, {'title': 'Changed'}, expected_updated_at=None)
    with pytest.raises(ValueError, match='saved local proposal'):
        incoming.create_proposal_thread(local)
    with pytest.raises(ValueError, match='saved local proposal'):
        incoming.merge_proposals({'id': 'tensology:87', 'source': 'gmail'}, local)
    client.post.assert_not_called()
    client.patch.assert_not_called()


def test_local_import_requires_current_review_and_excludes_covered_evidence(client, monkeypatch):
    first = {'id': 'local:gmail:new', 'origin': 'local', 'source': 'gmail', 'source_thread_id': 'thread',
             'message_ids': ['m2'], 'title': 'Review this', 'project_id': 2, 'evidence': []}
    exact = {**first, 'id': 'local:gmail:exact', 'message_ids': ['m1']}
    overlap = {**first, 'id': 'local:gmail:overlap', 'message_ids': ['m1', 'm3']}
    monkeypatch.setattr(sync, '_reviewable_local_rows', lambda: [first, exact, overlap])
    monkeypatch.setattr(sync, 'fetch_remote_proposals', lambda: [remote()])
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda row: True)
    upload = Mock(return_value={'id': 99, 'created': True})
    monkeypatch.setattr(sync, '_upload', upload)

    eligible, blocked = sync.review_local_imports()
    assert eligible['can_import'] and not blocked['can_import']
    assert 'already covered' in blocked['reason']
    upload.assert_not_called()
    with pytest.raises(ValueError):
        sync.import_reviewed_local_proposal(eligible['key'], 'old-revision')
    with pytest.raises(ValueError):
        sync.import_reviewed_local_proposal(blocked['key'], blocked['revision'])
    upload.assert_not_called()
    assert sync.import_reviewed_local_proposal(eligible['key'], eligible['revision']) == {'id': 99, 'created': True}
    upload.assert_called_once()


def test_no_key_keeps_standalone_proposals_without_import(monkeypatch):
    from distr.core.tensology_client import TensologyApiError

    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'tensology_enabled': False})
    monkeypatch.setattr(incoming, '_project_map', lambda: {})
    monkeypatch.setattr('distr.core.incoming.project_links.project_links', lambda **kwargs: [])
    monkeypatch.setattr(sync, 'fetch_remote_proposals', Mock(side_effect=TensologyApiError('No key', code='not_configured')))
    update = Mock(side_effect=AssertionError('Standalone proposals must stay local'))
    monkeypatch.setattr(sync, 'reconcile', update)
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **kwargs: {'items': []})
    local = {'id': 'local:gmail:thread', 'origin': 'local', 'source': 'gmail', 'dedupe_key': 'gmail:thread',
             'source_thread_id': 'thread', 'message_ids': ['m1'], 'title': 'Standalone proposal'}
    monkeypatch.setattr(incoming, '_local_proposals', lambda *args: [local])
    monkeypatch.setattr(incoming, '_record_local_proposals', lambda *args: None)
    monkeypatch.setattr(incoming, '_saved_local_proposals', lambda: [])
    monkeypatch.setattr(incoming, '_scoped_proposals', lambda rows: rows)
    monkeypatch.setattr(incoming, '_accepted_proposal_keys', lambda **kwargs: set())

    payload = incoming.list_incoming_proposals(force=True)
    assert [row['id'] for row in payload['proposals']] == ['local:gmail:thread']
    assert payload['can_review_local_imports'] is False
    assert payload['tensology']['error'] == ''
    update.assert_not_called()


def test_newer_local_assignment_updates_canonical_once(client, monkeypatch):
    correction = {'project_id': 3, 'updated_at': datetime(2026, 11, 9, 9, tzinfo=timezone.utc)}
    monkeypatch.setattr(sync, '_corrections', lambda: {'tensology:87': correction})
    saved = {**remote(), 'project': {'id': 191, 'decisions_project_id': 3}, 'updated_at': '2026-11-09T10:00:00+00:00'}
    client.patch.return_value = saved
    rows, result = sync.reconcile([], [remote()])
    assert result['updated'] == 1
    assert rows[0]['project']['id'] == 191
    assert client.patch.call_args.args[1]['updated_at'] == remote()['updated_at']
    client.patch.reset_mock()
    _, result = sync.reconcile([], rows)
    assert result['updated'] == 0
    client.patch.assert_not_called()


def test_newer_canonical_assignment_ignores_stale_local_override(client, monkeypatch):
    monkeypatch.setattr(sync, '_corrections', lambda: {'tensology:87': {'project_id': 3, 'updated_at': datetime(2026, 11, 8)}})
    rows, result = sync.reconcile([], [remote()])
    assert rows[0]['project']['id'] == 175
    assert result['state'] == 'complete'
    client.patch.assert_not_called()


def test_tied_conflicting_edits_are_reported_not_overwritten(client, monkeypatch):
    monkeypatch.setattr(sync, '_corrections', lambda: {'tensology:87': {'project_id': 3, 'updated_at': datetime(2026, 11, 9, 8)}})
    _, result = sync.reconcile([], [remote()])
    assert result['state'] == 'partial'
    assert result['conflicts'][0]['proposal_id'] == 'tensology:87'
    client.patch.assert_not_called()


def test_dry_run_never_writes(client, monkeypatch):
    monkeypatch.setattr(sync, '_corrections', lambda: {'tensology:87': {'project_id': 3, 'updated_at': datetime(2026, 11, 9, 9)}})
    _, result = sync.reconcile([], [remote()], dry_run=True)
    assert result['planned_changes'][0]['action'] == 'reassign'
    client.patch.assert_not_called()
    client.post.assert_not_called()


@pytest.mark.parametrize('dry_run', [False, True])
def test_local_only_mail_link_is_reported_as_partial_not_success(client, monkeypatch, dry_run):
    monkeypatch.setattr('distr.core.incoming.project_links.mail_project_links', lambda: {('gmail', 'thread'): (2, 'm1')})
    monkeypatch.setattr('distr.core.incoming.project_links.project_links', lambda: [])
    monkeypatch.setattr('distr.core.initiative.attention.learned_thread_links', lambda: {})
    monkeypatch.setattr(incoming, '_TRIAGE_CACHE', {'running': False})
    local = incoming._from_tensology(remote(), {})
    local.update(id='local:gmail:thread', origin='local')
    _, result = sync.reconcile([local], [], dry_run=dry_run)
    assert result['state'] == 'partial'
    assert result['uploaded'] == 0
    assert result['conflicts'][0]['proposal_id'] == local['id']
    assert 'only in DecisionsAI' in result['conflicts'][0]['reason']
    assert all(call.args[0] != 'work-requests/sync' for call in client.post.call_args_list)
    if dry_run:
        client.post.assert_not_called()
    else:
        assert client.post.call_args.args[1]['state'] == 'partial'


@pytest.mark.parametrize('dry_run', [False, True])
@pytest.mark.parametrize('preingested', [False, True])
def test_future_thread_inherits_only_verified_existing_sender_grant(client, monkeypatch, dry_run, preingested):
    sender = {'source': 'mail_sender', 'source_thread_id': 'client@example.test', 'revision': 'sender-rev',
              'projects': [{'id': 175, 'decisions_project_id': 2}]}
    monkeypatch.setattr('distr.core.incoming.project_links.mail_project_links', lambda: {('gmail', 'thread'): (2, 'm1')})
    shared = [sender]
    if preingested:
        shared.append({'source': 'gmail', 'source_thread_id': 'thread', 'projects': [], 'link_explicit': False})
    monkeypatch.setattr('distr.core.incoming.project_links.project_links', lambda: shared)
    monkeypatch.setattr('distr.core.initiative.attention.learned_thread_links', lambda: {})
    verified = Mock(return_value='client@example.test')
    monkeypatch.setattr('distr.core.incoming.project_links.verified_mail_sender', verified)
    monkeypatch.setattr(incoming, '_TRIAGE_CACHE', {'running': False})
    local = incoming._from_tensology(remote(), {})
    local.update(id='local:gmail:thread', origin='local')
    client.post.side_effect = lambda path, body, **kwargs: dict(remote(), created=True) if path == 'work-requests/sync' else {}
    _, result = sync.reconcile([local], [], dry_run=dry_run)
    assert result['state'] == 'complete'
    verified.assert_called_once_with('gmail', 'thread', 'm1')
    if dry_run:
        client.post.assert_not_called()
        assert [row['action'] for row in result['planned_changes']] == ['inherit_sender_link', 'create']
    else:
        upload = next(call for call in client.post.call_args_list if call.args[0] == 'work-requests/sync')
        assert upload.args[1]['sender_email'] == 'client@example.test'
        assert upload.args[1]['sender_revision'] == 'sender-rev'
        assert result['uploaded'] == 1


@pytest.mark.parametrize('mode', ['removed_sender', 'removed_conversation', 'wrong_sender'])
def test_future_sender_inheritance_never_restores_removed_or_different_links(client, monkeypatch, mode):
    sender = {'source': 'mail_sender', 'source_thread_id': 'client@example.test', 'revision': 'sender-rev',
              'projects': [] if mode == 'removed_sender' else [{'id': 175, 'decisions_project_id': 2}]}
    links = [sender]
    if mode == 'removed_conversation':
        links.append({'source': 'gmail', 'source_thread_id': 'thread', 'projects': []})
    monkeypatch.setattr('distr.core.incoming.project_links.mail_project_links', lambda: {('gmail', 'thread'): (2, 'm1')})
    monkeypatch.setattr('distr.core.incoming.project_links.project_links', lambda: links)
    monkeypatch.setattr('distr.core.initiative.attention.learned_thread_links', lambda: {})
    monkeypatch.setattr('distr.core.incoming.project_links.verified_mail_sender',
                        lambda *a: 'other@example.test' if mode == 'wrong_sender' else 'client@example.test')
    monkeypatch.setattr(incoming, '_TRIAGE_CACHE', {'running': False})
    local = incoming._from_tensology(remote(), {})
    local.update(id='local:gmail:thread', origin='local')
    _, result = sync.reconcile([local], [])
    assert result['state'] == 'partial'
    assert result['uploaded'] == 0
    assert all(call.args[0] != 'work-requests/sync' for call in client.post.call_args_list)


def test_partial_source_overlap_reports_conflict_instead_of_duplicate(client):
    local = incoming._from_tensology(remote(), {})
    local.update(id='local:gmail:thread', origin='local', message_ids=['m1', 'm2'])
    _, result = sync.reconcile([local], [remote()])
    assert result['state'] == 'partial'
    assert 'overlaps' in result['conflicts'][0]['reason']
    assert all(call.args[0] != 'work-requests/sync' for call in client.post.call_args_list)


def test_aggregate_local_window_already_covered_is_suppressed(client):
    local = incoming._from_tensology(remote(), {})
    local.update(id='local:gmail:thread', origin='local', message_ids=['m1', 'm2'])
    second = {**remote(), 'id': 88, 'source_message_ids': ['m2']}
    _, result = sync.reconcile([local], [remote(), second])
    assert result['state'] == 'complete'
    assert result['suppressed_local'] == 1
    assert all(call.args[0] != 'work-requests/sync' for call in client.post.call_args_list)


def test_canonical_subset_is_not_hidden_by_unrelated_broad_dismissal():
    import json
    from distr.core.db import get_session, IncomingProposalDismissal
    key = 'local:gmail:thread:unrelated-large-window'
    with get_session() as db:
        db.merge(IncomingProposalDismissal(key=key, conversation_key='gmail:thread', message_ids=json.dumps(['m1', 'm2'])))
        db.commit()
    try:
        row = incoming._from_tensology(remote(), {})
        assert incoming._undismissed_proposals([row]) == [row]
    finally:
        with get_session() as db:
            db.query(IncomingProposalDismissal).filter_by(key=key).delete()
            db.commit()


def test_task_save_roundtrip_uses_revision_and_returns_updated_canonical(client, monkeypatch):
    shaped = incoming._from_tensology(remote(), {})
    saved = {**remote(), 'updated_at': '2026-11-09T09:00:00+00:00', 'task_completed_indexes': [0]}
    client.patch.return_value = saved
    monkeypatch.setattr(incoming, '_project_map', lambda: {})
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kwargs: None)
    result = sync.update_proposal(shaped, {'task_completed_indexes': [0]}, expected_updated_at=shaped['updated_at'])
    assert result['task_completed_indexes'] == [0]
    assert result['updated_at'] == saved['updated_at']
    with pytest.raises(ValueError, match='changed'):
        sync.update_proposal(result, {'task_completed_indexes': []}, expected_updated_at=shaped['updated_at'])


def test_rich_tasks_preserved_in_upload_and_return(client):
    row = incoming._from_tensology(remote(), {})
    row.update(id='local:gmail:thread', origin='local', tasks=[{'title': 'Inspect', 'description': 'Detailed step', 'acceptance_criteria': 'Evidence'}])
    client.post.return_value = {**remote(), 'tasks': ['Inspect'], 'source_task_details': deepcopy(row['tasks'])}
    saved = sync._upload(client, row, {2: 175})
    body = client.post.call_args.args[1]
    assert body['tasks'] == ['Inspect']
    assert body['source_task_details'] == row['tasks']
    assert incoming._from_tensology(saved, {})['tasks'] == row['tasks']


def test_remote_failure_is_partial_not_complete(monkeypatch):
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', Mock(side_effect=RuntimeError('Offline')))
    _, result = sync.reconcile([], [])
    assert result['state'] == 'partial'
    assert result['error'] == 'Offline'


def test_real_edit_clock_beats_later_background_update(client, monkeypatch):
    row = {**remote(), 'details_edited_at': '2026-11-09T07:00:00+00:00', 'updated_at': '2026-11-09T10:00:00+00:00'}
    monkeypatch.setattr(sync, '_corrections', lambda: {'tensology:87': {'project_id': 3, 'updated_at': datetime(2026, 11, 9, 9)}})
    client.patch.return_value = {**row, 'project': {'id': 191, 'decisions_project_id': 3}}
    _, result = sync.reconcile([], [row])
    assert result['updated'] == 1


def test_inbound_identity_preserves_outbound_context_zero_effort_and_completed_tasks(client):
    row = incoming._from_tensology(remote(), {})
    row.update(id='local:gmail:thread', origin='local', estimated_minutes=0, message_ids=['m1', 'reply'],
               evidence=[{'id': 'm1', 'direction': 'inbound', 'body': 'Request'}, {'id': 'reply', 'direction': 'outbound', 'body': 'My response'}],
               task_completed_indexes=[0])
    assert sync.source_identity(row) == sync.source_identity(remote())
    sync._upload(client, row, {2: 175})
    body = client.post.call_args.args[1]
    assert body['source_message_ids'] == ['m1']
    assert body['source_context_message_ids'] == ['reply']
    assert body['estimated_hours'] == '0'
    assert body['task_completed_indexes'] == [0]
    assert body['evidence'][1]['body'] == 'My response'


def test_manual_check_waits_for_new_triage_before_acknowledging(client, monkeypatch):
    monkeypatch.setitem(incoming._TRIAGE_CACHE, 'running', True)
    sync.wait_for_triage('manual-check')
    _, result = sync.reconcile([], [remote()], request_id='manual-check')
    assert result['state'] == 'requested'
    assert not any(call.args[0] == 'work-requests/reconciliation' for call in client.post.call_args_list)
    monkeypatch.setitem(incoming._TRIAGE_CACHE, 'running', False)
    monkeypatch.setattr(sync, 'fetch_remote_proposals', lambda: [remote()])
    sync.finish_triage([])
    acknowledgements = [call.args[1] for call in client.post.call_args_list if call.args[0] == 'work-requests/reconciliation']
    assert acknowledgements[-1]['request_id'] == 'manual-check'
    assert acknowledgements[-1]['state'] == 'complete'


def test_finished_triage_notifies_and_refreshes_status_when_proposal_keys_are_unchanged(client, monkeypatch):
    from distr.core.incoming import events
    monkeypatch.setitem(incoming._TRIAGE_CACHE, 'running', False)
    monkeypatch.setattr(sync, 'fetch_remote_proposals', lambda: [remote()])
    monkeypatch.setattr(sync, '_LAST_RESULT', {'state': 'requested', 'request_id': 'same-keys'})
    publish = Mock()
    monkeypatch.setattr(events, 'publish_incoming_change', publish)
    row = incoming._from_tensology(remote(), {})
    pending_payload = {'proposals': [row], 'counts': {'gmail': 1}, 'tensology': {'error': ''},
                       'sync': {'state': 'requested', 'request_id': 'same-keys'}}
    incoming._store_proposals_list_cache(pending_payload)
    sync.wait_for_triage('same-keys')
    sync.finish_triage([])
    assert incoming._cached_proposals_list() is None
    publish.assert_called_once_with(status='proposal_sync')
    assert sync.last_result()['state'] == 'complete'
    # A concurrent list request may finish writing its old cache after the
    # worker invalidates it. The cached read must still use the latest status.
    incoming._store_proposals_list_cache(pending_payload)
    latest = incoming.list_incoming_proposals(refresh_local=False)
    assert latest['proposals'][0]['id'] == row['id']
    assert latest['sync']['state'] == 'complete'
    assert latest['sync']['request_id'] == 'same-keys'
    incoming.invalidate_proposals_list_cache()


def test_cached_read_preserves_an_existing_remote_error(monkeypatch):
    monkeypatch.setattr(sync, '_LAST_RESULT', {'state': 'complete'})
    incoming._store_proposals_list_cache({'proposals': [], 'counts': {}, 'tensology': {'error': 'Remote unavailable'},
                                          'sync': {'state': 'partial', 'error': 'Remote unavailable'}})
    assert incoming.list_incoming_proposals(refresh_local=False)['sync'] == {'state': 'partial', 'error': 'Remote unavailable'}
    incoming.invalidate_proposals_list_cache()


def test_dismissal_retry_survives_offline_and_later_refresh(client, monkeypatch):
    monkeypatch.setattr(sync, '_thread_receipts', lambda: [])
    monkeypatch.setattr(incoming, '_undismissed_proposals', lambda rows: [])
    client.post.side_effect = RuntimeError('Offline')
    rows, errors = sync.reconcile_decisions([remote()])
    assert errors and rows[0]['status'] == 'pending'
    client.post.side_effect = None
    client.post.return_value = {'status': 'dismissed'}
    rows, errors = sync.reconcile_decisions([remote()])
    assert not errors and rows[0]['status'] == 'dismissed'
    assert client.post.call_args.args[1] == {'action': 'dismiss'}


@pytest.mark.parametrize('reservation_reported', [False, True])
def test_offline_dismissal_reopens_only_the_reserved_proposal(monkeypatch, reservation_reported):
    from distr.core.db import IncomingProposalDismissal, get_session
    from distr.core.tensology_client import TensologyApiError

    row = {**remote(), 'id': 985018 if reservation_reported else 985019}
    row['source_conversation'] = {'thread_id': f'reservation-{row["id"]}'}
    proposal = incoming._from_tensology(row)
    alias = {**proposal, 'id': f'local:{proposal["dedupe_key"]}', 'origin': 'local'}
    later = {**proposal, 'id': f'local:gmail:later-{row["id"]}', 'message_ids': ['later-message']}
    client = Mock()
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: client)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', Mock())
    monkeypatch.setattr(sync, '_thread_receipts', lambda: [])
    client.post.side_effect = OSError('Offline')
    assert incoming.dismiss_proposal(proposal)
    assert incoming.dismiss_proposal(alias)
    assert incoming.dismiss_proposal(later)
    assert incoming._undismissed_proposals([proposal, later]) == []

    client.post.reset_mock()
    client.post.side_effect = TensologyApiError('Reserved for a merge', status=409, code='merge_source_reserved')
    if reservation_reported:
        row['merge_source_reserved'] = True
    rows, errors = sync.reconcile_decisions([row])
    assert not errors
    assert len(rows) == 1 and rows[0]['status'] == 'pending'
    assert incoming._undismissed_proposals([proposal, later]) == [proposal]
    sync.reconcile_decisions([row])
    assert client.post.call_count == (0 if reservation_reported else 1)
    with get_session() as db:
        assert db.get(IncomingProposalDismissal, proposal['id']) is None
        assert db.get(IncomingProposalDismissal, incoming._proposal_handoff_key(alias)) is None
        assert db.get(IncomingProposalDismissal, incoming._proposal_handoff_key(later)) is not None


def test_thread_receipt_retries_by_source_identity_without_starting_work_again(client, monkeypatch):
    proposal = incoming._from_tensology(remote(), {})
    proposal['id'] = 'local:gmail:thread'
    monkeypatch.setattr(sync, '_thread_receipts', lambda: [(936, {'key': 'legacy-key', 'proposal': proposal})])
    client.post.return_value = {'status': 'sent_to_decisions'}
    rows, errors = sync.reconcile_decisions([remote()])
    assert not errors and rows[0]['status'] == 'sent_to_decisions'
    assert client.post.call_args.args[1] == {'action': 'acknowledge_thread', 'thread_id': 936}


@pytest.mark.parametrize("damage", [None, "fingerprint", "missing_dismissal"])
def test_reconciliation_requires_original_merge_dismissal_evidence(client, monkeypatch, damage):
    from distr.core.db import IncomingProposalDismissal, get_session

    replacement = remote()
    replacement["requires_merge_receipt"] = True
    shaped = incoming._from_tensology(replacement)
    originals = [f"tensology:{wid}" for wid in {
        None: (985001, 985002), "fingerprint": (985003, 985004), "missing_dismissal": (985005, 985006),
    }[damage]]
    with get_session() as db:
        for key in originals[:1] if damage == "missing_dismissal" else originals:
            db.merge(IncomingProposalDismissal(key=key, conversation_key=key))
        db.commit()
    receipt = {
        "key": incoming._merge_key(originals),
        "original_keys": originals if damage != "fingerprint" else [originals[0], "tensology:985007"],
        "merged": {**shaped, "merged_from": []},
        "pending_originals": [],
    }
    monkeypatch.setattr(sync, "_thread_receipts", lambda: [
        (936, {"key": incoming._proposal_handoff_key(shaped), "merge_ack_ready": True}),
    ])
    monkeypatch.setattr(incoming, "_thread_merge_receipt", lambda *args: receipt)
    monkeypatch.setattr(incoming, "_undismissed_proposals", lambda rows: rows)
    monkeypatch.setattr("distr.core.incoming.project_links.proposal_in_scope", lambda proposal: True)
    client.post.return_value = {"status": "sent_to_decisions"}

    rows, errors = sync.reconcile_decisions([replacement])
    assert not errors
    if damage:
        assert rows[0]["status"] == "pending"
        client.post.assert_not_called()
    else:
        assert rows[0]["status"] == "sent_to_decisions"
        assert client.post.call_args.args[1] == {"action": "acknowledge_thread", "thread_id": 936}


def test_remote_decision_missed_while_offline_is_imported_without_write(client, monkeypatch):
    monkeypatch.setattr(sync, '_thread_receipts', lambda: [])
    record = Mock()
    monkeypatch.setattr(incoming, 'record_remote_proposal_decision', record)
    rows, errors = sync.reconcile_decisions([{**remote(), 'status': 'dismissed'}])
    assert not errors and rows[0]['status'] == 'dismissed'
    assert record.call_args.args[0]['request_id'] == 87
    client.post.assert_not_called()
