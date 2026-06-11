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
