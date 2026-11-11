"""Proposal boundary regressions. Providers, models and execution stay stubbed."""
import json
from unittest.mock import Mock

import pytest

from distr.core.incoming import proposals as proposals


@pytest.fixture(autouse=True)
def isolate_edge_cache(monkeypatch):
    from distr.core.db import IncomingProposalRestoration, get_session
    monkeypatch.setattr(proposals, '_RESTORED_SNAPSHOTS', {})
    monkeypatch.setattr(proposals, '_TRIAGE_CACHE', {'at':0, 'proposals':[], 'running':False})
    proposals.invalidate_proposals_list_cache()
    yield
    with get_session() as db:
        db.query(IncomingProposalRestoration).delete()
        db.commit()
    proposals.invalidate_proposals_list_cache()


@pytest.fixture
def merge_case(monkeypatch, tmp_path):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    with get_session() as db:
        project = Project(name='Edge sandbox', folder_location=str(tmp_path))
        db.add(project)
        db.commit()
        pid = project.id
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *args, **kwargs: True)
    monkeypatch.setattr('distr.core.incoming.project_links.source_project_index', lambda **kwargs: {})
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args: [])
    monkeypatch.setattr('distr.core.incoming.transcription.prepare_thread_transcripts', lambda *args: None)
    # No model, notices, workflow execution or provider call may escape fixtures.
    monkeypatch.setattr('distr.core.tensology_notify.notify_proposal_created', Mock(side_effect=AssertionError('unexpected notice')))
    monkeypatch.setattr('distr.core.workflow.development_harness.dispatch_development_prompt', Mock(side_effect=AssertionError('unexpected dispatch')))
    a = {'id':f'local:gmail:edge-{pid}-a', 'origin':'local', 'source':'gmail',
         'source_thread_id':f'edge-{pid}-a', 'project_id':pid, 'project_name':'Edge sandbox',
         'title':'First request', 'description':'Do first work', 'tasks':['First acceptance'],
         'message_ids':[f'{pid}-a1'], 'dedupe_key':f'gmail:edge-{pid}-a'}
    b = {**a, 'id':f'local:gmail:edge-{pid}-b', 'source_thread_id':f'edge-{pid}-b',
         'title':'Second request', 'message_ids':[f'{pid}-b1'], 'dedupe_key':f'gmail:edge-{pid}-b'}
    return a, b


def merge(a, b):
    return proposals.merge_proposals(a, b, complete=lambda *args: '{}')


def test_merge_prepares_development_identity_reply_sources_and_replays(merge_case):
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices
    a, b = merge_case
    result = merge(a, b)
    # Reverse order and discard transient caches to simulate a later request.
    proposals.invalidate_proposals_list_cache()
    assert merge(b, a)['chat_id'] == result['chat_id']
    with get_session() as db:
        assert db.query(DevelopmentWorkItem).filter_by(chat_id=result['chat_id']).count() == 1
        params = json.loads(db.get(Chat, result['chat_id']).params)
        assert params['incoming_proposal_handoff']['started'] is False
        assert params['incoming_merge']['pending_originals'] == []
    assert {row['thread_id'] for row in source_choices(proposal_for_thread(result['chat_id']))} == {a['source_thread_id'], b['source_thread_id']}


def _remote_pair(monkeypatch, pair, ids=(990101, 990102)):
    a, b = pair
    canonical = {}
    for wid, row in zip(ids, pair):
        canonical[f'work-requests/{wid}'] = {
            'id':wid, 'title':row['title'], 'description':row['description'], 'tasks':row['tasks'],
            'status':'pending', 'source':'gmail', 'project':{'id':80, 'decisions_project_id':row['project_id'], 'name':row['project_name']},
            'source_conversation':{'thread_id':row['source_thread_id']}, 'source_message_ids':row['message_ids'], 'attachments':[]}
    client = Mock()
    client.get.side_effect = lambda path: canonical[path]
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs:client)
    return {**a, 'id': f'tensology:{ids[0]}'}, {**b, 'id': f'tensology:{ids[1]}'}


