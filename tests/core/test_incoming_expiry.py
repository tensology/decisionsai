"""Evidence-based expiry indicators, durable snapshots and dismissal races."""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from distr.core.incoming import proposals
from distr.core.incoming.expiry import proposal_expiry, proposal_source_started_at, proposal_source_order_key

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def proposal(**extra):
    return {'id': 'local:expiry-fixture', 'origin': 'local', 'source': 'gmail',
            'source_thread_id': 'expiry-fixture', 'dedupe_key': 'gmail:expiry-fixture',
            'title': 'Review a source request', 'project_id': 7, 'project_name': 'DecisionsAI',
            'status': 'candidate', 'message_ids': ['a', 'b'],
            'evidence': [{'id': 'a', 'sent_at': (NOW - timedelta(hours=120)).isoformat()},
                         {'id': 'b', 'sent_at': (NOW - timedelta(hours=96)).isoformat()}], **extra}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    from distr.core.db import IncomingProposalDismissal, IncomingProposalRestoration, get_session
    with get_session() as db:
        db.query(IncomingProposalDismissal).delete()
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    monkeypatch.setattr(proposals, '_TRIAGE_CACHE', {'at': 0.0, 'proposals': [], 'running': False})
    monkeypatch.setattr(proposals, '_RESTORED_SNAPSHOTS', {})
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kw: None)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *a, **kw: {})
    proposals.invalidate_proposals_list_cache()
    yield
    proposals.invalidate_proposals_list_cache()


def test_exact_96_hour_boundary_uses_newest_referenced_message_only():
    value = proposal()
    assert proposal_expiry(value, now=NOW - timedelta(microseconds=1))['is_expired'] is False
    expired = proposal_expiry(value, now=NOW)
    assert expired['is_expired'] is True and expired['status'] == 'candidate'
    assert expired['expires_at'] == NOW.isoformat()
    assert expired['source_message_at'] == (NOW - timedelta(hours=96)).isoformat()
    value['created_at'] = NOW.isoformat()
    value['latest_activity'] = NOW.isoformat()
    value['evidence'].append({'id': 'unrelated', 'sent_at': NOW.isoformat()})
    assert proposal_expiry(value, now=NOW)['is_expired'] is True


def test_source_collection_starts_at_earliest_exact_evidence_not_proposal_creation():
    value = proposal(created_at=NOW.isoformat(), latest_activity=NOW.isoformat())
    value['evidence'].extend([
        {'id': 'unrelated-older-chat', 'sent_at': '2000-01-01T00:00:00Z'},
        {'id': 'unrelated-newer-chat', 'sent_at': NOW.isoformat()},
    ])
    assert proposal_source_started_at(value) == NOW - timedelta(hours=120)
    assert proposal_expiry(value, now=NOW)['source_message_at'] == (NOW - timedelta(hours=96)).isoformat()
    assert proposal_expiry(value, now=NOW)['expires_at'] == NOW.isoformat()


def test_source_start_compares_actual_instants_across_offsets_and_provider_epochs():
    value = proposal(evidence=[
        {'id': 'a', 'sent_at': '2026-05-15T01:00:00+02:00'},
        {'id': 'b', 'sent_at': datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp() * 1000},
    ])
    assert proposal_source_started_at(value) == datetime(2026, 5, 14, 23, tzinfo=timezone.utc)


def test_source_start_stays_unknown_when_referenced_dates_are_missing_or_invalid():
    value = proposal(created_at=NOW.isoformat(), evidence=[
        {'id': 'a', 'sent_at': 'invalid'}, {'id': 'b', 'sent_at': None},
        {'id': 'not-referenced', 'sent_at': '2026-05-15T00:00:00Z'},
    ])
    assert proposal_source_started_at(value) is None


def test_cached_api_exposes_source_collection_start_without_changing_latest_expiry():
    value = proposal(created_at=NOW.isoformat())
    proposals._store_proposals_list_cache({'proposals': [value], 'counts': {'gmail': 1}})
    result = proposals.list_incoming_proposals()['proposals'][0]
    assert result['source_started_at'] == (NOW - timedelta(hours=120)).isoformat()
    assert result['source_message_at'] == (NOW - timedelta(hours=96)).isoformat()
    assert result['expires_at'] == NOW.isoformat()


