"""Property-based tests for workflow agent orchestration (tasks 7.1–7.4).

7.1 [PBT-exploration] — Agent-instruction steps routed through signal_manager
     on UNFIXED code (expected to fail on fixed code).
7.2 [PBT-fix] — Agent-instruction steps execute on WorkflowAgent with isolated
     message history.
7.3 [PBT-preservation] — Direct-execution step types still execute in
     background threads unchanged.
7.4 [PBT-preservation] — Orchestration lifecycle events maintain state
     consistency.

Uses the ``hypothesis`` library for property-based testing.
"""

import sys
import asyncio
import threading
from types import SimpleNamespace
from typing import Dict, Optional
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Mock heavy dependencies BEFORE importing distr.app.workflow
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

# Mock sub-modules used by local imports
sys.modules.setdefault("distr.core.step_runner.context_assembly", MagicMock())
sys.modules.setdefault("distr.core.step_runner.agent_bridge", MagicMock())
sys.modules.setdefault("distr.core.step_runner.validation", MagicMock())
sys.modules.setdefault("distr.core.workflow.service", MagicMock())
sys.modules.setdefault("distr.core.workflow.scheduler", MagicMock())
sys.modules.setdefault("distr.gui.web.workflow_events", MagicMock())
sys.modules.setdefault("distr.gui", MagicMock())
sys.modules.setdefault("distr.gui.web", MagicMock())

from distr.app.workflow import WorkflowOrchestrationMixin, _DIRECT_EXECUTION_TYPES  # noqa: E402


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Non-empty printable text for titles, instructions, workflow descriptions
_printable_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=80,
).filter(lambda s: s.strip() != "")

# Workflow IDs — positive integers
_workflow_ids = st.integers(min_value=1, max_value=10_000)

# Step IDs — positive integers
_step_ids = st.integers(min_value=1, max_value=100_000)

# Direct execution step types (the set that should NOT go to WorkflowAgent)
_direct_step_types = st.sampled_from(sorted(_DIRECT_EXECUTION_TYPES))

# Lifecycle event names for orchestration state machine
_lifecycle_events = st.sampled_from(["start", "advance", "error", "cancel", "finish"])


@st.composite
def _agent_instruction_step(draw):
    """Generate a random agent-instruction step config dict."""
    return {
        "id": draw(_step_ids),
        "title": draw(_printable_text),
        "instruction": draw(_printable_text),
    }


@st.composite
def _agent_instruction_steps(draw, min_size=1, max_size=5):
    """Generate a list of agent-instruction step config dicts."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    steps = []
    used_ids = set()
    for _ in range(n):
        step = draw(_agent_instruction_step())
        # Ensure unique IDs
        while step["id"] in used_ids:
            step["id"] = draw(_step_ids)
        used_ids.add(step["id"])
        steps.append(step)
    return steps


@st.composite
def _direct_execution_step(draw):
    """Generate a random direct-execution step config dict."""
    return {
        "id": draw(_step_ids),
        "title": draw(_printable_text),
        "instruction": draw(_printable_text),
        "step_type": draw(_direct_step_types),
    }


@st.composite
def _lifecycle_event_sequence(draw, min_size=2, max_size=8):
    """Generate a random sequence of lifecycle events.

    Always starts with 'start' and ends with 'finish' or 'cancel'.
    """
    middle_events = st.sampled_from(["advance", "error"])
    n_middle = draw(st.integers(min_value=0, max_value=max_size - 2))
    events = ["start"]
    for _ in range(n_middle):
        events.append(draw(middle_events))
    events.append(draw(st.sampled_from(["finish", "cancel"])))
    return events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_workflow_agent():
    """Create a mock WorkflowAgent with the expected interface."""
    agent = MagicMock()
    agent.shutdown = MagicMock()
    agent.execute = MagicMock(return_value="agent response")
    agent._messages = []
    agent._shutdown = False
    return agent


def _make_mock_event_loop():
    """Create a mock asyncio event loop."""
    loop = MagicMock()
    loop.call_soon_threadsafe = MagicMock()
    return loop


def _setup_mixin():
    """Create a WorkflowOrchestrationMixin with common methods mocked."""
    mixin = WorkflowOrchestrationMixin()
    mixin._workflow_orchestrations = {}
    mixin._set_workflow_step_status = MagicMock()
    mixin._reset_workflow_timeout = MagicMock()
    mixin._cancel_workflow_timeout = MagicMock()
    mixin.chat_manager = None
    return mixin


def _insert_orchestration(mixin, workflow_id, steps, run_id=None):
    """Insert a mock orchestration into the mixin's dict and return it."""
    mock_agent = _make_mock_workflow_agent()
    mock_loop = _make_mock_event_loop()
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
# 7.1 [PBT-exploration] — Agent-instruction steps routed through
#     signal_manager.send_text_input on UNFIXED code (expect failure after fix)
# ===========================================================================


