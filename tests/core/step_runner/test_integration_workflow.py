"""Integration tests for workflow agent orchestration (tasks 8.1–8.3).

8.1 — Full workflow execution lifecycle: start → execute agent-instruction
      steps via WorkflowAgent → finish → verify report delivered via
      WorkflowAgentBridge.
8.2 — Concurrent workflows: two workflows with different workflow_ids both
      complete independently.
8.3 — User chat messages via _on_workflow_execute_step_requested are unaffected
      while a workflow orchestration is running on a WorkflowAgent.
"""

import sys
import asyncio
import queue
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies (same pattern as test_concurrent_orchestration.py)
# ---------------------------------------------------------------------------
_mock_qt = MagicMock()
for mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui"):
    sys.modules.setdefault(mod, _mock_qt)

_mock_sa = MagicMock()
for mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext", "sqlalchemy.ext.declarative"):
    sys.modules.setdefault(mod, _mock_sa)

_mock_db_pkg = MagicMock()
sys.modules.setdefault("distr.core.db", _mock_db_pkg)
sys.modules.setdefault("distr.core.db.step_runner", MagicMock())
sys.modules.setdefault("distr.core.db.workflow", MagicMock())

sys.modules.setdefault("distr.core.workflow_engine.context_assembly", MagicMock())
sys.modules.setdefault("distr.core.workflow.service", MagicMock())
sys.modules.setdefault("distr.core.workflow.scheduler", MagicMock())
sys.modules.setdefault("distr.gui.web.workflow_events", MagicMock())
sys.modules.setdefault("distr.gui", MagicMock())
sys.modules.setdefault("distr.gui.web", MagicMock())

sys.modules.pop("distr.core.workflow_engine.agent_bridge", None)