def test_replacement_failure_preserves_originals(monkeypatch, merge_case):
    pair = _remote_pair(monkeypatch, merge_case)
    dismiss = Mock()
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', dismiss)
    monkeypatch.setattr('distr.core.tensology_client.create_merged_work_request', Mock(side_effect=RuntimeError('upstream unavailable')))
    with pytest.raises(RuntimeError, match='upstream unavailable'):
        merge(*pair)
    dismiss.assert_not_called()
    assert proposals._undismissed_proposals(list(pair)) == list(pair)


def test_partial_merge_dismissal_retries_same_replacement_and_thread(monkeypatch, merge_case):
    pair = _remote_pair(monkeypatch, merge_case)
    replacement = Mock(return_value={'id':990103})
    monkeypatch.setattr('distr.core.tensology_client.create_merged_work_request', replacement)
    dismiss = Mock(side_effect=RuntimeError('dismiss temporarily unavailable'))
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', dismiss)
    with pytest.raises(RuntimeError, match='dismiss temporarily unavailable'):
        merge(*pair)
    dismiss.side_effect = None
    result = merge(*pair)
    assert result['merged']['id'] == 'tensology:990103'
    assert replacement.call_count == 1
    assert merge(*pair)['chat_id'] == result['chat_id']
    assert replacement.call_count == 1


def test_starting_remote_merge_preserves_original_reply_destinations(monkeypatch, merge_case):
    from distr.core.incoming.source_reply import proposal_for_thread, source_choices
    from distr.core.workflow import development_harness
    from distr.core import tensology_client

    a, b = merge_case
    pair = _remote_pair(monkeypatch, merge_case, ids=(990106, 990107))
    client = tensology_client.configured_tensology_client()
    get_original = client.get.side_effect
    client.get.side_effect = lambda path: get_original(path) if path != 'work-requests/990105' else {
        **get_original('work-requests/990106'), 'id': 990105,
        'title': 'Updated replacement brief', 'source_message_ids': a['message_ids'] + b['message_ids'],
    }
    monkeypatch.setattr(tensology_client, 'create_merged_work_request', Mock(return_value={'id': 990105}))
    monkeypatch.setattr(tensology_client, 'decide_work_request', Mock())
    monkeypatch.setattr(development_harness, 'development_execution_active', lambda *_: False)
    monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda *_: {})
    dispatch = Mock(return_value={'status': 'running'})
    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', dispatch)

    failure = {'name': 'missing.pdf', 'error': 'original source offline'}
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args: [failure])
    result = merge(*pair)
    chat_id = proposals.create_proposal_thread(result['merged'], start_work=True)
    assert chat_id == result['chat_id']
    assert dispatch.call_args.args[1].startswith('Updated replacement brief')
    assert {row['thread_id']: row['message_ids'] for row in source_choices(proposal_for_thread(chat_id))} == {
        a['source_thread_id']: a['message_ids'], b['source_thread_id']: b['message_ids'],
    }
    assert proposals.proposal_thread_attachment_report(chat_id)['failed_attachments'] == [failure]


def test_merge_persists_missing_attachment_failure(monkeypatch, merge_case):
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args:[{'name':'missing.pdf', 'error':'offline'}])
    result = merge(*merge_case)
    assert proposals.proposal_thread_attachment_report(result['chat_id'])['failed_attachments'] == [{'name':'missing.pdf', 'error':'offline'}]
    assert merge(*merge_case)['merged']['attachments'][0]['error'] == 'offline'