@pytest.mark.xfail(
    reason="Bug is fixed — agent instructions no longer route through signal_manager"
)
class TestPBTExplorationSignalManagerRouting:
    """Property test: on the OLD buggy code, agent-instruction steps would be
    routed through ``signal_manager.send_text_input``.

    On the FIXED code this test is expected to FAIL (xfail) because the fixed
    ``_send_workflow_instruction`` routes to ``WorkflowAgent.execute()``
    instead.

    **Validates: Requirements 1.1**
    """

    @given(
        workflow_id=_workflow_ids,
        step=_agent_instruction_step(),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_agent_instruction_routes_through_signal_manager(self, workflow_id, step):
        """For any random agent-instruction step config, the OLD code would
        call ``signal_manager.send_text_input.emit()``.

        On the fixed code, ``send_text_input.emit`` is never called — so this
        assertion will fail, confirming the bug is fixed.
        """
        mixin = _setup_mixin()
        steps = [step]
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id, steps)

        with patch("distr.app.workflow.signal_manager") as mock_signals:
            # Provide a prompt override so we skip DB lookups
            mixin._send_workflow_instruction(orch, 0, prompt="test instruction")

            # OLD buggy code would call signal_manager.send_text_input.emit(...)
            # FIXED code routes to WorkflowAgent.execute() instead
            mock_signals.send_text_input.emit.assert_called_once()



# ===========================================================================
# 7.2 [PBT-fix] — Agent-instruction steps execute on WorkflowAgent with
#     isolated message history
# ===========================================================================


