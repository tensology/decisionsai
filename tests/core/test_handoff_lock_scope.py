"""Proposal handoffs keep the Incoming lock free during slow preparation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event

import pytest

from distr.core.db import Chat, get_session
from distr.core.incoming import proposals, transcription
from tests.core.test_proposal_launch_retry import launch_case


def _try_handoff_lock():
    acquired = proposals._HANDOFF_LOCK.acquire(blocking=False)
    if acquired:
        proposals._HANDOFF_LOCK.release()
    return acquired


@pytest.mark.parametrize('auto_accept', [False, True])
def test_tensology_fetch_runs_outside_handoff_lock(launch_case, monkeypatch, auto_accept):
    remote, client, _ = launch_case
    proposal = proposals._from_tensology(remote)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def fetch(_path):
            assert pool.submit(_try_handoff_lock).result(timeout=5)
            return remote

        client.get.side_effect = fetch
        if auto_accept:
            monkeypatch.setattr('distr.core.settings.load_settings_from_db',
                                lambda: {'incoming_proposals_auto_accept': True})
            monkeypatch.setattr(proposals, 'auto_accept_skip_reason', lambda *args: '')
            assert proposals.auto_accept_proposals([proposal]) == [proposals._proposal_handoff_key(proposal)]
        else:
            proposals.create_proposal_thread(proposal)

    client.get.assert_called_once_with(f"work-requests/{remote['id']}")


@pytest.mark.parametrize('outer_lock', [False, True])
def test_transcription_precedes_locked_dispatch(launch_case, monkeypatch, outer_lock):
    remote, _, dispatch = launch_case
    proposal = proposals._from_tensology(remote)
    preparation = []
    transcript_report = {'ready': 1, 'empty': 0, 'failed': []}

    with ThreadPoolExecutor(max_workers=1) as pool:
        def prepare(chat_id):
            assert pool.submit(_try_handoff_lock).result(timeout=5) is not outer_lock
            dispatch.assert_not_called()
            preparation.append(chat_id)
            return transcript_report

        def start(chat_id, *args, **kwargs):
            assert preparation == [chat_id]
            assert not pool.submit(_try_handoff_lock).result(timeout=5)
            assert proposals.proposal_thread_attachment_report(chat_id)['transcript_report'] == transcript_report
            return {'status': 'running'}

        monkeypatch.setattr(transcription, 'prepare_thread_transcripts', prepare)
        dispatch.side_effect = start
        with proposals._HANDOFF_LOCK if outer_lock else nullcontext():
            chat_id = proposals.create_proposal_thread(proposal, start_work=True)

    dispatch.assert_called_once()
    assert dispatch.call_args.args[0] == chat_id


def test_duplicate_handoff_is_rejected_while_unrelated_lock_user_can_proceed(launch_case, monkeypatch):
    remote, client, dispatch = launch_case
    proposal = proposals._from_tensology(remote)
    transcribing, allow_finish = Event(), Event()

    def prepare(_chat_id):
        transcribing.set()
        assert allow_finish.wait(timeout=10)
        return {}

    monkeypatch.setattr(transcription, 'prepare_thread_transcripts', prepare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        original = pool.submit(proposals.create_proposal_thread, proposal, start_work=True)
        try:
            assert transcribing.wait(timeout=5)
            assert pool.submit(_try_handoff_lock).result(timeout=5)
            duplicate = pool.submit(proposals.create_proposal_thread, dict(proposal), start_work=True)
            with pytest.raises(ValueError, match=r'^This proposal is already being handed off\. Wait for it to finish\.$'):
                duplicate.result(timeout=5)
            dispatch.assert_not_called()
        finally:
            allow_finish.set()
        chat_id = original.result(timeout=5)

    dispatch.assert_called_once()
    client.get.assert_called_once()
    assert dispatch.call_args.args[0] == chat_id


def test_project_change_during_transcription_prevents_dispatch(launch_case, monkeypatch):
    remote, client, dispatch = launch_case
    proposal = proposals._from_tensology(remote)

    def prepare(chat_id):
        with get_session() as db:
            db.get(Chat, chat_id).project_id = None
        return {}

    monkeypatch.setattr(transcription, 'prepare_thread_transcripts', prepare)
    with pytest.raises(ValueError, match='The proposal thread does not belong to its linked project.'):
        proposals.create_proposal_thread(proposal, start_work=True)

    dispatch.assert_not_called()
    client.post.assert_not_called()
    assert proposals._proposal_handoff_key(proposal) not in proposals._HANDOFF_IN_FLIGHT