from distr.app.workflow import WorkflowOrchestrationMixin  # noqa: E402
from distr.core.workflow_engine.agent_bridge import (  # noqa: E402
    WorkflowAgentBridge,
    _agent_report_queue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drain_report_queue():
    """Empty the module-level agent report queue."""
    while True:
        try:
            _agent_report_queue.get_nowait()
        except queue.Empty:
            break


@pytest.fixture(autouse=True)
def _clean_report_queue():
    """Ensure the agent report queue is empty before and after each test."""
    _drain_report_queue()
    yield
    _drain_report_queue()


def _make_steps(count=2, start_id=100):
    return [
        {"id": start_id + i, "title": f"Step {i + 1}", "instruction": f"Do step {i + 1}"}
        for i in range(count)
    ]


def _make_mock_workflow_agent(responses=None):
    """Create a mock WorkflowAgent whose execute() returns canned responses.

    *responses* is a list of strings; each call to execute() pops the next one.
    If exhausted, returns "done".
    """
    agent = MagicMock()
    agent.shutdown = MagicMock()
    _responses = list(responses or ["agent response"])

    async def _execute(instruction):
        return _responses.pop(0) if _responses else "done"

    agent.execute = MagicMock(side_effect=_execute)
    return agent


def _make_mock_event_loop():
    loop = MagicMock()
    loop.call_soon_threadsafe = MagicMock()
    return loop


def _setup_mixin():
    mixin = WorkflowOrchestrationMixin()
    mixin._workflow_orchestrations = {}
    mixin._workflow_timeout_timers = {}
    mixin._set_workflow_step_status = MagicMock()
    mixin._reset_workflow_timeout = MagicMock()
    mixin._cancel_workflow_timeout = MagicMock()
    mixin.chat_manager = None
    return mixin


def _insert_orchestration(mixin, workflow_id, steps=None, run_id=None, agent=None, loop=None):
    """Insert a mock orchestration into the mixin's dict and return it."""
    if steps is None:
        steps = _make_steps()
    mock_agent = agent or _make_mock_workflow_agent(
        responses=[f"Response for step {i + 1}" for i in range(len(steps))]
    )
    mock_loop = loop or _make_mock_event_loop()

    orch = {
        "workflow_id": workflow_id,
        "run_id": run_id,
        "steps_data": steps,
        "current_index": 0,
        "is_retry": False,
        "retry_count": 0,
        "max_retries": 2,
        "on_failure": "skip",
        "is_verification_step": False,
        "prior_results": [],
        "workflow_description": "Test instruction",
        "chat_id": None,
        "any_step_succeeded": False,
        "workflow_type": "instruction",
        "workflow_agent": mock_agent,
        "agent_loop": mock_loop,
        "agent_thread": MagicMock(),
        "_advancing": False,
    }
    mixin._workflow_orchestrations[workflow_id] = orch
    return orch, mock_agent, mock_loop


# ===========================================================================
# 8.1 — Full workflow execution lifecycle
# ===========================================================================

class TestFullWorkflowLifecycle:
    """Integration: start orchestration → execute steps via WorkflowAgent →
    finish → verify report delivered via WorkflowAgentBridge.
    """

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_full_lifecycle_two_steps(self, mock_run_coro, mock_finish_run, mock_sm):
        """Walk through a 2-step orchestration end-to-end.

        1. Insert orchestration with mock WorkflowAgent.
        2. Simulate _send_workflow_instruction for step 0.
        3. Fire the _on_agent_done callback with a response.
        4. Verify advancement to step 1.
        5. Fire _on_agent_done for step 1.
        6. Verify _finish_workflow_orchestration is called.
        7. Verify WorkflowAgentBridge receives the report.
        """
        mixin = _setup_mixin()
        steps = _make_steps(2, start_id=100)
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id=1, steps=steps, run_id=10)

        # --- Step 0: send instruction ---
        mixin._send_workflow_instruction(orch, 0, prompt="Do step 1")

        # run_coroutine_threadsafe should have been called with the agent's loop
        mock_run_coro.assert_called_once()
        coro_arg, loop_arg = mock_run_coro.call_args[0]
        assert loop_arg is mock_loop

        # Capture the done callback
        future_mock = mock_run_coro.return_value
        assert future_mock.add_done_callback.called
        on_done_cb = future_mock.add_done_callback.call_args[0][0]

        # --- Simulate agent completing step 0 ---
        # Create a future-like object that returns a result
        done_future = MagicMock()
        done_future.result.return_value = "Step 1 completed successfully"

        # The callback uses QTimer.singleShot — patch it to call immediately
        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            # Make singleShot call the lambda immediately
            def _call_immediately(ms, fn):
                fn()
            mock_qtimer.singleShot = _call_immediately

            on_done_cb(done_future)

        # After advancing, current_index should be 1
        assert orch["current_index"] == 1
        assert orch["any_step_succeeded"] is True
        assert len(orch["prior_results"]) == 1
        assert orch["prior_results"][0]["title"] == "Step 1"

        # Step 0 should be marked completed
        mixin._set_workflow_step_status.assert_any_call(100, "completed", result="Step 1 completed successfully")
        # Step 1 should be marked running
        mixin._set_workflow_step_status.assert_any_call(101, "running")

        # --- Step 1: send instruction (triggered by advance) ---
        # Reset mock to capture the second call
        mock_run_coro.reset_mock()
        mixin._send_workflow_instruction(orch, 1, prompt="Do step 2")

        mock_run_coro.assert_called_once()
        future_mock_2 = mock_run_coro.return_value
        on_done_cb_2 = future_mock_2.add_done_callback.call_args[0][0]

        # --- Simulate agent completing step 1 → triggers finish ---
        done_future_2 = MagicMock()
        done_future_2.result.return_value = "Step 2 done"

        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            mock_qtimer.singleShot = _call_immediately
            on_done_cb_2(done_future_2)

        # Orchestration should be removed (finished)
        assert 1 not in mixin._workflow_orchestrations

        # WorkflowAgent.shutdown() should have been called
        mock_agent.shutdown.assert_called_once()

        # Event loop should have been stopped
        mock_loop.call_soon_threadsafe.assert_called_once()

        # --- Verify report delivered via WorkflowAgentBridge ---
        reports = WorkflowAgentBridge.get_pending_reports()
        assert len(reports) == 1
        assert reports[0]["session_id"] == 1
        assert "Completed successfully" in reports[0]["report"]
        assert "Step 1" in reports[0]["report"]
        assert "Step 2" in reports[0]["report"]

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_agent_error_triggers_retry_then_finish(self, mock_run_coro, mock_finish_run, mock_sm):
        """When the agent raises an exception, the error handler retries then finishes."""
        mixin = _setup_mixin()
        steps = _make_steps(1, start_id=200)
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id=5, steps=steps, run_id=20)
        orch["max_retries"] = 0  # No retries — fail immediately

        mixin._send_workflow_instruction(orch, 0, prompt="Do it")

        future_mock = mock_run_coro.return_value
        on_done_cb = future_mock.add_done_callback.call_args[0][0]

        # Simulate agent raising an exception
        error_future = MagicMock()
        error_future.result.side_effect = RuntimeError("LLM exploded")

        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            def _call_immediately(ms, fn):
                fn()
            mock_qtimer.singleShot = _call_immediately
            on_done_cb(error_future)

        # With max_retries=0, the step should be marked failed and orchestration finished
        assert 5 not in mixin._workflow_orchestrations
        mock_agent.shutdown.assert_called_once()

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_single_step_workflow_completes(self, mock_run_coro, mock_finish_run, mock_sm):
        """A single-step workflow completes and delivers a report."""
        mixin = _setup_mixin()
        steps = _make_steps(1, start_id=300)
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id=10, steps=steps, run_id=30)

        mixin._send_workflow_instruction(orch, 0, prompt="Only step")

        future_mock = mock_run_coro.return_value
        on_done_cb = future_mock.add_done_callback.call_args[0][0]

        done_future = MagicMock()
        done_future.result.return_value = "All done"

        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            def _call_immediately(ms, fn):
                fn()
            mock_qtimer.singleShot = _call_immediately
            on_done_cb(done_future)

        assert 10 not in mixin._workflow_orchestrations
        mock_agent.shutdown.assert_called_once()

        reports = WorkflowAgentBridge.get_pending_reports()
        assert len(reports) == 1
        assert reports[0]["session_id"] == 10


