from unittest.mock import Mock

from distr.core.workflow.dispatcher import _finalize_development_chat_turn


def test_completed_workflow_closes_turn_and_appends_result(monkeypatch):
    complete = Mock()
    terminal = Mock()
    append = Mock()
    monkeypatch.setattr("distr.core.chat_turns.latest_active_turn_id", lambda chat_id: 77)
    monkeypatch.setattr("distr.core.chat_turns.complete_turn", complete)
    monkeypatch.setattr("distr.core.chat_turns.terminal_turn", terminal)
    monkeypatch.setattr("distr.core.chat.ChatService.append_assistant_notice", append)
    monkeypatch.setattr("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge._generate_report", lambda result: "All done.")

    _finalize_development_chat_turn(17, "completed", {"success": True})

    complete.assert_called_once_with(17, turn_id=77, display_text="All done.")
    terminal.assert_not_called()
    append.assert_called_once_with(17, "All done.")


def test_workflow_completion_does_not_duplicate_closed_turn(monkeypatch):
    append = Mock()
    monkeypatch.setattr("distr.core.chat_turns.latest_active_turn_id", lambda chat_id: None)
    monkeypatch.setattr("distr.core.chat.ChatService.append_assistant_notice", append)

    _finalize_development_chat_turn(17, "completed", {"success": True})

    append.assert_not_called()