def test_source_start_can_use_authoritative_source_metadata_without_evidence_payload():
    value = proposal(evidence=[], source_started_at='2026-05-15T00:00:00Z', created_at=NOW.isoformat())
    assert proposal_source_started_at(value) == datetime(2026, 5, 15, tzinfo=timezone.utc)


def source_order_rows():
    return [
        proposal(id='may', dedupe_key='gmail:may', created_at='2030-01-01T00:00:00Z', evidence=[
            {'id': 'b', 'sent_at': '2026-11-05T00:00:00Z'},
            {'id': 'a', 'sent_at': '2026-05-15T00:00:00Z'},
            {'id': 'unrelated', 'sent_at': '2020-01-01T00:00:00Z'},
        ]),
        proposal(id='unknown', dedupe_key='gmail:unknown', created_at='2035-01-01T00:00:00Z', evidence=[]),
        proposal(id='june', dedupe_key='gmail:june', created_at='2020-01-01T00:00:00Z', evidence=[
            {'id': 'a', 'sent_at': '2026-06-01T00:00:00Z'},
        ]),
        proposal(id='november', dedupe_key='gmail:november', evidence=[
            {'id': 'a', 'sent_at': '2026-11-01T00:00:00Z'},
        ]),
    ]


def test_newest_collections_first_uses_their_start_not_generation_or_latest_followup():
    rows = source_order_rows()
    assert [row['id'] for row in sorted(rows, key=proposal_source_order_key)] == ['november', 'june', 'may', 'unknown']
    # May's later follow-up still determines its expiry, but cannot promote it.
    assert proposal_expiry(rows[0], now=NOW)['source_message_at'] == '2026-11-05T00:00:00+00:00'


def test_collection_order_uses_actual_timezone_instants_and_stable_ids_for_ties():
    rows = [proposal(id='z', evidence=[{'id': 'a', 'sent_at': '2026-05-15T01:00:00+02:00'}]),
            proposal(id='b', evidence=[{'id': 'a', 'sent_at': '2026-05-15T00:00:00Z'}]),
            proposal(id='a', evidence=[{'id': 'a', 'sent_at': '2026-05-15T02:00:00+02:00'}])]
    assert [row['id'] for row in sorted(rows, key=proposal_source_order_key)] == ['a', 'b', 'z']


def test_cached_payload_always_reorders_old_cached_sequences_by_source_start():
    proposals._store_proposals_list_cache({'proposals': source_order_rows(), 'counts': {'gmail': 4}})
    result = proposals.list_incoming_proposals()
    assert [row['id'] for row in result['proposals']] == ['november', 'june', 'may', 'unknown']
    assert result['counts'] == {'gmail': 4}


def test_fresh_list_uses_same_source_order_without_launching_triage(monkeypatch):
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', lambda **kwargs: [])
    monkeypatch.setattr(proposals, '_saved_local_proposals', lambda: [])
    monkeypatch.setattr(proposals, '_record_local_proposals', lambda rows: None)
    monkeypatch.setattr(proposals, 'apply_project_corrections', lambda rows: rows)
    proposals._TRIAGE_CACHE['proposals'] = source_order_rows()
    result = proposals.list_incoming_proposals(refresh_local=False)
    assert [row['id'] for row in result['proposals']] == ['november', 'june', 'may', 'unknown']


@pytest.mark.parametrize('status', ['accepted', 'approved', 'queued', 'sent_to_decisions', 'dismissed'])
def test_terminal_decisions_keep_their_status(status):
    value = proposal(status=status)
    result = proposal_expiry(value, now=NOW)
    assert result['status'] == status and result['is_expired'] is False


def test_missing_source_dates_never_use_creation_or_conversation_activity():
    value = proposal(evidence=[], created_at='2000-01-01T00:00:00Z', updated_at=NOW.isoformat())
    assert proposal_expiry(value, now=NOW) == {**value, 'source_message_at': None, 'expires_at': None, 'is_expired': False}


