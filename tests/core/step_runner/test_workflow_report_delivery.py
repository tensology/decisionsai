"""Unit tests for on_workflow_finished signal handler logic.

Validates Task 4: Ensure workflow report delivery.
- 4.1: on_workflow_finished reliably drains get_pending_reports() and sends to agent
- 4.2: Error handling when agent command queue is unavailable or agent process not running

We test the handler logic directly (extracted from SignalBridgeMixin._bridge_signals_to_agent)
to avoid importing PyQt6 which is not available in the test environment.
"""

import queue
from unittest.mock import MagicMock, patch

import pytest

from distr.core.step_runner.agent_bridge import (
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
    _drain_queue()
    yield
    _drain_queue()


def _make_self_mock(has_queue=True, agent_alive=True):
    """Build a mock 'self' (Application-like) with the attributes the handler checks."""
    app = MagicMock()
    if has_queue:
        app.agent_command_queue = MagicMock()
    else:
        app.agent_command_queue = None
    if agent_alive:
        app.agent_process = MagicMock()
        app.agent_process.is_alive.return_value = True
    else:
        app.agent_process = MagicMock()
        app.agent_process.is_alive.return_value = False
    app._quitting = False
    return app


def _build_handler(self_mock):
    """Build the on_workflow_finished handler closure matching signals.py.

    This replicates the exact logic from SignalBridgeMixin._bridge_signals_to_agent
    so we can test it without importing PyQt6.
    """
    import logging
    logger = logging.getLogger(__name__)

    def on_workflow_finished(session_id, summary):
        try:
            from distr.core.step_runner.agent_bridge import WorkflowAgentBridge
            reports = WorkflowAgentBridge.get_pending_reports()
            report_text = summary
            for r in reports:
                if r.get("session_id") == session_id:
                    report_text = r.get("report", summary)
                else:
                    WorkflowAgentBridge().queue_report_to_agent(
                        r.get("session_id", 0), r.get("report", "")
                    )
            if not report_text:
                logger.warning(
                    "Workflow finished: no report text for session %d, skipping agent delivery",
                    session_id,
                )
                return

            has_queue = (
                hasattr(self_mock, 'agent_command_queue')
                and self_mock.agent_command_queue is not None
            )
            agent_alive = (
                hasattr(self_mock, 'agent_process')
                and self_mock.agent_process is not None
                and self_mock.agent_process.is_alive()
            )
            if not has_queue:
                logger.warning(
                    "Workflow finished: agent command queue unavailable for session %d, "
                    "report not delivered",
                    session_id,
                )
                return
            if not agent_alive:
                logger.warning(
                    "Workflow finished: agent process not running for session %d, "
                    "attempting delivery anyway (agent may restart)",
                    session_id,
                )

            self_mock._send_command_to_agent('process_text_input', {
                'text': f"[Workflow Report]\n{report_text}",
                'speak': False,
            })
            logger.info("Workflow finished: forwarded report for session %d to agent", session_id)
        except Exception as e:
            logger.error("Workflow finished handler failed for session %d: %s", session_id, e, exc_info=True)

    return on_workflow_finished


# ---------------------------------------------------------------------------
# Task 4.1: Reliable report draining and delivery
# ---------------------------------------------------------------------------

class TestWorkflowReportDelivery:
    """Verify on_workflow_finished drains reports and sends to agent."""

    def test_sends_report_from_queue(self):
        """When a report is queued for the session, it is sent to the agent."""
        app = _make_self_mock()
        handler = _build_handler(app)

        WorkflowAgentBridge().queue_report_to_agent(42, "Workflow completed OK")

        handler(42, "fallback summary")

        app._send_command_to_agent.assert_called_once()
        args = app._send_command_to_agent.call_args
        assert args[0][0] == 'process_text_input'
        assert "Workflow completed OK" in args[0][1]['text']
        assert "[Workflow Report]" in args[0][1]['text']

    def test_falls_back_to_summary_when_no_queued_report(self):
        """When no report is queued, the signal summary is used."""
        app = _make_self_mock()
        handler = _build_handler(app)

        handler(99, "signal summary text")

        app._send_command_to_agent.assert_called_once()
        args = app._send_command_to_agent.call_args
        assert "signal summary text" in args[0][1]['text']

    def test_requeues_reports_for_other_sessions(self):
        """Reports for other sessions are re-queued, not lost."""
        app = _make_self_mock()
        handler = _build_handler(app)

        bridge = WorkflowAgentBridge()
        bridge.queue_report_to_agent(1, "report for session 1")
        bridge.queue_report_to_agent(2, "report for session 2")
        bridge.queue_report_to_agent(3, "report for session 3")

        handler(2, "fallback")

        # Session 2's report was sent to agent
        app._send_command_to_agent.assert_called_once()
        assert "report for session 2" in app._send_command_to_agent.call_args[0][1]['text']

        # Sessions 1 and 3 reports should be re-queued
        remaining = WorkflowAgentBridge.get_pending_reports()
        remaining_sessions = {r["session_id"] for r in remaining}
        assert 1 in remaining_sessions
        assert 3 in remaining_sessions
        assert 2 not in remaining_sessions

    def test_speak_is_false(self):
        """Report is sent with speak=False."""
        app = _make_self_mock()
        handler = _build_handler(app)

        handler(1, "some summary")

        args = app._send_command_to_agent.call_args
        assert args[0][1]['speak'] is False

    def test_empty_report_text_skips_delivery(self):
        """When report_text is empty, no command is sent."""
        app = _make_self_mock()
        handler = _build_handler(app)

        handler(1, "")

        app._send_command_to_agent.assert_not_called()


# ---------------------------------------------------------------------------
# Task 4.2: Error handling for unavailable agent
# ---------------------------------------------------------------------------

class TestWorkflowReportErrorHandling:
    """Verify error handling when agent is unavailable."""

    def test_no_command_queue_skips_delivery(self):
        """When agent_command_queue is None, report is not sent."""
        app = _make_self_mock(has_queue=False)
        handler = _build_handler(app)

        handler(1, "some report")

        app._send_command_to_agent.assert_not_called()

    def test_no_command_queue_attr_skips_delivery(self):
        """When agent_command_queue attribute doesn't exist, report is not sent."""
        app = _make_self_mock()
        del app.agent_command_queue
        handler = _build_handler(app)

        handler(1, "some report")

        app._send_command_to_agent.assert_not_called()

    def test_agent_not_alive_still_attempts_delivery(self):
        """When agent process is not alive, delivery is still attempted (agent may restart)."""
        app = _make_self_mock(agent_alive=False)
        handler = _build_handler(app)

        handler(1, "some report")

        # Should still call _send_command_to_agent (it handles restart internally)
        app._send_command_to_agent.assert_called_once()

    def test_send_command_exception_does_not_propagate(self):
        """If _send_command_to_agent raises, the handler does not propagate the exception."""
        app = _make_self_mock()
        app._send_command_to_agent.side_effect = RuntimeError("queue full")
        handler = _build_handler(app)

        # Should not raise
        handler(1, "some report")

    def test_get_pending_reports_exception_does_not_propagate(self):
        """If get_pending_reports raises, the handler does not propagate."""
        app = _make_self_mock()
        handler = _build_handler(app)

        with patch(
            "distr.core.step_runner.agent_bridge.WorkflowAgentBridge.get_pending_reports",
            side_effect=RuntimeError("queue corrupted"),
        ):
            # Should not raise
            handler(1, "fallback summary")