def test_recovered_remote_attachment_clears_warning_only_with_matching_evidence():
    recovered_link = {'name': 'report.pdf', 'api_path': 'source-1'}
    recovered_digest = {'name': 'report.pdf', 'api_path': 'source-2', 'sha256': 'found'}
    wrong_digest = {'name': 'report.pdf', 'api_path': 'source-1', 'sha256': 'missing'}
    unrelated = {'name': 'report.pdf'}
    merge = {'file_failures': [recovered_link, recovered_digest, wrong_digest, unrelated]}
    files = [{'name': 'report.pdf', 'path': '/managed/file', 'api_path': 'source-1', 'sha256': 'found'}]

    assert proposals._unresolved_merge_file_failures(merge, files) == [wrong_digest, unrelated]


def test_starting_merged_proposal_preserves_recorded_attachment_failure(monkeypatch, merge_case):
    from distr.core.workflow import development_harness

    failure = {'name': 'missing.pdf', 'error': 'original source offline'}
    monkeypatch.setattr(proposals, '_merge_proposal_attachments', lambda *args: [failure])
    result = merge(*merge_case)
    chat_id = result['chat_id']
    monkeypatch.setattr(development_harness, 'development_execution_active', lambda *_: False)
    monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda *_: {})
    dispatch = Mock(return_value={'status': 'running'})
    monkeypatch.setattr(development_harness, 'dispatch_development_prompt', dispatch)

    assert proposals.create_proposal_thread(result['merged'], start_work=True) == chat_id
    dispatch.assert_called_once()
    assert proposals.proposal_thread_attachment_report(chat_id)['failed_attachments'] == [failure]


def test_merge_reconciles_recovered_attachment_without_hiding_distinct_failure(monkeypatch):
    first = {'source': 'whatsapp', 'source_thread_id': 'same-chat'}
    second = {**first, 'id': 'second'}
    failure = {'name': 'report.pdf', 'source_message_id': 'message-1', 'error': 'offline'}
    distinct = {**failure, 'source_message_id': 'message-2'}
    recovered = {'name': 'report.pdf', 'source_message_id': 'message-1',
                 'path': '/managed/report.pdf', 'sha256': 'verified', 'size': 10}
    ensure = Mock(side_effect=[
        {'attachments': [], 'failures': [failure, distinct]},
        {'attachments': [recovered], 'failures': []},
    ])
    monkeypatch.setattr('distr.core.incoming.media.ensure_proposal_attachments', ensure)
    monkeypatch.setattr('distr.core.incoming.transcription.with_transcript', lambda row: row)

    assert proposals._merge_proposal_attachments(first, second, 1) == [distinct, recovered]


@pytest.mark.parametrize('status', ['dismissed', 'sent_to_decisions', 'approved', None])
def test_dismissed_authoritative_proposal_never_dispatches(monkeypatch, merge_case, status):
    a = merge_case[0]
    client = Mock()
    client.get.return_value = {'id':990104, 'status':status, 'attachments':[],
                              'project': {'id': 123, 'decisions_project_id': a['project_id'], 'name': a['project_name']}}
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs:client)
    with pytest.raises(ValueError, match='no longer pending'):
        proposals.create_proposal_thread({**a, 'id':'tensology:990104'}, start_work=True)


@pytest.mark.parametrize('text', ['Thanks, add a dark mode toggle.', 'Great, update the logo.', 'Please fix the failing E2E tests.', 'Please test E2E login.'])
def test_normal_requests_survive_prefilter(text):
    assert proposals._worth_triaging({'items':[{'text':text, 'sender':'Client', 'direction':'inbound'}], 'label':'Work'})


@pytest.mark.parametrize('text', ['Thanks!', '👍', 'Simulated E2E test please fix fixture'])
def test_acknowledgements_and_synthetic_messages_stay_filtered(text):
    assert not proposals._worth_triaging({'items':[{'text':text, 'sender':'Client', 'direction':'inbound'}], 'label':'Work'})