# ===========================================================================
# 8.2 — Concurrent workflows complete independently
# ===========================================================================

class TestConcurrentWorkflows:
    """Integration: two workflows with different workflow_ids both complete
    independently without interfering with each other.
    """

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_two_workflows_complete_independently(self, mock_run_coro, mock_finish_run, mock_sm):
        """Start two orchestrations, advance both, verify both complete and
        produce independent reports.
        """
        mixin = _setup_mixin()

        steps_a = _make_steps(2, start_id=100)
        steps_b = _make_steps(2, start_id=200)

        orch_a, agent_a, loop_a = _insert_orchestration(mixin, workflow_id=1, steps=steps_a, run_id=10)
        orch_b, agent_b, loop_b = _insert_orchestration(mixin, workflow_id=2, steps=steps_b, run_id=20)

        # Both orchestrations exist
        assert 1 in mixin._workflow_orchestrations
        assert 2 in mixin._workflow_orchestrations

        # --- Send instruction for workflow A step 0 ---
        mixin._send_workflow_instruction(orch_a, 0, prompt="A step 1")
        future_a0 = mock_run_coro.return_value
        cb_a0 = future_a0.add_done_callback.call_args[0][0]

        # --- Send instruction for workflow B step 0 ---
        mock_run_coro.reset_mock()
        mixin._send_workflow_instruction(orch_b, 0, prompt="B step 1")
        future_b0 = mock_run_coro.return_value
        cb_b0 = future_b0.add_done_callback.call_args[0][0]

        def _call_immediately(ms, fn):
            fn()

        # --- Complete workflow A step 0 ---
        done_a0 = MagicMock()
        done_a0.result.return_value = "A step 1 done"
        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            mock_qtimer.singleShot = _call_immediately
            cb_a0(done_a0)

        # A advanced to step 1, B still at step 0
        assert orch_a["current_index"] == 1
        assert orch_b["current_index"] == 0
        assert orch_a["any_step_succeeded"] is True
        assert orch_b["any_step_succeeded"] is False

        # --- Complete workflow B step 0 ---
        done_b0 = MagicMock()
        done_b0.result.return_value = "B step 1 done"
        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            mock_qtimer.singleShot = _call_immediately
            cb_b0(done_b0)

        assert orch_b["current_index"] == 1
        assert orch_b["any_step_succeeded"] is True

        # --- Complete workflow A step 1 → finishes A ---
        mock_run_coro.reset_mock()
        mixin._send_workflow_instruction(orch_a, 1, prompt="A step 2")
        future_a1 = mock_run_coro.return_value
        cb_a1 = future_a1.add_done_callback.call_args[0][0]

        done_a1 = MagicMock()
        done_a1.result.return_value = "A step 2 done"
        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            mock_qtimer.singleShot = _call_immediately
            cb_a1(done_a1)

        # A is finished, B is still running
        assert 1 not in mixin._workflow_orchestrations
        assert 2 in mixin._workflow_orchestrations
        agent_a.shutdown.assert_called_once()
        agent_b.shutdown.assert_not_called()

        # --- Complete workflow B step 1 → finishes B ---
        mock_run_coro.reset_mock()
        mixin._send_workflow_instruction(orch_b, 1, prompt="B step 2")
        future_b1 = mock_run_coro.return_value
        cb_b1 = future_b1.add_done_callback.call_args[0][0]

        done_b1 = MagicMock()
        done_b1.result.return_value = "B step 2 done"
        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            mock_qtimer.singleShot = _call_immediately
            cb_b1(done_b1)

        assert 2 not in mixin._workflow_orchestrations
        agent_b.shutdown.assert_called_once()

        # --- Verify both reports delivered independently ---
        reports = WorkflowAgentBridge.get_pending_reports()
        assert len(reports) == 2

        session_ids = {r["session_id"] for r in reports}
        assert session_ids == {1, 2}

        for r in reports:
            assert "Completed successfully" in r["report"]

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_finishing_one_does_not_affect_other(self, mock_run_coro, mock_finish_run, mock_sm):
        """Finishing workflow A does not touch workflow B's state."""
        mixin = _setup_mixin()

        steps_a = _make_steps(1, start_id=100)
        steps_b = _make_steps(3, start_id=200)

        orch_a, agent_a, loop_a = _insert_orchestration(mixin, workflow_id=10, steps=steps_a, run_id=1)
        orch_b, agent_b, loop_b = _insert_orchestration(mixin, workflow_id=20, steps=steps_b, run_id=2)

        # Finish A
        mixin._finish_workflow_orchestration(workflow_id=10, success=True)

        # A is gone
        assert 10 not in mixin._workflow_orchestrations
        agent_a.shutdown.assert_called_once()

        # B is untouched
        assert 20 in mixin._workflow_orchestrations
        agent_b.shutdown.assert_not_called()
        assert orch_b["current_index"] == 0

    @patch("distr.core.signals.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_cancel_one_workflow_other_continues(self, mock_run_coro, mock_finish_run, mock_sm):
        """Cancelling one workflow leaves the other running."""
        mixin = _setup_mixin()

        orch_a, agent_a, _ = _insert_orchestration(mixin, workflow_id=100, run_id=1)
        orch_b, agent_b, _ = _insert_orchestration(mixin, workflow_id=200, run_id=2)

        # Cancel A
        mixin._on_workflow_cancel_requested(100)

        assert 100 not in mixin._workflow_orchestrations
        assert 200 in mixin._workflow_orchestrations
        agent_a.shutdown.assert_called_once()
        agent_b.shutdown.assert_not_called()


# ===========================================================================
# 8.3 — User chat messages unaffected while workflow is running
# ===========================================================================

class TestUserChatUnaffectedDuringWorkflow:
    """Integration: _on_workflow_execute_step_requested (single-step execution
    via the main agent) still works via signal_manager.send_text_input while
    a workflow orchestration is running on a separate WorkflowAgent.

    The two paths — workflow agent execution and single-step main-agent
    execution — must be completely independent.
    """

    @patch("distr.app.workflow.signal_manager")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_single_step_uses_signal_while_workflow_runs(self, mock_run_coro, mock_sm):
        """_on_workflow_execute_step_requested emits send_text_input even when
        a workflow orchestration is active on a WorkflowAgent.
        """
        mixin = _setup_mixin()
        mixin._resolve_workflow_chat_id = MagicMock(return_value=(42, "workflow instr"))

        # Start a workflow orchestration on workflow 1
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id=1)

        # Now execute a single step via the main agent path (different workflow)
        mixin._on_workflow_execute_step_requested(
            step_id=999, workflow_id=50, instruction="User single step", chat_id=42
        )

        # signal_manager.send_text_input.emit should be called (main agent path)
        mock_sm.send_text_input.emit.assert_called_once_with("User single step", False, None, None)

        # The workflow orchestration should be completely unaffected
        assert 1 in mixin._workflow_orchestrations
        assert mixin._workflow_orchestrations[1]["current_index"] == 0
        mock_agent.shutdown.assert_not_called()

    @patch("distr.app.workflow.signal_manager")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_workflow_does_not_use_signal_manager(self, mock_run_coro, mock_sm):
        """Workflow step execution routes to WorkflowAgent.execute(), never
        through signal_manager.send_text_input.
        """
        mixin = _setup_mixin()
        steps = _make_steps(1, start_id=500)
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id=1, steps=steps)

        # Send a workflow instruction
        mixin._send_workflow_instruction(orch, 0, prompt="Workflow instruction")

        # run_coroutine_threadsafe should be called (WorkflowAgent path)
        mock_run_coro.assert_called_once()
        coro_arg, loop_arg = mock_run_coro.call_args[0]
        assert loop_arg is mock_loop

        # signal_manager.send_text_input should NOT be called
        mock_sm.send_text_input.emit.assert_not_called()

    @patch("distr.app.workflow.signal_manager")
    @patch("distr.app.workflow.WorkflowOrchestrationMixin._finish_workflow_run")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_both_paths_work_simultaneously(self, mock_run_coro, mock_finish_run, mock_sm):
        """A workflow step and a single-step execution can happen at the same
        time without interfering with each other.
        """
        mixin = _setup_mixin()
        mixin._resolve_workflow_chat_id = MagicMock(return_value=(42, "workflow instr"))

        steps = _make_steps(2, start_id=100)
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id=1, steps=steps, run_id=5)

        # --- Start workflow step 0 ---
        mixin._send_workflow_instruction(orch, 0, prompt="Workflow step 1")
        future_mock = mock_run_coro.return_value
        cb = future_mock.add_done_callback.call_args[0][0]

        # --- While workflow is in-flight, execute a single step via main agent ---
        mixin._on_workflow_execute_step_requested(
            step_id=888, workflow_id=77, instruction="User wants this done", chat_id=42
        )
        mock_sm.send_text_input.emit.assert_called_once_with("User wants this done", False, None, None)

        # Verify _pending_single_step was set for the single-step path
        assert mixin._pending_single_step is not None
        assert mixin._pending_single_step["step_id"] == 888
        assert mixin._pending_single_step["workflow_id"] == 77

        # --- Now complete the workflow step ---
        def _call_immediately(ms, fn):
            fn()

        done_future = MagicMock()
        done_future.result.return_value = "Workflow step 1 done"
        with patch("distr.app.workflow.QTimer") as mock_qtimer:
            mock_qtimer.singleShot = _call_immediately
            cb(done_future)

        # Workflow advanced to step 1
        assert orch["current_index"] == 1
        assert orch["any_step_succeeded"] is True

        # The single-step state is independent — still set from the execute_requested call
        assert mixin._pending_single_step["step_id"] == 888

    @patch("distr.app.workflow.signal_manager")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_multiple_single_steps_while_workflow_runs(self, mock_run_coro, mock_sm):
        """Multiple single-step executions can be triggered while a workflow runs."""
        mixin = _setup_mixin()
        mixin._resolve_workflow_chat_id = MagicMock(return_value=(10, "instr"))

        # Active workflow
        _insert_orchestration(mixin, workflow_id=1)

        # Fire multiple single-step executions
        for i in range(3):
            mock_sm.send_text_input.emit.reset_mock()
            mixin._on_workflow_execute_step_requested(
                step_id=i, workflow_id=50 + i, instruction=f"Single step {i}", chat_id=10
            )
            mock_sm.send_text_input.emit.assert_called_once_with(f"Single step {i}", False, None, None)

        # Workflow is still running, unaffected
        assert 1 in mixin._workflow_orchestrations