def test_cached_list_crosses_deadline_without_source_refetch(monkeypatch):
    instant = [NOW - timedelta(seconds=1)]
    original = proposal_expiry
    monkeypatch.setattr(proposals, 'proposal_expiry', lambda row: original(row, now=instant[0]))
    fetch = Mock(side_effect=AssertionError('Cached list must not refetch upstream'))
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', fetch)
    proposals._store_proposals_list_cache({'proposals': [proposal()], 'counts': {'gmail': 1}})
    assert proposals.list_incoming_proposals()['proposals'][0]['is_expired'] is False
    instant[0] = NOW
    listed = proposals.list_incoming_proposals()
    assert listed['proposals'][0]['is_expired'] is True
    assert listed['counts'] == {'gmail': 1}
    fetch.assert_not_called()


def test_late_cache_rebuild_cannot_resurrect_a_dismissal():
    value = proposal()
    proposals.dismiss_proposal(value)
    # A list build that started before the dismissal finishes afterward.
    proposals._store_proposals_list_cache({'proposals': [value], 'counts': {'gmail': 1}})
    assert proposals.list_incoming_proposals()['proposals'] == []


def test_regenerated_upstream_id_with_same_covered_evidence_stays_dismissed():
    original = proposal(id='tensology:555', origin='tensology')
    proposals.dismiss_proposal(original)
    regenerated = {**original, 'id': 'tensology:556', 'project_id': 99}
    assert proposals._undismissed_proposals([regenerated]) == []
    new_work = {**regenerated, 'message_ids': ['new-source-message']}
    assert proposals._undismissed_proposals([new_work]) == [new_work]


def test_expiry_indicator_does_not_add_an_auto_accept_restriction(monkeypatch):
    value = proposal()
    monkeypatch.setattr(proposals, 'proposal_expiry', lambda row: proposal_expiry(row, now=NOW))
    proposals._TRIAGE_CACHE['proposals'] = [value]
    assert proposal_expiry(value, now=NOW)['is_expired'] is True
    assert proposals.auto_accept_skip_reason(value, {}) == ''


def test_indicator_preserves_action_capabilities_and_pending_status():
    value = proposal(status='pending', can_push_to_thread=True)
    expired = proposal_expiry(value, now=NOW)
    assert expired['can_push_to_thread'] is True and expired['status'] == 'pending'


def test_canonical_terminal_coverage_filters_local_before_model(monkeypatch):
    row = {'id': 558, 'source': 'gmail', 'status': 'dismissed', 'source_message_ids': ['already-decided'],
           'project': {'name': 'Another project'}, 'source_conversation': {'thread_id': 'same-source'}}
    fetch = Mock(return_value=[row])
    monkeypatch.setattr('distr.core.tensology_client.fetch_pending_work_requests', fetch)
    monkeypatch.setattr(proposals, '_project_map', lambda: {})
    items = [{'source': 'gmail', 'source_thread_id': 'same-source', 'source_message_id': 'already-decided'},
             {'source': 'gmail', 'source_thread_id': 'same-source', 'source_message_id': 'new-request'}]
    monkeypatch.setattr('distr.core.incoming.service.list_development_incoming', lambda **kw: {'items': items})
    triage = Mock(return_value=[])
    monkeypatch.setattr(proposals, '_local_proposals', triage)
    assert proposals.list_incoming_proposals(force=True)['proposals'] == []
    assert triage.call_args.args[0] == [items[1]]
    fetch.assert_called_once_with(include_decided=True)


