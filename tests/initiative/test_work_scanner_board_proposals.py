"""Board proposals must skip terminal tickets and smoke/test fixture boards."""
from distr.core.initiative import work_scanner as ws


def _board(name="DecisionsAI", description="", tickets=None):
    return {
        "id": 13,
        "name": name,
        "description": description,
        "source_lane": "Current",
        "lanes": [{"id": 79, "name": "Backlog", "tickets": tickets or []}],
    }


def _ticket(tid, status=""):
    return {
        "id": tid,
        "title": f"Ticket {tid}",
        "workflow_status": status,
        "linked_workflow_id": 374,
        "linked_project_id": 11,
        "send_to_cli": False,
    }


def _proposals(board):
    scan = {"proposals": []}
    ws._add_board_proposals(scan, board)
    return scan["proposals"]


def test_cancelled_and_completed_tickets_are_not_proposed_for_workflow_start():
    board = _board(tickets=[_ticket(230, "cancelled"), _ticket(231, "completed"), _ticket(232, "")])
    starts = [p for p in _proposals(board) if p["action_type"] == "workflow_start"]
    assert len(starts) == 1
    assert starts[0]["payload"]["ticket_ids"] == [232]


def test_all_terminal_tickets_yield_no_workflow_start():
    board = _board(tickets=[_ticket(230, "Cancelled"), _ticket(231, "done"), _ticket(233, "running")])
    assert not [p for p in _proposals(board) if p["action_type"] == "workflow_start"]


def test_smoke_boards_get_no_proposals():
    assert _proposals(_board(name="Local Model Smoke", tickets=[_ticket(232)])) == []
    marked = _board(name="Bean & Byte", description="[dai-smoke-loop-fixture] test", tickets=[_ticket(1)])
    assert _proposals(marked) == []


def test_regular_board_still_proposes():
    kinds = {p["action_type"] for p in _proposals(_board(name="Smokehouse BBQ", tickets=[_ticket(5)]))}
    assert kinds == {"ticket_lane_move", "workflow_start"}
