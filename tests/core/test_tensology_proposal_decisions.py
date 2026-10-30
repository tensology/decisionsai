"""Day-plan proposals and the run/queue/dismiss reply path."""

from unittest.mock import patch

import distr.core.tensology_client as tc
from distr.core.initiative.planners import format_pending_proposals_markdown


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
    assert "run 1" in block and "dismiss all" in block


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
        reply = tc.handle_proposal_decision_reply("create ticket 1", source="telegram")
    decide.assert_called_once_with(7, "send_to_decisions", source="telegram")
    assert "Check database" in reply
