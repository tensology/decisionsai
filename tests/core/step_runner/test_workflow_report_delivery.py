"""Unit tests for workflow completion delivery (system activity, not chat prompts)."""

import queue
from unittest.mock import MagicMock, patch

import pytest

from distr.app.signals import SignalBridgeMixin
from distr.core.workflow_engine.agent_bridge import (
    WorkflowAgentBridge,
    _agent_report_queue,
)


def _drain_queue():
    while True:
        try:
            _agent_report_queue.get_nowait()
        except queue.Empty:
            break


@pytest.fixture(autouse=True)
def _clean_queue():
    _drain_queue()
    yield
    _drain_queue()


class DummySignalApp(SignalBridgeMixin):
    def __init__(self):
        self.telegram_manager = MagicMock()
        self.telegram_manager.is_connected.return_value = False
        self._send_command_to_agent = MagicMock()
        self._persist_calls = []

    def _persist_workflow_system_activity(self, report_text):
        self._persist_calls.append(report_text)

    def _send_workflow_report_to_telegram(self, session_id, report_text):
        pass


class TestWorkflowReportDelivery:
    def test_drains_queued_report_and_persists_system_activity(self):
        app = DummySignalApp()
        WorkflowAgentBridge().queue_report_to_agent(42, "Workflow completed OK")

        app._deliver_workflow_finished_report(42, "fallback summary")

        assert len(app._persist_calls) == 1
        assert "Workflow completed OK" in app._persist_calls[0]
        app._send_command_to_agent.assert_not_called()

    def test_falls_back_to_signal_summary(self):
        app = DummySignalApp()

        app._deliver_workflow_finished_report(99, "signal summary text")

        assert app._persist_calls == ["signal summary text"]
        app._send_command_to_agent.assert_not_called()

    def test_requeues_reports_for_other_sessions(self):
        app = DummySignalApp()
        bridge = WorkflowAgentBridge()
        bridge.queue_report_to_agent(1, "report for session 1")
        bridge.queue_report_to_agent(2, "report for session 2")
        bridge.queue_report_to_agent(3, "report for session 3")

        app._deliver_workflow_finished_report(2, "fallback")

        assert "report for session 2" in app._persist_calls[0]
        remaining = WorkflowAgentBridge.get_pending_reports()
        remaining_sessions = {r["session_id"] for r in remaining}
        assert remaining_sessions == {1, 3}

    def test_empty_report_skips_delivery(self):
        app = DummySignalApp()

        app._deliver_workflow_finished_report(1, "")

        assert app._persist_calls == []
        app._send_command_to_agent.assert_not_called()

    def test_automation_report_skips_persist_and_telegram(self, monkeypatch):
        app = DummySignalApp()
        monkeypatch.setattr(
            app,
            "_workflow_report_is_automation",
            lambda _text: True,
        )

        app._deliver_workflow_finished_report(1, "Automation Instruction: done.")

        assert app._persist_calls == []

    def test_bridge_exception_falls_back_to_summary(self):
        app = DummySignalApp()
        with patch(
            "distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge.get_pending_reports",
            side_effect=RuntimeError("queue corrupted"),
        ):
            app._deliver_workflow_finished_report(1, "fallback summary")

        assert app._persist_calls == ["fallback summary"]
