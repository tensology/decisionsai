"""Unit tests for WorkflowAgentBridge and _finish_step_runner_orchestration integration."""

import queue
from unittest.mock import MagicMock, patch

import pytest

from distr.core.workflow_engine.agent_bridge import (
    WorkflowAgentBridge,
    _agent_report_queue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drain_queue():
    """Empty the module-level queue so tests start clean."""
    while True:
        try:
            _agent_report_queue.get_nowait()
        except queue.Empty:
            break


@pytest.fixture(autouse=True)
def _clean_queue():
    """Ensure the agent report queue is empty before and after each test."""
    _drain_queue()
    yield
    _drain_queue()


# ---------------------------------------------------------------------------
# WorkflowAgentBridge unit tests
# ---------------------------------------------------------------------------

class TestGenerateReport:
    """Tests for WorkflowAgentBridge._generate_report."""

    def test_successful_run(self):
        report = WorkflowAgentBridge._generate_report({
            "session_id": 1,
            "run_id": 5,
            "success": True,
            "cancelled": False,
            "steps_summary": [
                {"title": "Open browser", "id": 10},
                {"title": "Login", "id": 11},
            ],
        })
        assert report.startswith("All done.")
        assert "Open browser" in report
        assert "Login" in report
        assert "Give a brief spoken" not in report

    def test_failed_run(self):
        report = WorkflowAgentBridge._generate_report({
            "session_id": 2,
            "run_id": 3,
            "success": False,
            "cancelled": False,
            "steps_summary": [],
        })
        assert "That didn't work out." in report
        assert "No steps were recorded." in report

    def test_cancelled_run(self):
        report = WorkflowAgentBridge._generate_report({
            "session_id": 4,
            "run_id": 7,
            "success": False,
            "cancelled": True,
            "steps_summary": [{"title": "Step A", "id": 1}],
        })
        assert "I stopped that run." in report

    def test_missing_fields_defaults(self):
        report = WorkflowAgentBridge._generate_report({})
        assert "That didn't work out." in report
        assert "No steps were recorded." in report

    def test_voice_note_step_is_summarized_naturally_without_raw_payload(self):
        report = WorkflowAgentBridge._generate_report({
            "session_id": 350,
            "run_id": 149,
            "success": True,
            "cancelled": False,
            "steps_summary": [
                {
                    "title": "Automation Instruction",
                    "status": "completed",
                    "result": "Voice note sent: 'Hey babe, you are a sexy beautiful dude!'",
                }
            ],
        })

        assert "All done." in report
        assert "It sent a Telegram voice note with the requested message." in report
        assert "Hey babe" not in report
        assert "That's what happened" not in report
        assert "Give a brief spoken" not in report

    def test_project_cli_workflow_report_is_plain_english_without_raw_cli_dump(self):
        report = WorkflowAgentBridge._generate_report({
            "session_id": 355,
            "run_id": 75,
            "success": False,
            "cancelled": False,
            "steps_summary": [
                {
                    "title": "Implement ticket with selected CLI backend",
                    "status": "completed",
                    "result": (
                        "Project CLI backend: codex Project: 10 Status: completed "
                        "Reading additional input from stdin... OpenAI Codex v0.140.0-alpha.2 "
                        "callback_url=http://127.0.0.1:8765/api/workflows/355/runs/75/codex-events"
                    ),
                },
                {
                    "title": "Validate Spotify remake with project checks",
                    "status": "completed",
                    "result": (
                        "> test > node --test tests/smoke.test.mjs "
                        "✔ spotify remake satisfies the current ticket contract "
                        "ℹ tests 4 ℹ pass 4 ℹ fail 0 ℹ duration_ms 85.1125"
                    ),
                },
                {
                    "title": "Report green evidence",
                    "status": "completed",
                    "result": "GREEN validation passed: Spotify remake ticket reached complete.",
                },
            ],
        })

        assert "All done." in report
        assert "Codex completed the implementation handoff." in report
        assert "Validation passed: 4 tests, 0 failures." in report
        assert "Green evidence was recorded." in report
        assert "marked failed" not in report
        assert "Reading additional input from stdin" not in report
        assert "callback_url" not in report
        assert "OpenAI Codex" not in report
        assert "node --test" not in report


class TestQueueReportToAgent:
    """Tests for queue_report_to_agent and get_pending_reports."""

    def test_queue_and_drain(self):
        bridge = WorkflowAgentBridge()
        bridge.queue_report_to_agent(1, "report A")
        bridge.queue_report_to_agent(2, "report B")

        reports = WorkflowAgentBridge.get_pending_reports()
        assert len(reports) == 2
        assert reports[0] == {"session_id": 1, "report": "report A"}
        assert reports[1] == {"session_id": 2, "report": "report B"}

    def test_drain_empty_queue(self):
        reports = WorkflowAgentBridge.get_pending_reports()
        assert reports == []

    def test_drain_clears_queue(self):
        bridge = WorkflowAgentBridge()
        bridge.queue_report_to_agent(1, "x")
        WorkflowAgentBridge.get_pending_reports()
        assert WorkflowAgentBridge.get_pending_reports() == []


class TestNotifyVoiceAgent:
    """Tests for notify_voice_agent signal emission."""

    @patch("distr.core.signals.signal_manager")
    def test_emits_workflow_finished(self, mock_sm):
        bridge = WorkflowAgentBridge()
        bridge.notify_voice_agent(42, "done")
        mock_sm.workflow_finished.emit.assert_called_once_with(42, "done")


class TestOnWorkflowCompleted:
    """Tests for the top-level on_workflow_completed orchestration."""

    @patch("distr.core.signals.signal_manager")
    def test_queues_and_notifies(self, mock_sm):
        bridge = WorkflowAgentBridge()
        run_result = {
            "session_id": 10,
            "run_id": 20,
            "success": True,
            "cancelled": False,
            "steps_summary": [{"title": "S1", "id": 1}],
        }
        bridge.on_workflow_completed(10, run_result)

        # Report was queued
        reports = WorkflowAgentBridge.get_pending_reports()
        assert len(reports) == 1
        assert reports[0]["session_id"] == 10
        assert reports[0]["report"].startswith("All done.")

        # Signal was emitted
        mock_sm.workflow_finished.emit.assert_called_once()
        args = mock_sm.workflow_finished.emit.call_args[0]
        assert args[0] == 10
        assert args[1].startswith("All done.")

    @patch("distr.core.signals.signal_manager")
    def test_handles_signal_error_gracefully(self, mock_sm):
        mock_sm.workflow_finished.emit.side_effect = RuntimeError("signal boom")
        bridge = WorkflowAgentBridge()
        # Should not raise
        bridge.on_workflow_completed(1, {"session_id": 1, "run_id": 2, "success": True})


# ---------------------------------------------------------------------------
# Integration: _finish_step_runner_orchestration calls the bridge
# ---------------------------------------------------------------------------

class TestFinishOrchestrationBridgeIntegration:
    """Verify _finish_step_runner_orchestration calls WorkflowAgentBridge."""

    def _make_mixin(self, workflow_id=1, run_id=5, steps_data=None):
        from distr.app.workflow import WorkflowOrchestrationMixin
        mixin = WorkflowOrchestrationMixin()
        mixin._workflow_timeout_timers = {}
        mixin._workflow_orchestrations = {
            workflow_id: {
                "workflow_id": workflow_id,
                "run_id": run_id,
                "steps_data": steps_data or [{"id": 10, "title": "Step 1"}],
                "workflow_agent": None,
                "agent_loop": None,
            },
        }
        return mixin, workflow_id

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._cancel_workflow_timeout")
    def test_bridge_called_on_success(self, mock_cancel, mock_sm):
        mixin, workflow_id = self._make_mixin()
        with patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run"):
            mixin._finish_workflow_orchestration(workflow_id=workflow_id, success=True)

        reports = WorkflowAgentBridge.get_pending_reports()
        assert len(reports) == 1
        assert reports[0]["session_id"] == 1
        assert reports[0]["report"].startswith("All done.")
        mock_sm.workflow_finished.emit.assert_called_once()

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._cancel_workflow_timeout")
    def test_bridge_called_on_cancel(self, mock_cancel, mock_sm):
        mixin, workflow_id = self._make_mixin()
        with patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run"), \
             patch("distr.core.db.get_session") as mock_db:
            mock_sess = MagicMock()
            mock_db.return_value.__enter__ = MagicMock(return_value=MagicMock(
                query=MagicMock(return_value=MagicMock(
                    filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_sess)))
                )),
                commit=MagicMock(),
            ))
            mock_db.return_value.__exit__ = MagicMock(return_value=False)
            mixin._finish_workflow_orchestration(workflow_id=workflow_id, success=False, cancelled=True)

        reports = WorkflowAgentBridge.get_pending_reports()
        assert len(reports) == 1
        assert "I stopped that run." in reports[0]["report"]

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._cancel_workflow_timeout")
    def test_bridge_failure_does_not_break_finish(self, mock_cancel, mock_sm):
        """If the bridge itself raises, _finish_workflow_orchestration still completes."""
        mixin, workflow_id = self._make_mixin()
        with patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run"), \
             patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge.on_workflow_completed",
                   side_effect=RuntimeError("bridge exploded")):
            # Should not raise
            mixin._finish_workflow_orchestration(workflow_id=workflow_id, success=True)
        # Orchestration state was still cleared
        assert workflow_id not in mixin._workflow_orchestrations
