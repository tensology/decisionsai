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