def test_full_durable_brief_survives_storage_and_restore(merge_case):
    a = merge_case[0]
    brief = {**a, 'description':'Detailed requirement. '*800,
             'tasks':[{'title':f'Task {index}', 'acceptance_criteria':'Required acceptance. '*250} for index in range(220)],
             'evidence':[{'id':str(index), 'body':'Original request paragraph. '*250} for index in range(220)]}
    proposals._record_local_proposals([brief])
    restored = next(row for row in proposals._saved_local_proposals() if row['id'] == a['id'])
    for field in ('description', 'tasks', 'evidence'):
        assert restored[field] == brief[field]
    proposals._persist_proposal_dismissal(brief)
    restored = proposals.undismiss_proposal(proposals._proposal_handoff_key(brief))['proposal']
    for field in ('description', 'tasks', 'evidence'):
        assert restored[field] == brief[field]
    assert len(proposals.proposal_snapshot_json(brief).encode()) <= 20000


@pytest.mark.parametrize('payload', [
    {'estimated_amount':5000}, {'evidence':[{'transcript':'Deploy to production'}]},
    {'attachments':[{'caption':'delete the production database'}]},
    {'context_evidence':[{'body':'Buy a paid subscription'}]},
])
def test_auto_accept_checks_all_retained_risk_fields(payload):
    assert proposals.auto_accept_skip_reason({'origin':'local', 'project_id':9, 'project_name':'DecisionsAI', 'title':'Update app', **payload}, {})


def test_canonical_context_remains_context_only():
    row = proposals._from_tensology({'id':990105, 'context_evidence':[{'id':'old', 'body':'Old request'}]})
    assert row['evidence'][0]['context_only'] is True


def test_synthetic_merged_source_authorizes_every_original():
    from distr.core.incoming.project_links import proposal_in_scope
    proposal = {'project_id':9, 'source':'whatsapp', 'source_thread_id':'merged:99',
                'source_scope':[{'source':'whatsapp', 'source_thread_id':'first'}, {'source':'gmail', 'source_thread_id':'second'}]}
    index = {('whatsapp','first'):{9}, ('gmail','second'):{9}}
    assert proposal_in_scope(proposal, index)
    assert not proposal_in_scope(proposal, {('whatsapp','first'):{9}})
    assert not proposal_in_scope(proposal, {('whatsapp','first'):{9}, ('gmail','second'):{10}})
    assert not proposal_in_scope({**proposal, 'source_scope':[]}, {('whatsapp','merged:99'):{9}})
    hidden = {**proposal, 'source_scope':proposal['source_scope'][:1],
              'evidence':[{'id':'other', 'original_source':{'channel':'gmail', 'source_thread_id':'unlinked'}}]}
    assert not proposal_in_scope(hidden, index)


def test_mixed_merge_idempotency_includes_local_source_and_replays(monkeypatch, merge_case):
    remote, _ = _remote_pair(monkeypatch, merge_case)
    local = merge_case[1]
    replacement = Mock(side_effect=[{'id':990201}, {'id':990202}])
    monkeypatch.setattr('distr.core.tensology_client.create_merged_work_request', replacement)
    monkeypatch.setattr('distr.core.tensology_client.decide_work_request', lambda *args: {})
    first = merge(remote, local)
    assert merge(local, remote)['chat_id'] == first['chat_id']
    other = {**local, 'id':local['id'] + '-next', 'message_ids':['new-source-message']}
    second = merge(remote, other)
    assert first['chat_id'] != second['chat_id']
    assert replacement.call_count == 2
    assert replacement.call_args_list[0].kwargs['idempotency_key'] != replacement.call_args_list[1].kwargs['idempotency_key']


def test_merge_transport_preserves_full_idempotency_key(monkeypatch):
    from distr.core.tensology_client import create_merged_work_request
    client = Mock()
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs:client)
    key = 'merge:' + 'a' * 64
    create_merged_work_request({'merged_from':[123]}, idempotency_key=key)
    assert client.post.call_args.kwargs['idempotency_key'] == key
