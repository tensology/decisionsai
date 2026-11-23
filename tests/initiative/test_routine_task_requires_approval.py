"""routine_task must ask for approval instead of running silently."""

from unittest.mock import MagicMock

from distr.core.initiative.policy import PolicyDecision, evaluate
from distr.core.initiative.proposed_action import ProposedAction
from distr.core.initiative.service import InitiativeService, build_initiative_boundaries
from distr.core.initiative.tiers import PermissionTier


def _service():
    service = InitiativeService.__new__(InitiativeService)
    service._cycle_situational = {}
    service._draft_and_ask = MagicMock()
    service._deliver_suggestion = MagicMock()
    service._dispatch_kanban = MagicMock()
    service._dispatch_workflow = MagicMock()
    service._recommend_oversight_only = MagicMock(return_value=False)
    return service


def _action(**payload):
    return ProposedAction(
        action_type="routine_task",
        description="Create a follow-up ticket",
        payload=payload or {"runner_type": "kanban", "board_id": 1, "title": "Follow up"},
    )


def test_operate_dispatch_asks_instead_of_creating_a_ticket():
    service = _service()
    settings = {
        "initiative_level": "operate",
        "initiative_allow_routine_tasks": True,
    }
    action = _action()
    boundaries = build_initiative_boundaries(settings)
    decision = evaluate(action, "operate", boundaries)

    assert decision == PolicyDecision.EXECUTE
    service._dispatch_action(action, settings, decision, boundaries)

    service._draft_and_ask.assert_called_once()
    assert action.requires_confirmation is True
    service._dispatch_kanban.assert_not_called()
    service._dispatch_workflow.assert_not_called()


def test_execute_action_redirects_silent_or_notify_to_approval():
    service = _service()
    settings = {"initiative_allow_routine_tasks": True}
    action = _action()

    service._execute_action(action, settings, tier=PermissionTier.SILENT)

    service._draft_and_ask.assert_called_once_with(
        action, settings, tier=PermissionTier.APPROVE
    )
    service._dispatch_kanban.assert_not_called()


def test_execute_action_runs_after_approval_tier():
    service = _service()
    settings = {"initiative_allow_routine_tasks": True}
    action = _action()

    service._execute_action(action, settings, tier=PermissionTier.APPROVE)

    service._dispatch_kanban.assert_called_once_with(
        action, settings, tier=PermissionTier.APPROVE
    )
    service._draft_and_ask.assert_not_called()


def test_approved_routine_task_draft_carries_execute_payload(tmp_path, monkeypatch):
    from distr.core.initiative.draft_queue import DraftQueue

    queue = DraftQueue(path=str(tmp_path / "drafts.json"))
    service = InitiativeService.__new__(InitiativeService)
    service._draft_queue = queue
    service._cycle_situational = {}
    service.telegram_manager = None
    service.chat_manager = None
    service._log_to_chat = MagicMock()
    service._send_telegram_if_allowed = MagicMock()
    monkeypatch.setattr(
        "distr.core.initiative.service._derive_initiative_action_context",
        lambda action: {},
    )

    action = _action()
    service._draft_and_ask(action, {"initiative_allow_telegram": False}, tier=PermissionTier.APPROVE)

    drafts = queue.get_all()
    assert len(drafts) == 1
    payload = drafts[0].execute_payload
    assert payload["kind"] == "initiative_action"
    assert payload["action"]["action_type"] == "routine_task"
    assert payload["action"]["payload"]["board_id"] == 1
