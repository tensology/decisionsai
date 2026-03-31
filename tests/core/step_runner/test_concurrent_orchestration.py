"""Unit tests for concurrent orchestration and WorkflowAgent routing (tasks 6.3–6.5).

6.3 — Concurrent orchestration creation: multiple session_ids coexist in _step_runner_orchestrations.
6.4 — Orchestration cleanup on finish and cancel: WorkflowAgent.shutdown() called, entry removed.
6.5 — _send_step_runner_instruction routes to WorkflowAgent, not signal_manager.send_text_input.
"""

import sys
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies that aren't available in the test environment.
# These must be set BEFORE importing distr.app.step_runner.
# ---------------------------------------------------------------------------
_mock_qt = MagicMock()
for mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui"):
    sys.modules.setdefault(mod, _mock_qt)

# sqlalchemy and DB layer
_mock_sa = MagicMock()
for mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext", "sqlalchemy.ext.declarative"):
    sys.modules.setdefault(mod, _mock_sa)

# Mock the DB module hierarchy so local imports inside step_runner methods work
_mock_db_pkg = MagicMock()
sys.modules.setdefault("distr.core.db", _mock_db_pkg)
sys.modules.setdefault("distr.core.db.step_runner", MagicMock())

# Mock step_runner sub-modules used by local imports
sys.modules.setdefault("distr.core.step_runner.service", MagicMock())
sys.modules.setdefault("distr.core.step_runner.context_assembly", MagicMock())
sys.modules.setdefault("distr.core.step_runner.agent_bridge", MagicMock())
sys.modules.setdefault("distr.core.step_runner.scheduler", MagicMock())
sys.modules.setdefault("distr.gui.web.step_runner_events", MagicMock())
sys.modules.setdefault("distr.gui", MagicMock())
sys.modules.setdefault("distr.gui.web", MagicMock())

from distr.app.step_runner import StepRunnerMixin  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_steps(count=2, start_id=100):
    """Generate a list of step dicts."""
    return [
        {"id": start_id + i, "title": f"Step {i + 1}", "instruction": f"Do step {i + 1}"}
        for i in range(count)
    ]


def _make_mock_workflow_agent():
    """Create a mock WorkflowAgent with the expected interface."""
    agent = MagicMock()
    agent.shutdown = MagicMock()
    agent.execute = MagicMock(return_value="agent response")
    return agent


def _make_mock_event_loop():
    """Create a mock asyncio event loop."""
    loop = MagicMock()
    loop.call_soon_threadsafe = MagicMock()
    return loop


def _setup_mixin():
    """Create a StepRunnerMixin with common methods mocked."""
    mixin = StepRunnerMixin()
    mixin._step_runner_orchestrations = {}
    mixin._set_step_status = MagicMock()
    mixin._reset_step_runner_timeout = MagicMock()
    mixin._cancel_step_runner_timeout = MagicMock()
    mixin._send_step_runner_instruction = MagicMock()
    mixin.chat_manager = None
    return mixin


def _start_orchestration(mixin, session_id, steps=None, run_id=None):
    """Insert a mock orchestration into the mixin's dict."""
    if steps is None:
        steps = _make_steps()
    mock_agent = _make_mock_workflow_agent()
    mock_loop = _make_mock_event_loop()

    orch = {
        "session_id": session_id,
        "run_id": run_id,
        "steps_data": steps,
        "current_index": 0,
        "is_retry": False,
        "retry_count": 0,
        "max_retries": 2,
        "on_failure": "skip",
        "is_verification_step": False,
        "prior_results": [],
        "session_instruction": "Test instruction",
        "chat_id": None,
        "any_step_succeeded": False,
        "session_type": "instruction",
        "workflow_agent": mock_agent,
        "agent_loop": mock_loop,
        "agent_thread": MagicMock(),
        "_advancing": False,
    }
    mixin._step_runner_orchestrations[session_id] = orch
    return orch, mock_agent, mock_loop


# ===========================================================================
# 6.3 — Concurrent orchestration creation
# ===========================================================================

