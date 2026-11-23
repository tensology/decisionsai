"""Incoming Start Work retries preserve the canonical Development thread."""
import json
from unittest.mock import Mock

import pytest

from distr.core.chat import _chat_params
from distr.core.db import Chat, get_session
from distr.core.db.projects import Project
from distr.core.incoming import proposal_sync, proposals
from distr.core.kanban.proposal_tickets import bind_proposal_thread_ticket
from distr.core.workflow.development_threads import ensure_development_thread


@pytest.fixture
def launch_case(monkeypatch, tmp_path):
    with get_session() as db:
        project = Project(name='Proposal launch retry', folder_location=str(tmp_path))
        db.add(project)
        db.flush()
        project_id = project.id
        project_name = project.name
    remote = {
        'id': 980000 + project_id, 'status': 'pending', 'attachments': [],
        'project': {'id': 123, 'decisions_project_id': project_id, 'name': project_name},
        'source': 'gmail', 'source_conversation': {'thread_id': f'launch-{project_id}'},
        'source_message_ids': ['m1'], 'title': 'Retry a proposal launch',
    }
    client = Mock()
    client.get.return_value = remote
    client.post.return_value = {'status': 'sent_to_decisions'}
    dispatch = Mock(return_value={'started': True, 'status': 'running'})
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: client)
    monkeypatch.setattr('distr.core.incoming.project_links.proposal_in_scope', lambda *args, **kwargs: True)
    monkeypatch.setattr('distr.core.incoming.media.ensure_proposal_attachments', Mock(return_value={'attachments': []}))
    monkeypatch.setattr('distr.core.incoming.transcription.prepare_thread_transcripts', Mock())
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', Mock())
    monkeypatch.setattr('distr.core.automation.model_route.automation_global_default', lambda settings: {})
    monkeypatch.setattr('distr.core.workflow.development_harness.dispatch_development_prompt', dispatch)
    return remote, client, dispatch


def _receipt(chat_id):
    with get_session() as db:
        return _chat_params(db.get(Chat, chat_id).params)['incoming_proposal_handoff']


def _saved_thread(proposal, *, started=False, start_pending=False, status=None):
    chat_id = ensure_development_thread(
        title=proposal['title'], starting_question=proposal['title'], project_id=proposal['project_id'],
        source_type='incoming_proposal', source_ref=proposals._proposal_handoff_key(proposal),
    )
    bind_proposal_thread_ticket(chat_id, proposal, proposal['project_id'])
    proposals._record_proposal_handoff(
        chat_id, proposal, project_id=proposal['project_id'], started=started, start_pending=start_pending,
    )
    if status is not None:
        with get_session() as db:
            chat = db.get(Chat, chat_id)
            params = _chat_params(chat.params)
            params.setdefault('development', {})['execution'] = {'status': status}
            chat.params = json.dumps(params)
    return chat_id


@pytest.mark.parametrize('result', [
    {'started': False}, {'status': 'failed'}, {'status': 'cancelled'}, {'status': 'blocked'},
])
def test_failed_dispatch_clears_pending_receipt(launch_case, result):
    remote, client, dispatch = launch_case
    proposal = proposals._from_tensology(remote)

    def fail(chat_id, *args, **kwargs):
        assert _receipt(chat_id)['start_pending'] is True
        return {**result, 'error': 'Launch refused'}

    dispatch.side_effect = fail
    with pytest.raises(ValueError, match='^Launch refused$'):
        proposals.create_proposal_thread(proposal, start_work=True)

    receipt = _receipt(dispatch.call_args.args[0])
    assert 'start_pending' not in receipt
    assert receipt['started'] is False
    client.post.assert_not_called()


def test_raising_dispatch_clears_pending_receipt(launch_case):
    remote, client, dispatch = launch_case
    proposal = proposals._from_tensology(remote)
    error = RuntimeError('Dispatch unavailable')
    dispatch.side_effect = error

    with pytest.raises(RuntimeError) as raised:
        proposals.create_proposal_thread(proposal, start_work=True)

    assert raised.value is error
    receipt = _receipt(dispatch.call_args.args[0])
    assert 'start_pending' not in receipt
    assert receipt['started'] is False
    client.post.assert_not_called()


def test_reconcile_skips_pending_launch_and_acknowledges_started_thread(launch_case):
    pending, client, _ = launch_case
    started = {
        **pending, 'id': pending['id'] + 10000,
        'source_conversation': {'thread_id': 'already-launched'},
    }
    _saved_thread(proposals._from_tensology(pending), start_pending=True)
    started_chat = _saved_thread(proposals._from_tensology(started), started=True)

    rows, errors = proposal_sync.reconcile_decisions([pending, started])

    assert not errors
    assert [row['status'] for row in rows] == ['pending', 'sent_to_decisions']
    client.post.assert_called_once_with(
        f"work-requests/{started['id']}/action", {'action': 'acknowledge_thread', 'thread_id': started_chat},
        idempotency_key=f"proposal-decision-{started['id']}-acknowledge_thread-{started_chat}",
    )


@pytest.mark.parametrize('status', [None, 'failed', 'blocked', 'cancelled'])
def test_start_work_retries_in_acknowledged_thread(launch_case, status):
    remote, _, dispatch = launch_case
    remote['status'] = 'sent_to_decisions'
    proposal = proposals._from_tensology(remote)
    chat_id = _saved_thread(proposal, started=status is not None, start_pending=status is None, status=status)

    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id

    dispatch.assert_called_once()
    assert dispatch.call_args.args[0] == chat_id
    assert _receipt(chat_id)['started'] is True
    assert 'start_pending' not in _receipt(chat_id)


def test_start_work_reuses_acknowledged_started_thread_without_dispatch(launch_case):
    remote, client, dispatch = launch_case
    remote['status'] = 'sent_to_decisions'
    proposal = proposals._from_tensology(remote)
    chat_id = _saved_thread(proposal, started=True, status='running')

    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id

    dispatch.assert_not_called()
    client.post.assert_not_called()


def test_reconcile_skips_failed_launch_receipt(launch_case):
    pending, client, _ = launch_case
    proposal = proposals._from_tensology(pending)
    chat_id = _saved_thread(proposal)
    proposals._record_proposal_handoff(chat_id, proposal, project_id=proposal['project_id'], start_failed=True)
    assert _receipt(chat_id)['start_failed'] is True

    rows, errors = proposal_sync.reconcile_decisions([pending])

    assert not errors
    assert [row['status'] for row in rows] == ['pending']
    client.post.assert_not_called()


def test_start_work_keeps_refusing_an_unstarted_thread_without_a_failed_launch(launch_case):
    remote, _, dispatch = launch_case
    remote['status'] = 'sent_to_decisions'
    proposal = proposals._from_tensology(remote)
    _saved_thread(proposal)

    with pytest.raises(ValueError, match='no longer pending'):
        proposals.create_proposal_thread(proposal, start_work=True)
    dispatch.assert_not_called()


def test_start_work_retries_after_a_recorded_failed_launch(launch_case):
    remote, _, dispatch = launch_case
    remote['status'] = 'sent_to_decisions'
    proposal = proposals._from_tensology(remote)
    chat_id = _saved_thread(proposal)
    proposals._record_proposal_handoff(chat_id, proposal, project_id=proposal['project_id'], start_failed=True)

    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id
    dispatch.assert_called_once()
    assert 'start_failed' not in _receipt(chat_id)