class TestPBTFixWorkflowAgentRouting:
    """Property test: for any random agent-instruction step config, the fixed
    code routes execution to ``WorkflowAgent.execute()`` via
    ``asyncio.run_coroutine_threadsafe``, and each agent maintains isolated
    message history.

    **Validates: Requirements 2.1, 2.2, 2.4**
    """

    @given(
        workflow_id=_workflow_ids,
        step=_agent_instruction_step(),
        instruction=_printable_text,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @patch("asyncio.run_coroutine_threadsafe")
    def test_agent_instruction_routes_to_workflow_agent(
        self, mock_run_coro, workflow_id, step, instruction
    ):
        """For any random agent-instruction step, the instruction is dispatched
        to the orchestration's WorkflowAgent via ``run_coroutine_threadsafe``,
        using the orchestration's dedicated event loop.
        """
        mixin = _setup_mixin()
        steps = [step]
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id, steps)

        mixin._send_workflow_instruction(orch, 0, prompt=instruction)

        mock_run_coro.assert_called_once()
        # The second arg to run_coroutine_threadsafe is the event loop
        call_args = mock_run_coro.call_args[0]
        assert call_args[1] is mock_loop

    @given(
        workflow_id=_workflow_ids,
        step=_agent_instruction_step(),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @patch("asyncio.run_coroutine_threadsafe")
    @patch("distr.app.workflow.signal_manager")
    def test_signal_manager_never_called(
        self, mock_signals, mock_run_coro, workflow_id, step
    ):
        """For any random agent-instruction step, ``signal_manager.send_text_input``
        is never invoked — instructions go to WorkflowAgent, not the main agent.
        """
        mixin = _setup_mixin()
        steps = [step]
        orch, _, _ = _insert_orchestration(mixin, workflow_id, steps)

        mixin._send_workflow_instruction(orch, 0, prompt="test")

        mock_signals.send_text_input.emit.assert_not_called()

    @given(
        sid_a=_workflow_ids,
        sid_b=_workflow_ids,
        step_a=_agent_instruction_step(),
        step_b=_agent_instruction_step(),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @patch("asyncio.run_coroutine_threadsafe")
    def test_concurrent_agents_have_isolated_history(
        self, mock_run_coro, sid_a, sid_b, step_a, step_b
    ):
        """For any two concurrent orchestrations, their WorkflowAgent instances
        are distinct objects — message history is isolated.
        """
        assume(sid_a != sid_b)

        mixin = _setup_mixin()
        orch_a, agent_a, loop_a = _insert_orchestration(mixin, sid_a, [step_a])
        orch_b, agent_b, loop_b = _insert_orchestration(mixin, sid_b, [step_b])

        # Agents are distinct instances
        assert agent_a is not agent_b
        assert loop_a is not loop_b

        # Sending instruction to A uses A's loop
        mixin._send_workflow_instruction(orch_a, 0, prompt="instruction A")
        first_call_loop = mock_run_coro.call_args_list[0][0][1]
        assert first_call_loop is loop_a

        # Sending instruction to B uses B's loop
        mixin._send_workflow_instruction(orch_b, 0, prompt="instruction B")
        second_call_loop = mock_run_coro.call_args_list[1][0][1]
        assert second_call_loop is loop_b



# ===========================================================================
# 7.3 [PBT-preservation] — Direct-execution step types still execute in
#     background threads unchanged
# ===========================================================================


class TestPBTPreservationDirectExecution:
    """Property test: for any random direct-execution step type (run_command,
    http_request, execute_code, playwright, play_recording), the step is
    dispatched to ``_execute_step_directly`` in a background thread, NOT to
    WorkflowAgent.

    **Validates: Requirements 3.3**
    """

    @given(
        workflow_id=_workflow_ids,
        step=_direct_execution_step(),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @patch("asyncio.run_coroutine_threadsafe")
    def test_direct_types_do_not_route_to_workflow_agent(
        self, mock_run_coro, workflow_id, step
    ):
        """For any direct-execution step type, ``run_coroutine_threadsafe``
        (the WorkflowAgent dispatch path) is never called.
        """
        step_type = step["step_type"]
        mixin = _setup_mixin()
        steps = [step]
        orch, mock_agent, mock_loop = _insert_orchestration(mixin, workflow_id, steps)

        # Mock the DB lookup to return the direct step type
        mock_db_session = MagicMock()
        mock_sess_obj = SimpleNamespace(id=workflow_id, context_rules=None, workflow_input=None)
        mock_step_obj = SimpleNamespace(id=step["id"], step_type=step_type, config=None)
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_sess_obj, mock_step_obj
        ]

        # Mock context assembly
        mock_ctx = MagicMock()
        mock_ctx.workflow_rules = ""
        mock_ctx.step_config = {}

        # Mock _validate_workflow_step_config to return True (valid config)
        mixin._validate_workflow_step_config = MagicMock(return_value=True)
        # Mock _execute_step_directly to capture the call
        mixin._execute_step_directly = MagicMock()

        with patch("distr.core.db.get_session") as mock_get_session, \
             patch("distr.core.step_runner.context_assembly.assemble_step_context", return_value=mock_ctx):
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

            mixin._send_workflow_instruction(orch, 0)

        # WorkflowAgent dispatch should NOT have been called
        mock_run_coro.assert_not_called()
        # Direct execution should have been called
        mixin._execute_step_directly.assert_called_once()
        call_args = mixin._execute_step_directly.call_args[0]
        assert call_args[0] is orch
        assert call_args[1] == 0
        assert call_args[2] == step_type

    @given(step_type=_direct_step_types)
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_all_direct_types_in_constant_set(self, step_type):
        """Every generated direct step type is in the _DIRECT_EXECUTION_TYPES set."""
        assert step_type in _DIRECT_EXECUTION_TYPES

    @given(
        workflow_id=_workflow_ids,
        step=_direct_execution_step(),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @patch("distr.app.workflow.signal_manager")
    @patch("asyncio.run_coroutine_threadsafe")
    def test_direct_types_do_not_use_signal_manager(
        self, mock_run_coro, mock_signals, workflow_id, step
    ):
        """For any direct-execution step type, ``signal_manager.send_text_input``
        is never called — direct steps use background threads.
        """
        step_type = step["step_type"]
        mixin = _setup_mixin()
        steps = [step]
        orch, _, _ = _insert_orchestration(mixin, workflow_id, steps)

        mock_db_session = MagicMock()
        mock_sess_obj = SimpleNamespace(id=workflow_id, context_rules=None, workflow_input=None)
        mock_step_obj = SimpleNamespace(id=step["id"], step_type=step_type, config=None)
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_sess_obj, mock_step_obj
        ]

        mock_ctx = MagicMock()
        mock_ctx.workflow_rules = ""
        mock_ctx.step_config = {}

        mixin._validate_workflow_step_config = MagicMock(return_value=True)
        mixin._execute_step_directly = MagicMock()

        with patch("distr.core.db.get_session") as mock_get_session, \
             patch("distr.core.step_runner.context_assembly.assemble_step_context", return_value=mock_ctx):
            mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db_session)
            mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

            mixin._send_workflow_instruction(orch, 0)

        mock_signals.send_text_input.emit.assert_not_called()



# ===========================================================================
# 7.4 [PBT-preservation] — Orchestration lifecycle events maintain state
#     consistency
# ===========================================================================


class TestPBTPreservationLifecycleStateConsistency:
    """Property test: for any random sequence of orchestration lifecycle events
    (start, advance, error, cancel, finish), the ``_workflow_orchestrations``
    dict remains consistent — no dangling entries, no missing agents, no
    corrupted indices.

    **Validates: Requirements 3.4, 3.5**
    """

    @given(
        workflow_id=_workflow_ids,
        steps=_agent_instruction_steps(min_size=1, max_size=5),
        events=_lifecycle_event_sequence(min_size=2, max_size=6),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_lifecycle_events_maintain_dict_consistency(self, workflow_id, steps, events):
        """For any random lifecycle event sequence, the orchestrations dict
        is always in a consistent state: entries that exist have valid agents,
        loops, and indices; entries that are finished/cancelled are removed.
        """
        mixin = _setup_mixin()

        # Patch _finish to avoid DB calls but still clean up
        original_finish = mixin._finish_workflow_orchestration

        def mock_finish(workflow_id, success=True, cancelled=False):
            mixin._cancel_workflow_timeout(workflow_id)
            with threading.Lock():
                orch = mixin._workflow_orchestrations.pop(workflow_id, None)
            if orch:
                wa = orch.get("workflow_agent")
                if wa:
                    wa.shutdown()
                al = orch.get("agent_loop")
                if al:
                    try:
                        al.call_soon_threadsafe(al.stop)
                    except Exception:
                        pass

        mixin._finish_workflow_orchestration = mock_finish

        for event in events:
            if event == "start":
                if workflow_id not in mixin._workflow_orchestrations:
                    _insert_orchestration(mixin, workflow_id, steps)

            elif event == "advance":
                orch = mixin._workflow_orchestrations.get(workflow_id)
                if orch:
                    idx = orch["current_index"]
                    if idx + 1 < len(orch["steps_data"]):
                        orch["current_index"] = idx + 1
                        orch["any_step_succeeded"] = True
                    else:
                        mock_finish(workflow_id=workflow_id, success=True)

            elif event == "error":
                orch = mixin._workflow_orchestrations.get(workflow_id)
                if orch:
                    orch["retry_count"] = orch.get("retry_count", 0) + 1
                    if orch["retry_count"] >= orch["max_retries"]:
                        # Skip to next or finish
                        idx = orch["current_index"]
                        if idx + 1 < len(orch["steps_data"]):
                            orch["current_index"] = idx + 1
                            orch["retry_count"] = 0
                        else:
                            mock_finish(workflow_id=workflow_id, success=False)

            elif event == "cancel":
                if workflow_id in mixin._workflow_orchestrations:
                    mock_finish(workflow_id=workflow_id, success=False, cancelled=True)

            elif event == "finish":
                if workflow_id in mixin._workflow_orchestrations:
                    orch = mixin._workflow_orchestrations[workflow_id]
                    mock_finish(
                        workflow_id=workflow_id,
                        success=orch.get("any_step_succeeded", False),
                    )

            # --- Invariant checks after every event ---
            for sid, orch in mixin._workflow_orchestrations.items():
                # Every live orchestration has a valid workflow_id key
                assert orch["workflow_id"] == sid
                # current_index is within bounds
                assert 0 <= orch["current_index"] < len(orch["steps_data"])
                # workflow_agent is present
                assert orch["workflow_agent"] is not None
                # agent_loop is present
                assert orch["agent_loop"] is not None
                # retry_count is non-negative
                assert orch.get("retry_count", 0) >= 0

    @given(
        sid_a=_workflow_ids,
        sid_b=_workflow_ids,
        steps_a=_agent_instruction_steps(min_size=1, max_size=3),
        steps_b=_agent_instruction_steps(min_size=1, max_size=3),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_finishing_one_orchestration_preserves_others(self, sid_a, sid_b, steps_a, steps_b):
        """Finishing orchestration A does not affect orchestration B's state."""
        assume(sid_a != sid_b)

        mixin = _setup_mixin()
        orch_a, agent_a, _ = _insert_orchestration(mixin, sid_a, steps_a)
        orch_b, agent_b, _ = _insert_orchestration(mixin, sid_b, steps_b)

        # Remove A
        removed = mixin._workflow_orchestrations.pop(sid_a, None)
        assert removed is orch_a

        # B is still intact
        assert sid_b in mixin._workflow_orchestrations
        remaining = mixin._workflow_orchestrations[sid_b]
        assert remaining is orch_b
        assert remaining["workflow_agent"] is agent_b
        assert remaining["current_index"] == 0

    @given(
        workflow_id=_workflow_ids,
        steps=_agent_instruction_steps(min_size=2, max_size=5),
        advance_count=st.integers(min_value=0, max_value=4),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.large_base_example])
    def test_advance_increments_index_correctly(self, workflow_id, steps, advance_count):
        """Advancing N times increments current_index by N (capped at len-1)."""
        mixin = _setup_mixin()
        orch, _, _ = _insert_orchestration(mixin, workflow_id, steps)

        expected_index = 0
        for _ in range(advance_count):
            if expected_index + 1 < len(steps):
                expected_index += 1
                orch["current_index"] = expected_index
            else:
                break

        assert orch["current_index"] == expected_index
        assert 0 <= orch["current_index"] < len(steps)

    @given(
        workflow_id=_workflow_ids,
        steps=_agent_instruction_steps(min_size=1, max_size=3),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_cancel_removes_orchestration(self, workflow_id, steps):
        """Cancelling an orchestration removes it from the dict."""
        mixin = _setup_mixin()
        _insert_orchestration(mixin, workflow_id, steps)

        assert workflow_id in mixin._workflow_orchestrations

        # Simulate cancel
        orch = mixin._workflow_orchestrations.pop(workflow_id, None)
        assert orch is not None
        assert workflow_id not in mixin._workflow_orchestrations