class TestConcurrentOrchestrationCreation:
    """Multiple session_ids coexist in _step_runner_orchestrations."""

    def test_two_orchestrations_coexist(self):
        """Two orchestrations with different session_ids can exist simultaneously."""
        mixin = _setup_mixin()

        orch_1, _, _ = _start_orchestration(mixin, session_id=1)
        orch_2, _, _ = _start_orchestration(mixin, session_id=2)

        assert 1 in mixin._step_runner_orchestrations
        assert 2 in mixin._step_runner_orchestrations
        assert mixin._step_runner_orchestrations[1] is orch_1
        assert mixin._step_runner_orchestrations[2] is orch_2

    def test_three_orchestrations_coexist(self):
        """Three concurrent orchestrations are all accessible."""
        mixin = _setup_mixin()

        for sid in [10, 20, 30]:
            _start_orchestration(mixin, session_id=sid)

        assert len(mixin._step_runner_orchestrations) == 3
        assert set(mixin._step_runner_orchestrations.keys()) == {10, 20, 30}

    def test_orchestrations_have_independent_state(self):
        """Each orchestration has its own steps, agent, and progress."""
        mixin = _setup_mixin()

        steps_a = _make_steps(3, start_id=100)
        steps_b = _make_steps(2, start_id=200)

        orch_a, _, _ = _start_orchestration(mixin, session_id=1, steps=steps_a)
        orch_b, _, _ = _start_orchestration(mixin, session_id=2, steps=steps_b)

        # Advance orchestration A
        orch_a["current_index"] = 2
        orch_a["any_step_succeeded"] = True

        # Orchestration B should be unaffected
        assert orch_b["current_index"] == 0
        assert orch_b["any_step_succeeded"] is False
        assert len(orch_a["steps_data"]) == 3
        assert len(orch_b["steps_data"]) == 2

    def test_orchestrations_have_separate_workflow_agents(self):
        """Each orchestration gets its own WorkflowAgent instance."""
        mixin = _setup_mixin()

        _, agent_a, _ = _start_orchestration(mixin, session_id=1)
        _, agent_b, _ = _start_orchestration(mixin, session_id=2)

        assert agent_a is not agent_b
        assert mixin._step_runner_orchestrations[1]["workflow_agent"] is agent_a
        assert mixin._step_runner_orchestrations[2]["workflow_agent"] is agent_b

    @patch("distr.app.step_runner.WorkflowAgent")
    @patch("distr.app.step_runner.StepRunnerMixin._resolve_step_runner_chat_id", return_value=(None, "test"))
    def test_start_orchestration_rejects_duplicate_session_id(self, mock_resolve, mock_wa_cls):
        """Starting an orchestration with an already-active session_id is rejected."""
        mock_wa_cls.return_value = MagicMock()

        mixin = _setup_mixin()
        steps = _make_steps(1)

        # Manually insert an existing orchestration for session_id=1
        existing = {"session_id": 1, "marker": "original"}
        mixin._step_runner_orchestrations[1] = existing

        # Try to start another orchestration for the same session_id
        mixin._start_step_runner_orchestration(1, "run-1", steps, "instruction")

        # The existing orchestration should not be replaced
        assert mixin._step_runner_orchestrations[1]["marker"] == "original"


# ===========================================================================
# 6.4 — Orchestration cleanup on finish and cancel
# ===========================================================================

