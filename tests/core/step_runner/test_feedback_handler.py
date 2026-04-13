"""Unit tests for the feedback handler in WorkflowOrchestrationMixin.

Tests _on_step_waiting_for_feedback and _provide_workflow_feedback.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from distr.app.workflow import WorkflowOrchestrationMixin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mixin():
    """Create a WorkflowOrchestrationMixin with minimal setup."""
    mixin = WorkflowOrchestrationMixin()
    mixin._workflow_orchestrations = {}
    mixin._workflow_timeout_timers = {}
    mixin._waiting_for_feedback = {}
    return mixin


# ---------------------------------------------------------------------------
# _on_step_waiting_for_feedback
# ---------------------------------------------------------------------------

class TestOnStepWaitingForFeedback:
    """Tests for the signal handler that stores waiting state."""

    def test_stores_waiting_state(self):
        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(
            step_id=10, workflow_id=1, run_id=5, result_text="Step done",
        )
        assert 5 in mixin._waiting_for_feedback
        info = mixin._waiting_for_feedback[5]
        assert info["step_id"] == 10
        assert info["workflow_id"] == 1
        assert info["run_id"] == 5
        assert info["result_text"] == "Step done"

    def test_overwrites_previous_waiting_state_for_same_run(self):
        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "first")
        mixin._on_step_waiting_for_feedback(20, 1, 5, "second")
        assert mixin._waiting_for_feedback[5]["step_id"] == 20
        assert mixin._waiting_for_feedback[5]["result_text"] == "second"

    def test_multiple_runs_stored_independently(self):
        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "run5")
        mixin._on_step_waiting_for_feedback(20, 2, 6, "run6")
        assert len(mixin._waiting_for_feedback) == 2
        assert mixin._waiting_for_feedback[5]["step_id"] == 10
        assert mixin._waiting_for_feedback[6]["step_id"] == 20

    def test_initializes_dict_if_missing(self):
        mixin = WorkflowOrchestrationMixin()
        mixin._workflow_orchestrations = {}
        mixin._workflow_timeout_timers = {}
        # Don't set _waiting_for_feedback — handler should create it
        if hasattr(mixin, "_waiting_for_feedback"):
            del mixin._waiting_for_feedback
        mixin._on_step_waiting_for_feedback(10, 1, 5, "result")
        assert hasattr(mixin, "_waiting_for_feedback")
        assert 5 in mixin._waiting_for_feedback


# ---------------------------------------------------------------------------
# _provide_workflow_feedback
# ---------------------------------------------------------------------------

class TestProvideWorkflowFeedback:
    """Tests for the method that resumes a waiting step with feedback."""

    @patch("distr.core.workflow.router.StepRouter")
    def test_calls_resume_from_feedback(self, MockRouter):
        mock_router = MockRouter.return_value
        mock_router.resume_from_feedback.return_value = {
            "action": "end_run", "status": "completed",
        }

        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "step result")

        decision = mixin._provide_workflow_feedback(5, "looks good")

        mock_router.resume_from_feedback.assert_called_once_with(10, 5, "looks good")
        assert decision["action"] == "end_run"

    @patch("distr.core.workflow.router.StepRouter")
    def test_removes_waiting_state_after_feedback(self, MockRouter):
        mock_router = MockRouter.return_value
        mock_router.resume_from_feedback.return_value = {
            "action": "end_run", "status": "completed",
        }

        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "result")
        mixin._provide_workflow_feedback(5, "ok")

        assert 5 not in mixin._waiting_for_feedback

    def test_returns_none_for_unknown_run(self):
        mixin = _make_mixin()
        result = mixin._provide_workflow_feedback(999, "feedback")
        assert result is None

    @patch("distr.core.workflow.router.StepRouter")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._dispatch_next_after_feedback")
    def test_dispatches_next_step_on_next_step_decision(self, mock_dispatch, MockRouter):
        mock_router = MockRouter.return_value
        mock_router.resume_from_feedback.return_value = {
            "action": "next_step", "step_id": 20, "wait_before_next": 0,
        }

        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "result")
        decision = mixin._provide_workflow_feedback(5, "continue please")

        assert decision["action"] == "next_step"
        mock_dispatch.assert_called_once_with(1, 5, 20, 0)

    @patch("distr.core.workflow.router.StepRouter")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._dispatch_next_after_feedback")
    def test_does_not_dispatch_on_end_run(self, mock_dispatch, MockRouter):
        mock_router = MockRouter.return_value
        mock_router.resume_from_feedback.return_value = {
            "action": "end_run", "status": "completed",
        }

        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "result")
        mixin._provide_workflow_feedback(5, "done")

        mock_dispatch.assert_not_called()

    @patch("distr.core.workflow.router.StepRouter")
    def test_handles_router_exception(self, MockRouter):
        mock_router = MockRouter.return_value
        mock_router.resume_from_feedback.side_effect = RuntimeError("db error")

        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "result")
        decision = mixin._provide_workflow_feedback(5, "feedback")

        assert decision["action"] == "end_run"
        assert decision["status"] == "failed"
        assert "db error" in decision["error"]

    @patch("distr.core.workflow.router.StepRouter")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._dispatch_next_after_feedback")
    def test_passes_wait_before_next_to_dispatch(self, mock_dispatch, MockRouter):
        mock_router = MockRouter.return_value
        mock_router.resume_from_feedback.return_value = {
            "action": "next_step", "step_id": 30, "wait_before_next": 2000,
        }

        mixin = _make_mixin()
        mixin._on_step_waiting_for_feedback(10, 1, 5, "result")
        mixin._provide_workflow_feedback(5, "go ahead")

        mock_dispatch.assert_called_once_with(1, 5, 30, 2000)
