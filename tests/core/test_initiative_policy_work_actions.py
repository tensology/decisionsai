from distr.core.initiative.policy import PolicyDecision, evaluate
from distr.core.initiative.proposed_action import ProposedAction, parse_llm_response
from distr.core.initiative.service import InitiativeService, build_initiative_boundaries
from distr.core.initiative.voice_commands import match_draft_decision
from distr.core.project_cli_backends import get_backend, normalize_backend_id


def test_new_work_action_types_parse_from_llm_response():
    action = parse_llm_response(
        '{"action_type":"workflow_start","description":"Run ticket workflow","payload":{"ticket_ids":[1]}}'
    )

    assert action.action_type == "workflow_start"
    assert action.payload["ticket_ids"] == [1]


def test_workflow_start_requires_routine_and_workflow_scope():
    action = ProposedAction(action_type="workflow_start", description="Run it")

    assert evaluate(action, "operate", {}, policy_context=None) == PolicyDecision.DRAFT_AND_ASK
    assert evaluate(
        action,
        "operate",
        {
            "initiative_allow_routine_tasks": True,
            "initiative_allow_workflow_start": True,
        },
        policy_context=None,
    ) == PolicyDecision.EXECUTE


def test_project_cli_task_requires_explicit_project_cli_scope():
    action = ProposedAction(action_type="project_cli_task", description="Send to CLI")

    assert evaluate(
        action,
        "own",
        {
            "initiative_allow_routine_tasks": True,
            "initiative_allow_project_cli": False,
        },
        policy_context=None,
    ) == PolicyDecision.DRAFT_AND_ASK
    assert evaluate(
        action,
        "own",
        {
            "initiative_allow_routine_tasks": True,
            "initiative_allow_project_cli": True,
        },
        policy_context=None,
    ) == PolicyDecision.EXECUTE


def test_ticket_lane_move_can_execute_when_allowed():
    action = ProposedAction(action_type="ticket_lane_move", description="Move backlog")

    assert evaluate(action, "operate", {}, policy_context=None) == PolicyDecision.DRAFT_AND_ASK
    assert evaluate(
        action,
        "operate",
        {"initiative_allow_ticket_lane_moves": True},
        policy_context=None,
    ) == PolicyDecision.EXECUTE


def test_ticket_lane_move_message_uses_clean_plural_wording():
    from distr.core.initiative.action_handlers import _moved_tickets_message

    assert _moved_tickets_message(1, "Current") == "Moved 1 ticket to Current"
    assert _moved_tickets_message(2, "Current") == "Moved 2 tickets to Current"
    assert "ticket(s)" not in _moved_tickets_message(2, "Current")


def test_initiative_cannot_complete_a_ticket_for_the_human():
    from distr.core.initiative.action_handlers import move_tickets

    import pytest

    with pytest.raises(ValueError, match="Only a human"):
        move_tickets({"board_id": 1, "ticket_ids": [1], "target_lane": "Complete"})


def test_telegram_continue_phrase_approves_pending_draft():
    assert match_draft_decision("yes continue") == "approve"
    assert match_draft_decision("go ahead and continue") == "approve"


def test_codex_project_cli_backend_is_registered():
    assert normalize_backend_id("codex_cli") == "codex"
    assert get_backend("codex").id == "codex"


def test_initiative_boundaries_available_for_scheduler_cycle():
    settings = {
        "initiative_allow_project_cli": True,
        "initiative_allow_workflow_start": True,
        "initiative_ask_external_comms": False,
    }

    boundaries = build_initiative_boundaries(settings)

    assert hasattr(InitiativeService, "_initiative_boundaries")
    assert InitiativeService._initiative_boundaries(settings) == boundaries
    assert boundaries["initiative_allow_project_cli"] is True
    assert boundaries["initiative_allow_workflow_start"] is True
    assert boundaries["initiative_ask_external_comms"] is False
    assert boundaries["initiative_ask_file_changes"] is True


def _scan_bundle(*proposals):
    from types import SimpleNamespace

    return SimpleNamespace(work_scan={"proposals": list(proposals)})


def test_work_scan_skips_recent_duplicate_and_picks_next_candidate():
    stale = {
        "action_type": "workflow_start",
        "description": "Ticket #230 looks ready to execute",
        "payload": {"ticket_ids": [230], "board_id": 13},
    }
    triage = {
        "action_type": "message_triage",
        "description": "1 WhatsApp message arrived",
        "payload": {"source": "whatsapp", "message_ids": [1]},
    }
    settings = {"initiative_allow_workflow_start": True}
    service = InitiativeService.__new__(InitiativeService)
    from collections import deque

    service._recent_proposals = deque()
    service._proposal_cooldown_s = 7200.0

    first = InitiativeService._proposal_from_work_scan(
        _scan_bundle(stale, triage), settings, "operate", is_duplicate=service._is_duplicate_proposal
    )
    assert first.action_type == "workflow_start"
    service._record_proposal(first)

    second = InitiativeService._proposal_from_work_scan(
        _scan_bundle(stale, triage), settings, "operate", is_duplicate=service._is_duplicate_proposal
    )
    assert second.action_type == "message_triage"


def test_work_scan_returns_top_candidate_when_everything_is_duplicate():
    stale = {"action_type": "workflow_start", "description": "Ticket #230", "payload": {"ticket_ids": [230]}}
    settings = {"initiative_allow_workflow_start": True}
    action = InitiativeService._proposal_from_work_scan(
        _scan_bundle(stale), settings, "operate", is_duplicate=lambda _a: True
    )
    assert action.action_type == "workflow_start"
    assert InitiativeService._proposal_from_work_scan(_scan_bundle(stale), settings, "operate").payload == {
        "ticket_ids": [230]
    }


def test_board_proposal_targets_a_real_delivery_lane():
    """Regression: proposals used the legacy lane name 'Current', which
    move_ticket_to_delivery_lane rejects ("Automation may only move tickets to
    In Progress or QA"), so backlog promotions silently failed."""
    from distr.core.initiative.work_scanner import _add_board_proposals

    scan = {"proposals": []}
    board = {
        "id": 14,
        "name": "Plan My Charter",
        "source_lane": "",
        "lanes": [
            {"name": "Backlog", "tickets": [{"id": 330, "title": "Reorder dates"}]},
            {"name": "In Progress", "tickets": []},
        ],
    }
    _add_board_proposals(scan, board)

    moves = [p for p in scan["proposals"] if p["action_type"] == "ticket_lane_move"]
    assert moves, "expected a backlog promotion proposal"
    assert moves[0]["payload"]["target_lane"] == "In Progress"


def test_move_tickets_rejects_legacy_current_lane(monkeypatch):
    import contextlib

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from distr.core.db import Base
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def _sess():
        db = Session()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    import distr.core.db as dbmod

    monkeypatch.setattr(dbmod, "get_session", _sess)

    with _sess() as db:
        board = KanbanBoard(name="B", source="database")
        db.add(board)
        db.flush()
        for i, name in enumerate(("Backlog", "In Progress", "QA", "Complete")):
            db.add(KanbanLane(board_id=board.id, name=name, position=i))
        db.flush()
        lane = db.query(KanbanLane).filter_by(board_id=board.id, name="Backlog").first()
        ticket = KanbanTicket(lane_id=lane.id, title="x")
        db.add(ticket)
        db.flush()
        board_id, ticket_id = int(board.id), int(ticket.id)

    from distr.core.initiative.action_handlers import move_tickets

    import pytest

    with pytest.raises(ValueError, match="In Progress or QA"):
        move_tickets({"board_id": board_id, "ticket_ids": [ticket_id], "target_lane": "Current"})