class TestOrchestrationCleanup:
    """WorkflowAgent.shutdown() called and entry removed on finish/cancel."""

    @patch("distr.core.step_runner.agent_bridge.WorkflowAgentBridge")
    @patch("distr.gui.web.step_runner_events.increment_step_runner_updated")
    @patch("distr.core.db.get_session")
    def test_finish_calls_shutdown_and_removes_entry(self, mock_db, mock_inc, mock_bridge):
        """_finish_step_runner_orchestration shuts down the agent and removes the entry."""
        mock_db_ctx = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_db_ctx)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_db_ctx.query.return_value.filter.return_value.first.return_value = MagicMock()

        mixin = _setup_mixin()
        orch, mock_agent, mock_loop = _start_orchestration(mixin, session_id=42)

        assert 42 in mixin._step_runner_orchestrations

        mixin._finish_step_runner_orchestration(session_id=42, success=True)

        mock_agent.shutdown.assert_called_once()
        mock_loop.call_soon_threadsafe.assert_called_once()
        assert 42 not in mixin._step_runner_orchestrations

    @patch("distr.core.step_runner.agent_bridge.WorkflowAgentBridge")
    @patch("distr.gui.web.step_runner_events.increment_step_runner_updated")
    @patch("distr.core.db.get_session")
    def test_finish_on_failure_still_cleans_up(self, mock_db, mock_inc, mock_bridge):
        """Finishing with success=False still shuts down the agent."""
        mock_db_ctx = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_db_ctx)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_db_ctx.query.return_value.filter.return_value.first.return_value = MagicMock()

        mixin = _setup_mixin()
        _, mock_agent, _ = _start_orchestration(mixin, session_id=7)

        mixin._finish_step_runner_orchestration(session_id=7, success=False)

        mock_agent.shutdown.assert_called_once()
        assert 7 not in mixin._step_runner_orchestrations

    @patch("distr.core.step_runner.agent_bridge.WorkflowAgentBridge")
    @patch("distr.gui.web.step_runner_events.increment_step_runner_updated")
    @patch("distr.core.db.get_session")
    def test_cancel_cleans_up_orchestration(self, mock_db, mock_inc, mock_bridge):
        """Cancelling via _on_step_runner_cancel_requested triggers finish with cancelled=True."""
        mock_db_ctx = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_db_ctx)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_db_ctx.query.return_value.filter.return_value.first.return_value = MagicMock()

        mixin = _setup_mixin()
        orch, mock_agent, _ = _start_orchestration(mixin, session_id=99)

        # Replace _finish with a spy to verify it's called with cancelled=True
        original_finish = mixin._finish_step_runner_orchestration
        finish_calls = []

        def spy_finish(**kwargs):
            finish_calls.append(kwargs)
            original_finish(**kwargs)

        mixin._finish_step_runner_orchestration = spy_finish
        mixin._on_step_runner_cancel_requested(99)

        assert len(finish_calls) == 1
        assert finish_calls[0]["session_id"] == 99
        assert finish_calls[0]["success"] is False
        assert finish_calls[0]["cancelled"] is True
        assert 99 not in mixin._step_runner_orchestrations

    @patch("distr.core.step_runner.agent_bridge.WorkflowAgentBridge")
    @patch("distr.gui.web.step_runner_events.increment_step_runner_updated")
    @patch("distr.core.db.get_session")
    def test_finish_one_does_not_affect_other(self, mock_db, mock_inc, mock_bridge):
        """Finishing one orchestration leaves others intact."""
        mock_db_ctx = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_db_ctx)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        mock_db_ctx.query.return_value.filter.return_value.first.return_value = MagicMock()

        mixin = _setup_mixin()
        _, agent_a, _ = _start_orchestration(mixin, session_id=1)
        _, agent_b, _ = _start_orchestration(mixin, session_id=2)

        mixin._finish_step_runner_orchestration(session_id=1, success=True)

        agent_a.shutdown.assert_called_once()
        agent_b.shutdown.assert_not_called()
        assert 1 not in mixin._step_runner_orchestrations
        assert 2 in mixin._step_runner_orchestrations

    @patch("distr.core.step_runner.agent_bridge.WorkflowAgentBridge")
    @patch("distr.gui.web.step_runner_events.increment_step_runner_updated")
    @patch("distr.core.db.get_session")
    def test_finish_nonexistent_session_is_noop(self, mock_db, mock_inc, mock_bridge):
        """Finishing a session_id that doesn't exist does nothing."""
        mixin = _setup_mixin()
        # Should not raise
        mixin._finish_step_runner_orchestration(session_id=999, success=True)


# ===========================================================================
# 6.5 — _send_step_runner_instruction routes to WorkflowAgent
# ===========================================================================

