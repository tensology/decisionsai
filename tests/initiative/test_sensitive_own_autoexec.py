"""Sensitive actions auto-execute only in Own outcomes with "Ask before sensitive actions" OFF.

Every executor, audit, chat, and Telegram path is mocked: nothing is sent.
"""

from unittest.mock import MagicMock

import pytest

import distr.core.initiative.action_handlers as action_handlers
import distr.core.orchestrator as orchestrator
from distr.core.initiative.policy import evaluate
from distr.core.initiative.proposed_action import ProposedAction
from distr.core.initiative.service import InitiativeService, build_initiative_boundaries


def _settings(level: str, ask_sensitive: bool) -> dict:
    return {
        "initiative_level": level,
        "initiative_allow_telegram": True,
        "initiative_allow_routine_tasks": True,
        "initiative_allow_workflow_start": True,
        "initiative_ask_sensitive": ask_sensitive,
    }


def _action(target: str = "workflow_start") -> ProposedAction:
    payload = {"ticket_ids": [7]}
    if target:
        payload["proposed_action_type"] = target
    return ProposedAction(action_type="sensitive", description="Start the release workflow", payload=payload)


@pytest.fixture
def harness(monkeypatch):
    executor = MagicMock(return_value={"success": True, "message": "Started 1 workflow"})
    emit = MagicMock(return_value=1)
    monkeypatch.setattr(action_handlers, "execute_initiative_action", executor)
    monkeypatch.setattr(orchestrator, "emit_event", emit)
    service = InitiativeService.__new__(InitiativeService)
    service._cycle_situational = {}
    service._log_to_chat = MagicMock()
    service._send_telegram_if_allowed = MagicMock()
    service._draft_and_ask = MagicMock()
    service._deliver_suggestion = MagicMock()
    return service, executor, emit


def _run(service, settings, action):
    boundaries = build_initiative_boundaries(settings)
    decision = evaluate(action, settings["initiative_level"], boundaries)
    service._dispatch_action(action, settings, decision, boundaries)
    return decision


def test_own_with_ask_sensitive_off_invokes_existing_executor_and_audits(harness):
    service, executor, emit = harness
    settings = _settings("own", ask_sensitive=False)

    _run(service, settings, _action("workflow_start"))

    executor.assert_called_once()
    kwargs = executor.call_args.kwargs
    assert kwargs["action_type"] == "workflow_start"
    assert kwargs["payload"]["ticket_ids"] == [7]
    service._draft_and_ask.assert_not_called()
    # Phase 3: Own outcomes also announces the start (own_announce) before running.
    events = [c.kwargs for c in emit.call_args_list]
    sensitive = [e for e in events if e["event_type"] == "sensitive_action_auto_executed"]
    assert len(sensitive) == 1 and sensitive[0]["status"] == "executed"
    assert [e["event_type"] for e in events] == ["outcome_own_announce", "sensitive_action_auto_executed"]
    service._log_to_chat.assert_called_once()
    assert "[Auto] sensitive workflow_start executed" in service._log_to_chat.call_args.args[0]


def test_own_with_ask_sensitive_off_and_no_executor_stays_notice_only(harness):
    service, executor, emit = harness

    _run(service, _settings("own", ask_sensitive=False), _action(target=""))

    executor.assert_not_called()
    service._draft_and_ask.assert_not_called()
    assert emit.call_args.kwargs["status"] == "notice_only"
    assert "notice only" in service._log_to_chat.call_args.args[0]


def test_own_executor_failure_is_audited_not_raised(harness):
    service, executor, emit = harness
    executor.side_effect = PermissionError("workflow starts are disabled")

    _run(service, _settings("own", ask_sensitive=False), _action("workflow_start"))

    assert emit.call_args.kwargs["status"] == "failed"
    assert "could not run" in service._log_to_chat.call_args.args[0]


@pytest.mark.parametrize("level", ["operate", "assist", "observe"])
def test_other_levels_with_ask_sensitive_off_never_invoke_executor(harness, level):
    service, executor, emit = harness

    _run(service, _settings(level, ask_sensitive=False), _action("workflow_start"))

    executor.assert_not_called()
    assert not any(
        c.kwargs.get("event_type") == "sensitive_action_auto_executed" for c in emit.call_args_list
    )


def test_own_with_ask_sensitive_on_requires_approval(harness):
    service, executor, emit = harness

    _run(service, _settings("own", ask_sensitive=True), _action("workflow_start"))

    executor.assert_not_called()
    service._draft_and_ask.assert_called_once()
    emit.assert_not_called()


def test_own_allowed_work_action_executes_without_approval(harness):
    service, executor, emit = harness
    action = ProposedAction(action_type="workflow_start", description="Run", payload={"ticket_ids": [7]})

    _run(service, _settings("own", ask_sensitive=False), action)

    # Own outcomes: the allow toggles gate this, not the approval floor.
    service._draft_and_ask.assert_not_called()
    executor.assert_called_once()
    assert executor.call_args.kwargs["action_type"] == "workflow_start"


def test_operate_allowed_work_action_still_requires_approval(harness):
    service, executor, emit = harness
    action = ProposedAction(action_type="workflow_start", description="Run", payload={"ticket_ids": [7]})

    _run(service, _settings("operate", ask_sensitive=False), action)

    service._draft_and_ask.assert_called_once()
    executor.assert_not_called()
