"""Day-plan proposals and the run/queue/dismiss reply path."""

from unittest.mock import Mock, patch

import distr.core.tensology_client as tc
from distr.core.initiative.planners import format_pending_proposals_markdown


def test_incoming_pending_old_collection_survives_bounded_decided_history():
    pending = {'id': 1, 'status': 'pending', 'source_started_at': '2026-05-15T00:00:00Z'}
    history = [{'id': i, 'status': 'dismissed', 'source_started_at': '2026-11-01T00:00:00Z'} for i in range(2, 202)]
    client = Mock()
    client.get.side_effect = [[pending], history]
    with patch.object(tc, 'configured_tensology_client', return_value=client):
        rows = tc.fetch_pending_work_requests(include_decided=True)
    assert rows[0] == pending and len(rows) == 201
    assert [call.kwargs['query']['status'] for call in client.get.call_args_list] == ['pending', 'all']


def test_incoming_later_canonical_decision_wins_over_earlier_pending_snapshot():
    client = Mock()
    client.get.side_effect = [[{'id': 1, 'status': 'pending'}], [{'id': 1, 'status': 'dismissed'}]]
    with patch.object(tc, 'configured_tensology_client', return_value=client):
        assert tc.fetch_pending_work_requests(include_decided=True) == [{'id': 1, 'status': 'dismissed'}]


def test_format_pending_proposals_markdown_lists_names_and_choices():
    block = format_pending_proposals_markdown([
        {
            "id": 1,
            "project": {"name": "Merrypak"},
            "title": "Celery worker dead on jackcat-ub-srv",
            "priority": "high",
            "rationale": "Queue keeps dropping",
        },
        {"id": 2, "project": {"name": "AuctionNow"}, "title": "Add CSV export", "priority": "medium"},
    ])

    assert "Proposals needing your call" in block
    assert "Celery worker dead on jackcat-ub-srv" in block
    assert "Merrypak" in block and "**(urgent)**" in block
    assert "#1 Celery worker" in block and "#2 Add CSV export" in block
    assert "Ask for **proposals**" in block
    assert "run 1" not in block and "dismiss all" not in block


def test_format_pending_proposals_markdown_empty():
    assert format_pending_proposals_markdown([]) == ""
    assert format_pending_proposals_markdown(None) == ""


def test_proposal_decision_reply_queues_the_indexed_proposal():
    with (
        patch.object(tc, "fetch_pending_work_requests", return_value=[
            {"id": 7, "title": "Celery worker dead"}, {"id": 8, "title": "CSV export"},
        ]),
        patch.object(tc, "decide_work_request") as decide,
    ):
        reply = tc.handle_proposal_decision_reply("queue 2")

    decide.assert_called_once_with(8, "queue")
    assert "CSV export" in reply


def test_proposal_decision_reply_run_all():
    with (
        patch.object(tc, "fetch_pending_work_requests", return_value=[
            {"id": 7, "title": "A"}, {"id": 8, "title": "B"},
        ]),
        patch.object(tc, "decide_work_request") as decide,
    ):
        reply = tc.handle_proposal_decision_reply("run all")

    assert decide.call_count == 2
    assert decide.call_args_list[0].args == (7, "send_to_decisions")
    assert "2 proposal" in reply


def test_proposal_decision_reply_out_of_range_and_no_pending():
    with patch.object(tc, "fetch_pending_work_requests", return_value=[{"id": 7, "title": "A"}]):
        assert "no proposal 5" in tc.handle_proposal_decision_reply("dismiss 5")
    with patch.object(tc, "fetch_pending_work_requests", return_value=[]):
        assert "no Tensology proposals waiting" in tc.handle_proposal_decision_reply("run 1")


def test_proposal_decision_reply_ignores_other_text():
    assert tc.handle_proposal_decision_reply("continue") is None
    assert tc.handle_proposal_decision_reply("what's actually waiting?") is None


def test_telegram_create_ticket_preserves_approval_source():
    with (
        patch.object(tc, "fetch_pending_work_requests", return_value=[{"id":7,"title":"Check database"}]),
        patch.object(tc, "decide_work_request") as decide,
    ):
        reply = tc.handle_proposal_decision_reply("create ticket #7", source="telegram", telegram_chat_id=12345)
    decide.assert_called_once_with(7, "send_to_decisions", source="telegram", telegram_chat_id=12345)
    assert "Check database" in reply


def test_telegram_approval_request_carries_the_private_chat():
    client = Mock()
    with patch.object(tc, 'configured_tensology_client', return_value=client):
        tc.decide_work_request(7, 'run', source='telegram', telegram_chat_id='12345')
    client.post.assert_called_once_with(
        'work-requests/7/action',
        {'action': 'send_to_decisions', 'source': 'telegram', 'approval_telegram_chat_id': 12345},
        idempotency_key='work-request-7-send_to_decisions',
    )


def test_telegram_exact_reference_survives_pending_list_reorder():
    with (
        patch.object(tc, "fetch_pending_work_requests", return_value=[
            {"id": 19, "title": "Newly arrived"}, {"id": 8, "title": "Reviewed proposal"},
        ]),
        patch.object(tc, "decide_work_request") as decide,
    ):
        result = tc.handle_proposal_decision_reply("run #8", source="telegram", telegram_chat_id=12345)
    decide.assert_called_once_with(8, "send_to_decisions", source="telegram", telegram_chat_id=12345)
    assert "Reviewed proposal" in result


def test_telegram_position_and_bulk_commands_cannot_change_unreviewed_proposals():
    with (
        patch.object(tc, "fetch_pending_work_requests", return_value=[{"id": 19, "title": "Newly arrived"}]) as fetch,
        patch.object(tc, "decide_work_request") as decide,
    ):
        for command in ("run 1", "queue 1", "dismiss 1", "run all", "dismiss all"):
            result = tc.handle_proposal_decision_reply(command, source="telegram", telegram_chat_id=12345)
            assert "Ask for proposals" in result
    fetch.assert_not_called()
    decide.assert_not_called()


def test_telegram_missing_exact_reference_does_not_select_a_replacement():
    with (
        patch.object(tc, "fetch_pending_work_requests", return_value=[{"id": 19, "title": "Newly arrived"}]),
        patch.object(tc, "decide_work_request") as decide,
    ):
        result = tc.handle_proposal_decision_reply("run #8", source="telegram", telegram_chat_id=12345)
    decide.assert_not_called()
    assert "Proposal #8 is no longer waiting" in result