class TestSendStepRunnerInstructionRouting:
    """Verify instructions route to WorkflowAgent, not signal_manager.send_text_input."""

    @patch("asyncio.run_coroutine_threadsafe")
    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.step_runner.service.build_step_context_prompt", return_value="built prompt")
    @patch("distr.core.db.get_session")
    def test_agent_instruction_routes_to_workflow_agent(
        self, mock_get_session, mock_build, mock_assemble, mock_run_coro
    ):
        """Agent instruction steps call WorkflowAgent.execute via run_coroutine_threadsafe."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = SimpleNamespace(id=1, context_rules=None, workflow_input=None)
        db_step = SimpleNamespace(id=10, step_type="agent_instruction", config=None)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [db_session, db_step]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_ctx = StepInputContext(workflow_rules="rules")
        mock_assemble.return_value = mock_ctx

        mock_agent = MagicMock()
        mock_loop = MagicMock()

        mixin = StepRunnerMixin()
        orch = {
            "session_id": 1,
            "steps_data": [{"id": 10, "title": "Step 1", "instruction": "Do it"}],
            "prior_results": [],
            "session_instruction": "Test",
            "workflow_agent": mock_agent,
            "agent_loop": mock_loop,
        }

        mixin._send_step_runner_instruction(orch, 0)

        # Should call run_coroutine_threadsafe with the agent's loop
        mock_run_coro.assert_called_once()
        args = mock_run_coro.call_args[0]
        assert args[1] is mock_loop

    @patch("asyncio.run_coroutine_threadsafe")
    def test_prompt_override_routes_to_workflow_agent(self, mock_run_coro):
        """When a prompt override is given, it still routes to WorkflowAgent."""
        mock_agent = MagicMock()
        mock_loop = MagicMock()

        mixin = StepRunnerMixin()
        orch = {
            "session_id": 1,
            "steps_data": [{"id": 10, "title": "Step 1", "instruction": "Do it"}],
            "prior_results": [],
            "session_instruction": "Test",
            "workflow_agent": mock_agent,
            "agent_loop": mock_loop,
        }

        mixin._send_step_runner_instruction(orch, 0, prompt="custom prompt")

        mock_run_coro.assert_called_once()
        args = mock_run_coro.call_args[0]
        assert args[1] is mock_loop

    @patch("asyncio.run_coroutine_threadsafe")
    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.step_runner.service.build_step_context_prompt", return_value="prompt")
    @patch("distr.core.db.get_session")
    @patch("distr.app.step_runner.signal_manager")
    def test_does_not_use_signal_manager_send_text_input(
        self, mock_signals, mock_get_session, mock_build, mock_assemble, mock_run_coro
    ):
        """Agent instructions must NOT go through signal_manager.send_text_input."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = SimpleNamespace(id=1, context_rules=None, workflow_input=None)
        db_step = SimpleNamespace(id=10, step_type="agent_instruction", config=None)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [db_session, db_step]
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        mock_ctx = StepInputContext(workflow_rules="")
        mock_assemble.return_value = mock_ctx

        mock_agent = MagicMock()
        mock_loop = MagicMock()

        mixin = StepRunnerMixin()
        orch = {
            "session_id": 1,
            "steps_data": [{"id": 10, "title": "Step 1", "instruction": "Do it"}],
            "prior_results": [],
            "session_instruction": "Test",
            "workflow_agent": mock_agent,
            "agent_loop": mock_loop,
        }

        mixin._send_step_runner_instruction(orch, 0)
        mock_signals.send_text_input.emit.assert_not_called()

    def test_missing_workflow_agent_finishes_orchestration(self):
        """If workflow_agent is None, the orchestration is finished with failure."""
        mixin = StepRunnerMixin()
        mixin._finish_step_runner_orchestration = MagicMock()

        orch = {
            "session_id": 5,
            "steps_data": [{"id": 10, "title": "Step 1", "instruction": "Do it"}],
            "prior_results": [],
            "session_instruction": "Test",
            "workflow_agent": None,
            "agent_loop": MagicMock(),
        }

        mixin._send_step_runner_instruction(orch, 0, prompt="test")

        mixin._finish_step_runner_orchestration.assert_called_once_with(
            session_id=5, success=False
        )

    def test_missing_event_loop_finishes_orchestration(self):
        """If agent_loop is None, the orchestration is finished with failure."""
        mixin = StepRunnerMixin()
        mixin._finish_step_runner_orchestration = MagicMock()

        orch = {
            "session_id": 5,
            "steps_data": [{"id": 10, "title": "Step 1", "instruction": "Do it"}],
            "prior_results": [],
            "session_instruction": "Test",
            "workflow_agent": MagicMock(),
            "agent_loop": None,
        }

        mixin._send_step_runner_instruction(orch, 0, prompt="test")

        mixin._finish_step_runner_orchestration.assert_called_once_with(
            session_id=5, success=False
        )