def test_expiry_does_not_remove_context_before_triage(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(proposals, '_triage_model', lambda settings: ('fixture', 'fixture'))
    monkeypatch.setattr(proposals, '_candidate_projects', lambda: [{'id': 7, 'name': 'Fixture'}])
    old = {'source': 'gmail', 'source_thread_id': 'context', 'source_message_id': 'historical',
           'created_at': (now - timedelta(hours=100)).isoformat(), 'text': 'Please fix old request', 'sender': 'Client'}
    fresh = {**old, 'source_message_id': 'fresh', 'created_at': now.isoformat(), 'text': 'Please review new requirement'}
    complete = Mock(return_value='{"is_actionable":false}')
    proposals._triage_conversations([old, fresh], {}, complete=complete)
    assert 'historical' in complete.call_args.args[2][1]['content']


def test_first_local_snapshot_survives_a_fresh_process_and_dismissal_remains_authoritative():
    import json
    import subprocess
    import sys
    value = proposal()
    proposals._record_local_proposals([value])
    # Same work may be regenerated with a changed creation date or wording.
    proposals._record_local_proposals([{**value, 'created_at': NOW.isoformat(), 'title': 'Changed wording',
                                       'evidence': [{'id': 'b', 'sent_at': NOW.isoformat()}]}])
    saved = next(row for row in proposals._saved_local_proposals() if row['id'] == value['id'])
    assert saved['title'] == value['title']
    assert proposal_expiry(saved, now=NOW)['expires_at'] == NOW.isoformat()
    script = (
        'import json,sys; from datetime import datetime; '
        'from distr.core.incoming.proposals import _saved_local_proposals,_undismissed_proposals; '
        'from distr.core.incoming.expiry import proposal_expiry; '
        'rows=[row for row in _saved_local_proposals() if row["id"]==sys.argv[1]]; '
        'assert len(rows)<=1; '
        'assert all(proposal_expiry(row,now=datetime.fromisoformat(sys.argv[2]))["is_expired"] for row in rows); '
        'print(len(_undismissed_proposals(rows)))'
    )
    first = subprocess.run([sys.executable, '-c', script, value['id'], NOW.isoformat()], capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip().endswith('1')
    proposals.dismiss_proposal(value)
    after = subprocess.run([sys.executable, '-c', script, value['id'], NOW.isoformat()], capture_output=True, text=True, timeout=15)
    assert after.returncode == 0, after.stderr
    assert after.stdout.strip().endswith('0')


def test_whatsapp_provider_clock_never_falls_back_to_import_date(monkeypatch):
    from distr.core.db import WhatsAppMessage, get_session
    from distr.core.incoming.service import list_development_incoming
    monkeypatch.setattr('distr.core.work_intake.get_work_intake_service', lambda: type('EmptyIntake', (), {'list_inbox': lambda self, limit: []})())
    monkeypatch.setattr('distr.core.incoming.media.list_proposal_attachment_manifest', lambda row: [])
    thread = 'provider-clock-fixture@g.us'
    with get_session() as db:
        db.add(WhatsAppMessage(message_id='provider-missing', jid=thread, text='Missing original timestamp',
                              whatsapp_timestamp=None, created_date=NOW.replace(tzinfo=None)))
        db.add(WhatsAppMessage(message_id='provider-old', jid=thread, text='Original old request',
                              whatsapp_timestamp=int((NOW - timedelta(hours=100)).timestamp()), created_date=NOW.replace(tzinfo=None)))
        db.add(WhatsAppMessage(message_id='provider-unrelated', jid=thread, text='Unrelated later chat',
                              whatsapp_timestamp=int(NOW.timestamp()), created_date=NOW.replace(tzinfo=None)))
        db.commit()
    items = {row['source_message_id']: row for row in list_development_incoming(limit=5000, network=False)['items']
             if row.get('source_thread_id') == thread}
    assert items['provider-missing']['created_at']
    assert items['provider-missing']['source_sent_at'] == ''
    missing = proposals._proposal_from_triage({'source': 'whatsapp', 'thread_id': thread, 'participant': 'Fixture',
                                              'items': [items['provider-missing']]}, {'id': 7, 'name': 'Fixture'}, {'title': 'Missing date'})
    assert missing['evidence'][0]['sent_at'] == ''
    assert proposal_expiry(missing, now=NOW)['source_message_at'] is None
    legacy = {'origin': 'local', 'source': 'whatsapp', 'source_thread_id': thread, 'message_ids': ['provider-old']}
    hydrated = proposals._with_source_dates(legacy)
    projected = proposal_expiry(hydrated, now=NOW)
    assert projected['source_message_at'] == (NOW - timedelta(hours=100)).isoformat()
    assert projected['expires_at'] == (NOW - timedelta(hours=4)).isoformat()
    assert projected['is_expired'] is True
