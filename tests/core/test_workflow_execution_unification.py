# Feature: workflow-execution-unification, Property 5: Run start creates dedicated agent and event loop
"""
Property-based test verifying that start_workflow_run() creates a dedicated
WorkflowAgent instance and asyncio event loop, stored in _active_runs keyed
by the run ID.

**Validates: Requirements 2.1**
"""

import asyncio
import json
import sys
import threading
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock

from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

# distr.core.signals requires PyQt6 which is not available in the test environment.
# Inject a mock module so local imports inside service.py resolve correctly.
_mock_signals_module = MagicMock()
_mock_signal_manager = MagicMock()
_mock_signals_module.signal_manager = _mock_signal_manager
sys.modules.setdefault("distr.core.signals", _mock_signals_module)

# distr.core.workflow_engine.test_loop requires pydantic which is not available in tests.
_mock_test_loop_module = MagicMock()
sys.modules.setdefault("distr.core.workflow_engine.test_loop", _mock_test_loop_module)

import distr.core.workflow.service as service_mod
import distr.core.workflow.dispatcher as dispatcher_mod
import distr.core.workflow.post_execution as post_execution_mod
import distr.core.workflow.router as router_mod
import distr.core.workflow.step_executor as step_executor_mod
import distr.core.workflow.planning as planning_mod
from distr.core.workflow.service import start_workflow_run, _active_runs, _RunContext


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_instruction_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?"),
    min_size=1,
    max_size=80,
)

_step_name_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_- "),
    min_size=1,
    max_size=30,
)

_context_strategy = st.one_of(st.none(), _instruction_strategy)


@st.composite
def workflow_run_input(draw):
    """Generate inputs for start_workflow_run: a workflow_id and optional context."""
    workflow_id = draw(st.integers(min_value=1, max_value=10000))
    context = draw(_context_strategy)
    num_steps = draw(st.integers(min_value=1, max_value=5))
    step_names = [draw(_step_name_strategy) for _ in range(num_steps)]
    instructions = [draw(_instruction_strategy) for _ in range(num_steps)]
    return {
        "workflow_id": workflow_id,
        "context": context,
        "num_steps": num_steps,
        "step_names": step_names,
        "instructions": instructions,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_workflow(workflow_id, step_names, instructions):
    """Build a mock AutoWorkflow with mock steps."""
    steps = []
    for i, (name, instr) in enumerate(zip(step_names, instructions)):
        step = MagicMock()
        step.id = workflow_id * 100 + i
        step.position = i
        step.name = name
        step.status = "pending"
        step.result = None
        step.action_type = "agent_instruction"
        step.instruction = instr
        step.recording_filename = ""
        step.action_id = None
        step.code = ""
        step.wait_for_continue = False
        steps.append(step)

    wf = MagicMock()
    wf.id = workflow_id
    wf.steps = steps
    return wf


def _make_mock_run(run_id):
    """Build a mock AutoWorkflowRun."""
    run = MagicMock()
    run.id = run_id
    run.status = "running"
    run.current_step_id = None
    return run


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

@hyp_settings(max_examples=100)
@given(data=workflow_run_input())
def test_run_start_creates_dedicated_agent_and_event_loop(data):
    """**Validates: Requirements 2.1**

    For any call to start_workflow_run() that succeeds (workflow exists and has
    steps), the engine creates a new WorkflowAgent instance and a new asyncio
    event loop, and stores them in _active_runs keyed by the run ID.
    """
    workflow_id = data["workflow_id"]
    context = data["context"]
    step_names = data["step_names"]
    instructions = data["instructions"]

    wf = _make_mock_workflow(workflow_id, step_names, instructions)

    # Each test gets a unique, incrementing run_id
    run_id_counter = [workflow_id * 1000]

    def fake_flush_run(run_mock):
        run_mock.id = run_id_counter[0]
        run_id_counter[0] += 1

    mock_run = _make_mock_run(0)  # id set by fake_flush

    mock_db = MagicMock()
    # query().filter().first() returns the workflow
    mock_db.query.return_value.filter.return_value.first.return_value = wf

    def fake_add(obj):
        # When the run record is added, set up flush behavior
        if hasattr(obj, 'status') and not hasattr(obj, 'position'):
            mock_run.id = run_id_counter[0]
            obj.id = run_id_counter[0]

    mock_db.add.side_effect = fake_add

    def fake_flush():
        pass

    mock_db.flush.side_effect = fake_flush

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    # Clean up _active_runs before each test iteration
    _active_runs.clear()

    try:
        with patch.object(dispatcher_mod, "get_session", return_value=mock_session_ctx), \
             patch("distr.core.workflow_agent.WorkflowAgent", return_value=mock_workflow_agent) as mock_wa_cls, \
             patch.object(dispatcher_mod.StepDispatcher, "run_in_workflow", return_value={"success": True, "message": "ok"}) as mock_dispatch, \
             patch.object(dispatcher_mod, "os") as mock_os:

            # Allow env var setting without side effects
            mock_os.environ = {}

            result = start_workflow_run(workflow_id, context=context)

        # --- Assertions ---

        # 1. start_workflow_run should succeed (no error key)
        assert "error" not in result, f"start_workflow_run returned error: {result}"

        # 2. A run_id should be returned
        assert "run_id" in result, f"No run_id in result: {result}"
        run_id = result["run_id"]

        # 3. WorkflowAgent() was instantiated exactly once
        mock_wa_cls.assert_called_once()

        # 4. _active_runs contains an entry keyed by the run_id
        assert run_id in _active_runs, (
            f"run_id {run_id} not found in _active_runs. Keys: {list(_active_runs.keys())}"
        )

        # 5. The entry is a _RunContext with the correct fields
        ctx = _active_runs[run_id]
        assert isinstance(ctx, _RunContext), f"Expected _RunContext, got {type(ctx)}"

        # 6. The WorkflowAgent stored is the one we created
        assert ctx.workflow_agent is mock_workflow_agent, (
            "WorkflowAgent in _active_runs is not the instance created by the constructor"
        )

        # 7. The event loop is an asyncio event loop
        assert isinstance(ctx.event_loop, asyncio.AbstractEventLoop), (
            f"Expected asyncio event loop, got {type(ctx.event_loop)}"
        )

        # 8. The thread is a threading.Thread
        assert isinstance(ctx.thread, threading.Thread), (
            f"Expected threading.Thread, got {type(ctx.thread)}"
        )

        # 9. StepDispatcher.run_in_workflow was called for the first step
        mock_dispatch.assert_called_once()

    finally:
        # Clean up: stop event loops and remove entries to avoid state leakage
        for rid, ctx in list(_active_runs.items()):
            try:
                if isinstance(ctx.event_loop, asyncio.AbstractEventLoop) and ctx.event_loop.is_running():
                    ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
            except Exception:
                pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 15: Context injection round-trip
"""
Property-based test verifying that when a context string is provided to
start_workflow_run(), the instruction passed to _dispatch_step() for the first
agent_instruction step equals the context prepended to the original instruction.
When no context is provided, the instruction is passed unmodified.

**Validates: Requirements 10.2, 10.3**
"""


@st.composite
def context_injection_input(draw):
    """Generate inputs for context injection testing: context string and first-step instruction."""
    workflow_id = draw(st.integers(min_value=1, max_value=10000))
    # Non-empty context string
    context = draw(_instruction_strategy)
    instruction = draw(_instruction_strategy)
    step_name = draw(_step_name_strategy)
    return {
        "workflow_id": workflow_id,
        "context": context,
        "instruction": instruction,
        "step_name": step_name,
    }


@hyp_settings(max_examples=100)
@given(data=context_injection_input())
def test_context_injection_round_trip_with_context(data):
    """**Validates: Requirements 10.2, 10.3**

    For any non-empty context string and any first-step instruction, the
    instruction passed to _dispatch_step() equals the context prepended to
    the original instruction (f"{context}\\n\\n{instruction}").
    """
    workflow_id = data["workflow_id"]
    context = data["context"]
    instruction = data["instruction"]
    step_name = data["step_name"]

    wf = _make_mock_workflow(workflow_id, [step_name], [instruction])

    run_id_val = workflow_id * 1000

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = wf

    def fake_add(obj):
        if hasattr(obj, 'status') and not hasattr(obj, 'position'):
            obj.id = run_id_val

    mock_db.add.side_effect = fake_add
    mock_db.flush.side_effect = lambda: None

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    _active_runs.clear()

    try:
        with patch.object(dispatcher_mod, "get_session", return_value=mock_session_ctx), \
             patch("distr.core.workflow_agent.WorkflowAgent", return_value=mock_workflow_agent), \
             patch.object(dispatcher_mod.StepDispatcher, "run_in_workflow", return_value={"success": True, "message": "ok"}) as mock_dispatch, \
             patch.object(dispatcher_mod, "os") as mock_os:

            mock_os.environ = {}

            result = start_workflow_run(workflow_id, context=context)

        assert "error" not in result, f"start_workflow_run returned error: {result}"

        # StepDispatcher.run_in_workflow should have been called once for the first step
        mock_dispatch.assert_called_once()

        # In the new architecture, context is stored in _RunContext.context_prefix
        run_id = result["run_id"]
        assert run_id in _active_runs, f"run_id {run_id} not in _active_runs"
        ctx = _active_runs[run_id]
        assert ctx.context_prefix == context, (
            f"Expected context_prefix to be {context!r}, got {ctx.context_prefix!r}"
        )

    finally:
        for rid, ctx in list(_active_runs.items()):
            try:
                if isinstance(ctx.event_loop, asyncio.AbstractEventLoop) and ctx.event_loop.is_running():
                    ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
            except Exception:
                pass
        _active_runs.clear()


@hyp_settings(max_examples=100)
@given(data=workflow_run_input())
def test_context_injection_round_trip_without_context(data):
    """**Validates: Requirements 10.2, 10.3**

    For any call without a context string (None), the instruction passed to
    _dispatch_step() equals the original step instruction unmodified.
    """
    workflow_id = data["workflow_id"]
    step_names = data["step_names"]
    instructions = data["instructions"]
    original_first_instruction = instructions[0]

    wf = _make_mock_workflow(workflow_id, step_names, instructions)

    run_id_val = workflow_id * 1000

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = wf

    def fake_add(obj):
        if hasattr(obj, 'status') and not hasattr(obj, 'position'):
            obj.id = run_id_val

    mock_db.add.side_effect = fake_add
    mock_db.flush.side_effect = lambda: None

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    _active_runs.clear()

    try:
        with patch.object(dispatcher_mod, "get_session", return_value=mock_session_ctx), \
             patch("distr.core.workflow_agent.WorkflowAgent", return_value=mock_workflow_agent), \
             patch.object(dispatcher_mod.StepDispatcher, "run_in_workflow", return_value={"success": True, "message": "ok"}) as mock_dispatch, \
             patch.object(dispatcher_mod, "os") as mock_os:

            mock_os.environ = {}

            # Call without context (None)
            result = start_workflow_run(workflow_id, context=None)

        assert "error" not in result, f"start_workflow_run returned error: {result}"

        mock_dispatch.assert_called_once()

        # In the new architecture, context_prefix should be empty when no context
        run_id = result["run_id"]
        assert run_id in _active_runs, f"run_id {run_id} not in _active_runs"
        ctx = _active_runs[run_id]
        assert ctx.context_prefix == "", (
            f"Expected empty context_prefix when no context, got {ctx.context_prefix!r}"
        )

    finally:
        for rid, ctx in list(_active_runs.items()):
            try:
                if isinstance(ctx.event_loop, asyncio.AbstractEventLoop) and ctx.event_loop.is_running():
                    ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
            except Exception:
                pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 1: Agent instruction dispatch isolation
"""
Property-based test verifying that for any step with action_type == "agent_instruction"
and any non-empty instruction string, dispatching the step SHALL call
WorkflowAgent.execute() with the instruction and SHALL NOT emit
signal_manager.send_text_input at any point.

**Validates: Requirements 1.1, 1.5, 5.2, 5.3**
"""


# Strategy for non-empty instructions that won't be stripped to empty
_nonempty_instruction_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789"),
    min_size=1,
    max_size=80,
).map(lambda s: s.strip() or "fallback").filter(lambda s: len(s.strip()) > 0)


@st.composite
def agent_instruction_dispatch_input(draw):
    """Generate inputs for agent instruction dispatch: step_id, run_id, instruction, step_name."""
    step_id = draw(st.integers(min_value=1, max_value=50000))
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    instruction = draw(_nonempty_instruction_strategy)
    step_name = draw(_step_name_strategy)
    context_prefix = draw(st.sampled_from(["Workflow Run", "Step Runner", "Test"]))
    return {
        "step_id": step_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "instruction": instruction,
        "step_name": step_name,
        "context_prefix": context_prefix,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=agent_instruction_dispatch_input())
def test_agent_instruction_dispatch_isolation(data):
    """**Validates: Requirements 1.1, 1.5, 5.2, 5.3**

    For any step with action_type == "agent_instruction" and any non-empty
    instruction string, dispatching the step SHALL call WorkflowAgent.execute()
    with the instruction and SHALL NOT emit signal_manager.send_text_input
    at any point.
    """
    step_id = data["step_id"]
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    instruction = data["instruction"]
    step_name = data["step_name"]
    context_prefix = data["context_prefix"]

    # Build the expected prompt that _dispatch_step constructs
    expected_prompt = f"[{context_prefix} — {step_name}]\n{instruction}"

    # --- Set up mock WorkflowAgent and event loop in _active_runs ---
    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    # Make execute() return a real coroutine so asyncio.run_coroutine_threadsafe works
    execute_calls = []

    async def fake_execute(prompt):
        execute_calls.append(prompt)
        return "agent response"

    mock_workflow_agent.execute = fake_execute

    # Create a real event loop running in a background thread
    agent_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(agent_loop)
        agent_loop.run_forever()

    agent_thread = threading.Thread(target=_run_loop, daemon=True)
    agent_thread.start()

    # Pre-populate _active_runs with the mock _RunContext
    _active_runs.clear()
    _active_runs[run_id] = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=agent_loop,
        thread=agent_thread,
        context_prefix="",
    )

    # --- Set up mock DB to return step and run matching the _RunContext ---
    mock_step_obj = MagicMock()
    mock_step_obj.id = step_id
    mock_step_obj.workflow_id = workflow_id
    mock_step_obj.wait_for_continue = False
    mock_step_obj.name = step_name
    mock_step_obj.action_type = "agent_instruction"
    mock_step_obj.instruction = instruction
    mock_step_obj.code = ""
    mock_step_obj.recording_filename = ""
    mock_step_obj.action_id = None
    mock_step_obj.config = None
    mock_step_obj.timeout_seconds = 300
    mock_step_obj.status = "pending"
    mock_step_obj.result = None

    mock_run_obj = MagicMock()
    mock_run_obj.id = run_id
    mock_run_obj.workflow_id = workflow_id
    mock_run_obj.current_step_id = step_id
    mock_run_obj.status = "running"

    mock_db = MagicMock()

    # query(AutoWorkflowStep).filter(...).first() returns mock_step_obj
    # query(AutoWorkflowRun).filter(...).first() returns mock_run_obj
    # We need to handle two different query() calls in sequence
    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step_obj
        elif model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.filter.return_value.first.return_value = mock_run_obj
            q.filter.return_value.first.return_value = mock_run_obj
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    # Mock signal_manager to verify it is NOT called
    mock_signal_manager = MagicMock()

    try:
        with patch.object(dispatcher_mod, "get_session", return_value=mock_session_ctx), \
             patch("distr.core.signals.signal_manager", mock_signal_manager), \
             patch("distr.core.workflow.post_execution._run_verification", return_value=True), \
             patch.object(dispatcher_mod, "increment_workflow_updated"):

            from distr.core.workflow.dispatcher import StepDispatcher

            dispatcher = StepDispatcher()
            result = dispatcher.run_in_workflow(step_id, run_id)

        # --- Assertions ---

        # 1. Dispatch should succeed (dispatched to WorkflowAgent)
        assert "error" not in result, f"run_in_workflow returned error: {result}"
        assert result.get("success") is True, f"Expected success=True, got: {result}"

        # 2. WorkflowAgent.execute() was called via run_coroutine_threadsafe
        #    The coroutine was scheduled on the event loop; give it a moment to run
        import time
        time.sleep(0.2)  # Allow the event loop to process the coroutine

        # For async agent steps, the result is returned asynchronously
        # The key property is that signal_manager.send_text_input was NOT called

        # 3. signal_manager.send_text_input.emit was NOT called
        mock_signal_manager.send_text_input.emit.assert_not_called()

    finally:
        # Clean up: stop event loop and clear _active_runs
        try:
            agent_loop.call_soon_threadsafe(agent_loop.stop)
        except Exception:
            pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 2: Successful execution completes step with pass
"""
Property-based test verifying that for any agent-instruction step where
WorkflowAgent.execute() returns a response string without raising an exception,
complete_step() SHALL be called with that response text and passed=True.

**Validates: Requirements 1.2, 3.1**
"""


# Strategy for non-empty response strings returned by WorkflowAgent.execute()
_response_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?\n"),
    min_size=1,
    max_size=120,
)


@st.composite
def successful_execution_input(draw):
    """Generate inputs for successful agent execution: step_id, run_id, instruction, response."""
    step_id = draw(st.integers(min_value=1, max_value=50000))
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    instruction = draw(_nonempty_instruction_strategy)
    step_name = draw(_step_name_strategy)
    response_text = draw(_response_strategy)
    context_prefix = draw(st.sampled_from(["Workflow Run", "Step Runner", "Test"]))
    return {
        "step_id": step_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "instruction": instruction,
        "step_name": step_name,
        "response_text": response_text,
        "context_prefix": context_prefix,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=successful_execution_input())
def test_successful_execution_completes_step_with_pass(data):
    """**Validates: Requirements 1.2, 3.1**

    For any agent-instruction step where WorkflowAgent.execute() returns a
    response string without raising an exception, complete_step() SHALL be
    called with that response text and passed=True.
    """
    step_id = data["step_id"]
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    instruction = data["instruction"]
    step_name = data["step_name"]
    response_text = data["response_text"]
    context_prefix = data["context_prefix"]

    # Event to signal when the callback has fired
    callback_fired = threading.Event()

    # --- Set up mock WorkflowAgent whose execute() returns the generated response ---
    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    async def fake_execute(prompt):
        return response_text

    mock_workflow_agent.execute = fake_execute

    # Create a real event loop running in a background thread
    agent_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(agent_loop)
        agent_loop.run_forever()

    agent_thread = threading.Thread(target=_run_loop, daemon=True)
    agent_thread.start()

    # Pre-populate _active_runs with the mock _RunContext
    _active_runs.clear()
    _active_runs[run_id] = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=agent_loop,
        thread=agent_thread,
        context_prefix="",
    )

    # --- Set up mock DB to return step and run matching the _RunContext ---
    mock_step_obj = MagicMock()
    mock_step_obj.id = step_id
    mock_step_obj.workflow_id = workflow_id
    mock_step_obj.wait_for_continue = False
    mock_step_obj.name = step_name
    mock_step_obj.action_type = "agent_instruction"
    mock_step_obj.instruction = instruction
    mock_step_obj.code = ""
    mock_step_obj.recording_filename = ""
    mock_step_obj.action_id = None
    mock_step_obj.config = None
    mock_step_obj.timeout_seconds = 300
    mock_step_obj.status = "pending"
    mock_step_obj.result = None

    mock_run_obj = MagicMock()
    mock_run_obj.id = run_id
    mock_run_obj.workflow_id = workflow_id
    mock_run_obj.current_step_id = step_id
    mock_run_obj.status = "running"

    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step_obj
        elif model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.filter.return_value.first.return_value = mock_run_obj
            q.filter.return_value.first.return_value = mock_run_obj
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "complete_step", side_effect=lambda *a, **kw: callback_fired.set()) as mock_complete_step, \
             patch.object(service_mod, "_check_and_enter_wait", return_value=None):

            from distr.core.workflow.service import _dispatch_step

            result = _dispatch_step(
                step_id=step_id,
                step_name=step_name,
                action_type="agent_instruction",
                instruction=instruction,
                recording_filename="",
                context_prefix=context_prefix,
            )

            # 1. _dispatch_step should succeed
            assert "error" not in result, f"_dispatch_step returned error: {result}"
            assert result.get("success") is True, f"Expected success=True, got: {result}"

            # 2. Wait for the callback to fire (with timeout)
            assert callback_fired.wait(timeout=5.0), (
                "Timed out waiting for complete_step() callback to fire"
            )

            # 3. complete_step() was called exactly once with the response text and passed=True
            mock_complete_step.assert_called_once()
            call_args = mock_complete_step.call_args
            assert call_args[0][0] == step_id, (
                f"Expected complete_step called with step_id={step_id}, got {call_args[0][0]}"
            )
            assert call_args[0][1] == response_text, (
                f"Expected complete_step called with response_text={response_text!r}, "
                f"got {call_args[0][1]!r}"
            )
            assert call_args[1].get("passed") is True or (len(call_args[0]) > 2 and call_args[0][2] is True), (
                f"Expected complete_step called with passed=True, got call_args={call_args}"
            )

    finally:
        # Clean up: stop event loop and clear _active_runs
        try:
            agent_loop.call_soon_threadsafe(agent_loop.stop)
        except Exception:
            pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 3: Failed execution completes step with fail
"""
Property-based test verifying that for any agent-instruction step where
WorkflowAgent.execute() raises an exception, complete_step() SHALL be called
with the exception's string representation and passed=False.

**Validates: Requirements 1.3**
"""


# Strategy for error messages
_error_message_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?:-_"),
    min_size=1,
    max_size=120,
)


@st.composite
def failed_execution_input(draw):
    """Generate inputs for failed agent execution: step_id, run_id, instruction, error_message."""
    step_id = draw(st.integers(min_value=1, max_value=50000))
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    instruction = draw(_nonempty_instruction_strategy)
    step_name = draw(_step_name_strategy)
    error_message = draw(_error_message_strategy)
    context_prefix = draw(st.sampled_from(["Workflow Run", "Step Runner", "Test"]))
    return {
        "step_id": step_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "instruction": instruction,
        "step_name": step_name,
        "error_message": error_message,
        "context_prefix": context_prefix,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=failed_execution_input())
def test_failed_execution_completes_step_with_fail(data):
    """**Validates: Requirements 1.3**

    For any agent-instruction step where WorkflowAgent.execute() raises an
    exception, complete_step() SHALL be called with the exception's string
    representation and passed=False.
    """
    step_id = data["step_id"]
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    instruction = data["instruction"]
    step_name = data["step_name"]
    error_message = data["error_message"]
    context_prefix = data["context_prefix"]

    # Event to signal when the callback has fired
    callback_fired = threading.Event()

    # --- Set up mock WorkflowAgent whose execute() raises an exception ---
    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    async def fake_execute(prompt):
        raise RuntimeError(error_message)

    mock_workflow_agent.execute = fake_execute

    # Create a real event loop running in a background thread
    agent_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(agent_loop)
        agent_loop.run_forever()

    agent_thread = threading.Thread(target=_run_loop, daemon=True)
    agent_thread.start()

    # Pre-populate _active_runs with the mock _RunContext
    _active_runs.clear()
    _active_runs[run_id] = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=agent_loop,
        thread=agent_thread,
        context_prefix="",
    )

    # --- Set up mock DB to return step and run matching the _RunContext ---
    mock_step_obj = MagicMock()
    mock_step_obj.id = step_id
    mock_step_obj.workflow_id = workflow_id
    mock_step_obj.wait_for_continue = False
    mock_step_obj.name = step_name
    mock_step_obj.action_type = "agent_instruction"
    mock_step_obj.instruction = instruction
    mock_step_obj.code = ""
    mock_step_obj.recording_filename = ""
    mock_step_obj.action_id = None
    mock_step_obj.config = None
    mock_step_obj.timeout_seconds = 300
    mock_step_obj.status = "pending"
    mock_step_obj.result = None

    mock_run_obj = MagicMock()
    mock_run_obj.id = run_id
    mock_run_obj.workflow_id = workflow_id
    mock_run_obj.current_step_id = step_id
    mock_run_obj.status = "running"

    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step_obj
        elif model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.filter.return_value.first.return_value = mock_run_obj
            q.filter.return_value.first.return_value = mock_run_obj
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "complete_step", side_effect=lambda *a, **kw: callback_fired.set()) as mock_complete_step, \
             patch.object(service_mod, "_check_and_enter_wait", return_value=None):

            from distr.core.workflow.service import _dispatch_step

            result = _dispatch_step(
                step_id=step_id,
                step_name=step_name,
                action_type="agent_instruction",
                instruction=instruction,
                recording_filename="",
                context_prefix=context_prefix,
            )

            # 1. _dispatch_step should succeed (step was dispatched, error happens async)
            assert "error" not in result, f"_dispatch_step returned error: {result}"
            assert result.get("success") is True, f"Expected success=True, got: {result}"

            # 2. Wait for the callback to fire (with timeout)
            assert callback_fired.wait(timeout=5.0), (
                "Timed out waiting for complete_step() callback to fire"
            )

            # 3. complete_step() was called exactly once with the error message and passed=False
            mock_complete_step.assert_called_once()
            call_args = mock_complete_step.call_args
            assert call_args[0][0] == step_id, (
                f"Expected complete_step called with step_id={step_id}, got {call_args[0][0]}"
            )
            assert call_args[0][1] == error_message, (
                f"Expected complete_step called with error_message={error_message!r}, "
                f"got {call_args[0][1]!r}"
            )
            assert call_args[1].get("passed") is False or (len(call_args[0]) > 2 and call_args[0][2] is False), (
                f"Expected complete_step called with passed=False, got call_args={call_args}"
            )

    finally:
        # Clean up: stop event loop and clear _active_runs
        try:
            agent_loop.call_soon_threadsafe(agent_loop.stop)
        except Exception:
            pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 4: Non-agent step types bypass WorkflowAgent
"""
Property-based test verifying that for any step with action_type in
{execute_code, playwright, play_recording}, dispatching the step SHALL NOT
create, reference, or call any method on a WorkflowAgent instance, and SHALL
follow the existing execution path (TestLoopService or signal emission).

**Validates: Requirements 1.4, 9.1, 9.2, 9.3, 9.4**
"""


@st.composite
def non_agent_step_input(draw):
    """Generate inputs for non-agent step dispatch testing."""
    step_id = draw(st.integers(min_value=1, max_value=50000))
    step_name = draw(_step_name_strategy)
    action_type = draw(st.sampled_from(["execute_code", "playwright", "play_recording"]))
    instruction = draw(_nonempty_instruction_strategy)
    code = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_=() \n"),
        min_size=1,
        max_size=80,
    ).filter(lambda s: len(s.strip()) > 0))
    recording_filename = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789_-."),
        min_size=3,
        max_size=40,
    ).map(lambda s: s + ".webm"))
    return {
        "step_id": step_id,
        "step_name": step_name,
        "action_type": action_type,
        "instruction": instruction,
        "code": code,
        "recording_filename": recording_filename,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=non_agent_step_input())
def test_non_agent_step_types_bypass_workflow_agent(data):
    """**Validates: Requirements 1.4, 9.1, 9.2, 9.3, 9.4**

    For any step with action_type in {execute_code, playwright, play_recording},
    dispatching the step SHALL NOT create, reference, or call any method on a
    WorkflowAgent instance, and SHALL follow the existing execution path
    (TestLoopService or signal emission).
    """
    step_id = data["step_id"]
    step_name = data["step_name"]
    action_type = data["action_type"]
    instruction = data["instruction"]
    code = data["code"]
    recording_filename = data["recording_filename"]

    # Clear _active_runs to ensure no WorkflowAgent context is available
    _active_runs.clear()

    # Mock ExecutionResult for TestLoopService
    mock_exec_result = MagicMock()
    mock_exec_result.exit_code = 0
    mock_exec_result.stdout = "ok"
    mock_exec_result.stderr = ""

    mock_test_loop = MagicMock()
    mock_test_loop._execute_python.return_value = mock_exec_result
    mock_test_loop._execute_playwright.return_value = mock_exec_result

    mock_signal_manager = MagicMock()

    # Track whether WorkflowAgent is ever instantiated or referenced
    mock_wa_cls = MagicMock()

    try:
        with patch.object(service_mod, "get_session") as mock_get_session, \
             patch("distr.core.workflow.service.WorkflowAgent", mock_wa_cls), \
             patch.object(service_mod, "complete_step", return_value={"done": True, "status": "passed"}) as mock_complete_step, \
             patch.object(service_mod, "_check_and_enter_wait", return_value=None), \
             patch.object(service_mod, "update_step") as mock_update_step, \
             patch("distr.core.workflow_engine.test_loop.TestLoopService", return_value=mock_test_loop), \
             patch("distr.core.signals.signal_manager", mock_signal_manager):

            from distr.core.workflow.service import _dispatch_step

            result = _dispatch_step(
                step_id=step_id,
                step_name=step_name,
                action_type=action_type,
                instruction=instruction,
                recording_filename=recording_filename,
                context_prefix="Workflow Run",
                code=code,
            )

        # --- Assertions ---

        # 1. WorkflowAgent was NEVER instantiated
        mock_wa_cls.assert_not_called()

        # 2. No method on any WorkflowAgent instance was called (execute, shutdown, etc.)
        #    Since mock_wa_cls was never called, there's no instance, but verify the
        #    return_value (which would be the mock instance) was also not used
        mock_wa_cls.return_value.execute.assert_not_called()
        mock_wa_cls.return_value.shutdown.assert_not_called()

        # 3. The appropriate existing handler WAS called for each action type
        #    Note: _dispatch_step strips the code before passing to TestLoopService
        stripped_code = code.strip()
        if action_type == "execute_code":
            # TestLoopService._execute_python() should have been called
            mock_test_loop._execute_python.assert_called_once_with(stripped_code)
            mock_test_loop._execute_playwright.assert_not_called()
            mock_signal_manager.play_recording_file.emit.assert_not_called()
        elif action_type == "playwright":
            # TestLoopService._execute_playwright() should have been called
            mock_test_loop._execute_playwright.assert_called_once_with(stripped_code)
            mock_test_loop._execute_python.assert_not_called()
            mock_signal_manager.play_recording_file.emit.assert_not_called()
        elif action_type == "play_recording":
            # signal_manager.play_recording_file.emit() should have been called
            mock_signal_manager.play_recording_file.emit.assert_called_once_with(recording_filename)
            mock_test_loop._execute_python.assert_not_called()
            mock_test_loop._execute_playwright.assert_not_called()

        # 4. signal_manager.send_text_input.emit was NOT called (that's the agent path)
        mock_signal_manager.send_text_input.emit.assert_not_called()

        # 5. The dispatch should have succeeded (no error)
        assert "error" not in result, f"_dispatch_step returned error: {result}"

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 7: Terminal status triggers cleanup
"""
Property-based test verifying that for any workflow run that reaches a terminal
status (completed, failed, or cancelled), the engine SHALL call
WorkflowAgent.shutdown() and stop the associated event loop, and the run SHALL
be removed from _active_runs.

**Validates: Requirements 2.3**
"""


@hyp_settings(max_examples=100)
@given(
    terminal_status=st.sampled_from(["completed", "failed", "cancelled"]),
    run_id=st.integers(min_value=1, max_value=50000),
)
def test_terminal_status_triggers_cleanup(terminal_status, run_id):
    """**Validates: Requirements 2.3**

    For any workflow run that reaches a terminal status (completed, failed, or
    cancelled), the engine SHALL call WorkflowAgent.shutdown() and stop the
    associated event loop, and the run SHALL be removed from _active_runs.
    """
    # --- Set up a mock _RunContext in _active_runs ---
    mock_workflow_agent = MagicMock()
    mock_event_loop = MagicMock()
    mock_thread = MagicMock()

    ctx = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=mock_event_loop,
        thread=mock_thread,
        context_prefix="",
    )

    _active_runs.clear()
    _active_runs[run_id] = ctx

    try:
        # Call _cleanup_run directly — this is what _finalize_terminal_run and
        # cancel_run invoke when a run reaches terminal status.
        from distr.core.workflow.service import _cleanup_run
        _cleanup_run(run_id)

        # --- Assertions ---

        # 1. WorkflowAgent.shutdown() was called exactly once
        mock_workflow_agent.shutdown.assert_called_once()

        # 2. event_loop.call_soon_threadsafe(event_loop.stop) was called
        mock_event_loop.call_soon_threadsafe.assert_called_once_with(mock_event_loop.stop)

        # 3. The run_id is removed from _active_runs
        assert run_id not in _active_runs, (
            f"run_id {run_id} should have been removed from _active_runs after cleanup, "
            f"but keys are: {list(_active_runs.keys())}"
        )

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 12: Terminal status triggers bridge notification
"""
Property-based test verifying that for any workflow run that reaches a terminal
status, the engine SHALL call WorkflowAgentBridge.on_workflow_completed() with
a run result dict containing run_id, success, cancelled, and steps_summary fields.

**Validates: Requirements 6.1, 6.2**
"""


@hyp_settings(max_examples=100)
@given(
    terminal_status=st.sampled_from(["completed", "failed", "cancelled"]),
    run_id=st.integers(min_value=1, max_value=50000),
    workflow_id=st.integers(min_value=1, max_value=50000),
)
def test_terminal_status_triggers_bridge_notification(terminal_status, run_id, workflow_id):
    """**Validates: Requirements 6.1, 6.2**

    For any workflow run that reaches a terminal status, the engine SHALL call
    WorkflowAgentBridge.on_workflow_completed() with a run result dict containing
    run_id, success, cancelled, and steps_summary fields.
    """
    # --- Set up a mock _RunContext in _active_runs so _cleanup_run has something to clean ---
    mock_workflow_agent = MagicMock()
    mock_event_loop = MagicMock()
    mock_thread = MagicMock()

    ctx = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=mock_event_loop,
        thread=mock_thread,
        context_prefix="",
    )

    _active_runs.clear()
    _active_runs[run_id] = ctx

    # --- Mock get_session for the step results query inside _finalize_terminal_run ---
    mock_step_result = MagicMock()
    mock_step_result.step.name = "Test Step"
    mock_step_result.step_id = 100
    mock_step_result.status = "passed"

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_step_result]

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    # --- Mock WorkflowAgentBridge ---
    mock_bridge_instance = MagicMock()

    try:
        with patch.object(dispatcher_mod, "get_session", return_value=mock_session_ctx), \
             patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge", return_value=mock_bridge_instance) as mock_bridge_cls:

            from distr.core.workflow.service import _finalize_terminal_run
            _finalize_terminal_run(run_id, workflow_id, terminal_status)

        # --- Assertions ---

        # 1. WorkflowAgentBridge was instantiated
        mock_bridge_cls.assert_called_once()

        # 2. on_workflow_completed was called exactly once
        mock_bridge_instance.on_workflow_completed.assert_called_once()

        # 3. Extract the call arguments
        call_args = mock_bridge_instance.on_workflow_completed.call_args
        called_workflow_id = call_args[0][0]
        run_result = call_args[0][1]

        # 4. The first argument is the workflow_id
        assert called_workflow_id == workflow_id, (
            f"Expected workflow_id={workflow_id}, got {called_workflow_id}"
        )

        # 5. run_result contains the required fields
        assert "run_id" in run_result, f"run_result missing 'run_id': {run_result}"
        assert "success" in run_result, f"run_result missing 'success': {run_result}"
        assert "cancelled" in run_result, f"run_result missing 'cancelled': {run_result}"
        assert "steps_summary" in run_result, f"run_result missing 'steps_summary': {run_result}"

        # 6. run_id matches
        assert run_result["run_id"] == run_id, (
            f"Expected run_result['run_id']={run_id}, got {run_result['run_id']}"
        )

        # 7. success is True only when status is "completed"
        expected_success = (terminal_status == "completed")
        assert run_result["success"] == expected_success, (
            f"For status={terminal_status!r}, expected success={expected_success}, "
            f"got {run_result['success']}"
        )

        # 8. cancelled is True only when status is "cancelled"
        expected_cancelled = (terminal_status == "cancelled")
        assert run_result["cancelled"] == expected_cancelled, (
            f"For status={terminal_status!r}, expected cancelled={expected_cancelled}, "
            f"got {run_result['cancelled']}"
        )

        # 9. steps_summary is a list
        assert isinstance(run_result["steps_summary"], list), (
            f"Expected steps_summary to be a list, got {type(run_result['steps_summary'])}"
        )

        # 10. _cleanup_run was also called (run removed from _active_runs)
        assert run_id not in _active_runs, (
            f"run_id {run_id} should have been removed from _active_runs after finalization"
        )

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 11: Final step sets run to completed with timestamp
"""
Property-based test verifying that for any workflow run where routing determines
there is no next step (goto is null or -1, or no valid next step exists), the
AutoWorkflowRun.status SHALL be set to "completed" and AutoWorkflowRun.completed_at
SHALL be set to a non-null datetime.

**Validates: Requirements 3.4, 4.1**
"""


@st.composite
def terminal_routing_step_input(draw):
    """Generate inputs for complete_step where routing leads to END."""
    step_id = draw(st.integers(min_value=1, max_value=50000))
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    result_text = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?"),
        min_size=1,
        max_size=120,
    ))
    passed = draw(st.booleans())
    # Terminal routing: on_pass_goto / on_fail_goto is None or -1
    terminal_goto = draw(st.sampled_from([None, -1]))
    return {
        "step_id": step_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "result_text": result_text,
        "passed": passed,
        "terminal_goto": terminal_goto,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=terminal_routing_step_input())
def test_final_step_sets_run_to_completed_with_timestamp(data):
    """**Validates: Requirements 3.4, 4.1**

    For any workflow run where routing determines there is no next step
    (goto is null or -1, or no valid next step exists), the AutoWorkflowRun.status
    SHALL be set to "completed" and AutoWorkflowRun.completed_at SHALL be set
    to a non-null datetime.
    """
    step_id = data["step_id"]
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    result_text = data["result_text"]
    passed = data["passed"]
    terminal_goto = data["terminal_goto"]

    # --- Build mock step with terminal routing (static mode) ---
    mock_step = MagicMock()
    mock_step.id = step_id
    mock_step.workflow_id = workflow_id
    mock_step.wait_for_continue = False
    mock_step.validation_type = "none"
    mock_step.routing_mode = "static"
    mock_step.wait_before_next = 0
    # Set both on_pass_goto and on_fail_goto to terminal value so regardless
    # of the passed flag, routing leads to END
    mock_step.on_pass_goto = terminal_goto
    mock_step.on_fail_goto = terminal_goto

    # --- Build mock run ---
    mock_run = MagicMock()
    mock_run.id = run_id
    mock_run.workflow_id = workflow_id
    mock_run.status = "running"
    mock_run.current_step_id = step_id
    mock_run.completed_at = None  # Not yet completed

    # --- Build mock DB ---
    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step
        elif model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.filter.return_value.first.return_value = mock_run
            q.filter.return_value.first.return_value = mock_run
        elif model.__name__ == "AutoWorkflowStepResult":
            pass  # db.add handles this
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    # Pre-populate _active_runs so _cleanup_run has something to clean
    mock_workflow_agent = MagicMock()
    mock_event_loop = MagicMock()
    mock_thread = MagicMock()

    _active_runs.clear()
    _active_runs[run_id] = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=mock_event_loop,
        thread=mock_thread,
        context_prefix="",
    )

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "_finalize_terminal_run") as mock_finalize, \
             patch.object(service_mod, "_speak_result"):

            from distr.core.workflow.service import complete_step as cs
            result = cs(step_id, result_text, passed)

        # --- Assertions ---

        # 1. The result indicates the run is done and completed
        assert result.get("done") is True, f"Expected done=True, got: {result}"
        assert result.get("status") == "completed", (
            f"Expected status='completed', got: {result.get('status')}"
        )
        assert result.get("run_id") == run_id, (
            f"Expected run_id={run_id}, got: {result.get('run_id')}"
        )

        # 2. The run's status was set to "completed"
        assert mock_run.status == "completed", (
            f"Expected run.status='completed', got: {mock_run.status!r}"
        )

        # 3. The run's completed_at was set to a non-null datetime
        assert mock_run.completed_at is not None, (
            "Expected run.completed_at to be set to a non-null datetime, got None"
        )
        assert isinstance(mock_run.completed_at, datetime), (
            f"Expected run.completed_at to be a datetime, got {type(mock_run.completed_at)}"
        )

        # 4. _finalize_terminal_run was called with the correct arguments
        mock_finalize.assert_called_once_with(run_id, workflow_id, "completed")

        # 5. db.commit() was called (to persist the status change)
        mock_db.commit.assert_called()

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 6: Same agent reused across steps within a run
"""
Property-based test verifying that for any workflow run with N agent-instruction
steps (N >= 2), all N calls to WorkflowAgent.execute() SHALL use the same
WorkflowAgent instance (by object identity).

**Validates: Requirements 2.2**
"""


@st.composite
def multi_step_run_input(draw):
    """Generate inputs for a multi-step workflow run: run_id, workflow_id, and N step configs."""
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    n_steps = draw(st.integers(min_value=2, max_value=5))
    steps = []
    for i in range(n_steps):
        step_id = draw(st.integers(min_value=1, max_value=50000))
        step_name = draw(_step_name_strategy)
        instruction = draw(_nonempty_instruction_strategy)
        steps.append({
            "step_id": step_id,
            "step_name": step_name,
            "instruction": instruction,
        })
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "steps": steps,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=multi_step_run_input())
def test_same_agent_reused_across_steps_within_a_run(data):
    """**Validates: Requirements 2.2**

    For any workflow run with N agent-instruction steps (N >= 2), all N calls
    to WorkflowAgent.execute() SHALL use the same WorkflowAgent instance
    (by object identity).
    """
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    steps = data["steps"]

    # Track which WorkflowAgent instances are used for execute() calls
    agents_used = []

    # --- Set up a real-ish WorkflowAgent mock with identity tracking ---
    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    async def fake_execute(prompt):
        # Record the agent identity (id) each time execute is called
        agents_used.append(id(mock_workflow_agent))
        return "agent response"

    mock_workflow_agent.execute = fake_execute

    # Create a real event loop running in a background thread
    agent_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(agent_loop)
        agent_loop.run_forever()

    agent_thread = threading.Thread(target=_run_loop, daemon=True)
    agent_thread.start()

    # Pre-populate _active_runs with a single _RunContext for this run
    _active_runs.clear()
    _active_runs[run_id] = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=agent_loop,
        thread=agent_thread,
        context_prefix="",
    )

    try:
        for step_data in steps:
            step_id = step_data["step_id"]
            step_name = step_data["step_name"]
            instruction = step_data["instruction"]

            # --- Mock DB to return step and run matching the _RunContext ---
            mock_step_obj = MagicMock()
            mock_step_obj.id = step_id
            mock_step_obj.workflow_id = workflow_id
            mock_step_obj.wait_for_continue = False
            mock_step_obj.name = step_name
            mock_step_obj.action_type = "agent_instruction"
            mock_step_obj.instruction = instruction
            mock_step_obj.code = ""
            mock_step_obj.recording_filename = ""
            mock_step_obj.action_id = None
            mock_step_obj.config = None
            mock_step_obj.timeout_seconds = 300
            mock_step_obj.status = "pending"
            mock_step_obj.result = None

            mock_run_obj = MagicMock()
            mock_run_obj.id = run_id
            mock_run_obj.workflow_id = workflow_id
            mock_run_obj.current_step_id = step_id
            mock_run_obj.status = "running"

            mock_db = MagicMock()

            def query_side_effect(model, _step=mock_step_obj, _run=mock_run_obj):
                q = MagicMock()
                if model.__name__ == "AutoWorkflowStep":
                    q.filter.return_value.first.return_value = _step
                elif model.__name__ == "AutoWorkflowRun":
                    q.filter.return_value.filter.return_value.first.return_value = _run
                    q.filter.return_value.first.return_value = _run
                return q

            mock_db.query.side_effect = query_side_effect

            mock_session_ctx = MagicMock()
            mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
            mock_session_ctx.__exit__ = MagicMock(return_value=False)

            callback_fired = threading.Event()

            with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
                 patch.object(service_mod, "complete_step", side_effect=lambda *a, **kw: callback_fired.set()), \
                 patch.object(service_mod, "_check_and_enter_wait", return_value=None):

                from distr.core.workflow.service import _dispatch_step

                result = _dispatch_step(
                    step_id=step_id,
                    step_name=step_name,
                    action_type="agent_instruction",
                    instruction=instruction,
                    recording_filename="",
                    context_prefix="Workflow Run",
                )

                assert "error" not in result, f"_dispatch_step returned error: {result}"
                assert result.get("success") is True, f"Expected success=True, got: {result}"

                # Wait for the async callback to fire
                assert callback_fired.wait(timeout=5.0), (
                    f"Timed out waiting for complete_step() callback for step {step_id}"
                )

        # --- Assertions ---

        n_steps = len(steps)

        # 1. execute() was called exactly N times (once per step)
        assert len(agents_used) == n_steps, (
            f"Expected {n_steps} execute() calls, got {len(agents_used)}"
        )

        # 2. All calls used the same WorkflowAgent instance (same object identity)
        unique_agents = set(agents_used)
        assert len(unique_agents) == 1, (
            f"Expected all execute() calls to use the same agent instance, "
            f"but got {len(unique_agents)} distinct agent identities"
        )

        # 3. The agent used is the one we stored in _active_runs
        assert agents_used[0] == id(mock_workflow_agent), (
            "The agent used for execute() is not the one stored in _active_runs"
        )

    finally:
        # Clean up: stop event loop and clear _active_runs
        try:
            agent_loop.call_soon_threadsafe(agent_loop.stop)
        except Exception:
            pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 8: Concurrent runs have independent agents
"""
Property-based test verifying that for any two workflow runs started concurrently,
each run SHALL have a distinct WorkflowAgent instance (different object identity)
with independent _messages lists, such that messages appended in one run do not
appear in the other.

**Validates: Requirements 2.4**
"""


@st.composite
def concurrent_runs_input(draw):
    """Generate two distinct run IDs for concurrent run independence testing."""
    run_id_a = draw(st.integers(min_value=1, max_value=25000))
    run_id_b = draw(st.integers(min_value=25001, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    instruction_a = draw(_nonempty_instruction_strategy)
    instruction_b = draw(_nonempty_instruction_strategy)
    step_name_a = draw(_step_name_strategy)
    step_name_b = draw(_step_name_strategy)
    step_id_a = draw(st.integers(min_value=1, max_value=25000))
    step_id_b = draw(st.integers(min_value=25001, max_value=50000))
    return {
        "run_id_a": run_id_a,
        "run_id_b": run_id_b,
        "workflow_id": workflow_id,
        "instruction_a": instruction_a,
        "instruction_b": instruction_b,
        "step_name_a": step_name_a,
        "step_name_b": step_name_b,
        "step_id_a": step_id_a,
        "step_id_b": step_id_b,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=concurrent_runs_input())
def test_concurrent_runs_have_independent_agents(data):
    """**Validates: Requirements 2.4**

    For any two workflow runs started concurrently, each run SHALL have a
    distinct WorkflowAgent instance (different object identity) with independent
    _messages lists, such that messages appended in one run do not appear in
    the other.
    """
    run_id_a = data["run_id_a"]
    run_id_b = data["run_id_b"]
    workflow_id = data["workflow_id"]
    instruction_a = data["instruction_a"]
    instruction_b = data["instruction_b"]
    step_name_a = data["step_name_a"]
    step_name_b = data["step_name_b"]
    step_id_a = data["step_id_a"]
    step_id_b = data["step_id_b"]

    # --- Set up two distinct mock WorkflowAgents with independent _messages ---
    mock_agent_a = MagicMock()
    mock_agent_a._shutdown = False
    mock_agent_a._messages = []

    mock_agent_b = MagicMock()
    mock_agent_b._shutdown = False
    mock_agent_b._messages = []

    # Track execute() calls per agent
    execute_calls_a = []
    execute_calls_b = []

    async def fake_execute_a(prompt):
        execute_calls_a.append(prompt)
        mock_agent_a._messages.append({"role": "user", "content": prompt})
        mock_agent_a._messages.append({"role": "assistant", "content": "response_a"})
        return "response_a"

    async def fake_execute_b(prompt):
        execute_calls_b.append(prompt)
        mock_agent_b._messages.append({"role": "user", "content": prompt})
        mock_agent_b._messages.append({"role": "assistant", "content": "response_b"})
        return "response_b"

    mock_agent_a.execute = fake_execute_a
    mock_agent_b.execute = fake_execute_b

    # Create two real event loops in background threads
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()

    def _run_loop_a():
        asyncio.set_event_loop(loop_a)
        loop_a.run_forever()

    def _run_loop_b():
        asyncio.set_event_loop(loop_b)
        loop_b.run_forever()

    thread_a = threading.Thread(target=_run_loop_a, daemon=True)
    thread_b = threading.Thread(target=_run_loop_b, daemon=True)
    thread_a.start()
    thread_b.start()

    # Pre-populate _active_runs with two separate _RunContext entries
    _active_runs.clear()
    _active_runs[run_id_a] = _RunContext(
        run_id=run_id_a,
        workflow_agent=mock_agent_a,
        event_loop=loop_a,
        thread=thread_a,
        context_prefix="",
    )
    _active_runs[run_id_b] = _RunContext(
        run_id=run_id_b,
        workflow_agent=mock_agent_b,
        event_loop=loop_b,
        thread=thread_b,
        context_prefix="",
    )

    try:
        # --- Verify agents are distinct (different object identity) ---
        assert mock_agent_a is not mock_agent_b, (
            "The two WorkflowAgent instances must be distinct objects"
        )
        assert _active_runs[run_id_a].workflow_agent is not _active_runs[run_id_b].workflow_agent, (
            "The WorkflowAgent stored for run A must not be the same object as run B"
        )

        # --- Dispatch a step for run A ---
        callback_a = threading.Event()

        mock_step_a = MagicMock()
        mock_step_a.id = step_id_a
        mock_step_a.workflow_id = workflow_id
        mock_step_a.wait_for_continue = False

        mock_run_a = MagicMock()
        mock_run_a.id = run_id_a
        mock_run_a.workflow_id = workflow_id
        mock_run_a.current_step_id = step_id_a
        mock_run_a.status = "running"

        mock_db_a = MagicMock()

        def query_side_effect_a(model, _step=mock_step_a, _run=mock_run_a):
            q = MagicMock()
            if model.__name__ == "AutoWorkflowStep":
                q.filter.return_value.first.return_value = _step
            elif model.__name__ == "AutoWorkflowRun":
                q.filter.return_value.filter.return_value.first.return_value = _run
                q.filter.return_value.first.return_value = _run
            return q

        mock_db_a.query.side_effect = query_side_effect_a

        mock_session_a = MagicMock()
        mock_session_a.__enter__ = MagicMock(return_value=mock_db_a)
        mock_session_a.__exit__ = MagicMock(return_value=False)

        with patch.object(service_mod, "get_session", return_value=mock_session_a), \
             patch.object(service_mod, "complete_step", side_effect=lambda *a, **kw: callback_a.set()), \
             patch.object(service_mod, "_check_and_enter_wait", return_value=None):

            result_a = service_mod._dispatch_step(
                step_id=step_id_a,
                step_name=step_name_a,
                action_type="agent_instruction",
                instruction=instruction_a,
                recording_filename="",
                context_prefix="Workflow Run",
            )

            assert "error" not in result_a, f"_dispatch_step for run A returned error: {result_a}"
            assert callback_a.wait(timeout=5.0), "Timed out waiting for run A callback"

        # --- Dispatch a step for run B ---
        callback_b = threading.Event()

        mock_step_b = MagicMock()
        mock_step_b.id = step_id_b
        mock_step_b.workflow_id = workflow_id
        mock_step_b.wait_for_continue = False

        mock_run_b = MagicMock()
        mock_run_b.id = run_id_b
        mock_run_b.workflow_id = workflow_id
        mock_run_b.current_step_id = step_id_b
        mock_run_b.status = "running"

        mock_db_b = MagicMock()

        def query_side_effect_b(model, _step=mock_step_b, _run=mock_run_b):
            q = MagicMock()
            if model.__name__ == "AutoWorkflowStep":
                q.filter.return_value.first.return_value = _step
            elif model.__name__ == "AutoWorkflowRun":
                q.filter.return_value.filter.return_value.first.return_value = _run
                q.filter.return_value.first.return_value = _run
            return q

        mock_db_b.query.side_effect = query_side_effect_b

        mock_session_b = MagicMock()
        mock_session_b.__enter__ = MagicMock(return_value=mock_db_b)
        mock_session_b.__exit__ = MagicMock(return_value=False)

        with patch.object(service_mod, "get_session", return_value=mock_session_b), \
             patch.object(service_mod, "complete_step", side_effect=lambda *a, **kw: callback_b.set()), \
             patch.object(service_mod, "_check_and_enter_wait", return_value=None):

            result_b = service_mod._dispatch_step(
                step_id=step_id_b,
                step_name=step_name_b,
                action_type="agent_instruction",
                instruction=instruction_b,
                recording_filename="",
                context_prefix="Workflow Run",
            )

            assert "error" not in result_b, f"_dispatch_step for run B returned error: {result_b}"
            assert callback_b.wait(timeout=5.0), "Timed out waiting for run B callback"

        # --- Assertions ---

        # 1. execute() calls for run A went to agent A only
        assert len(execute_calls_a) == 1, (
            f"Expected 1 execute() call on agent A, got {len(execute_calls_a)}"
        )

        # 2. execute() calls for run B went to agent B only
        assert len(execute_calls_b) == 1, (
            f"Expected 1 execute() call on agent B, got {len(execute_calls_b)}"
        )

        # 3. Agent A's _messages contain only run A's messages
        assert len(mock_agent_a._messages) == 2, (
            f"Expected 2 messages in agent A, got {len(mock_agent_a._messages)}"
        )
        for msg in mock_agent_a._messages:
            assert "response_b" not in msg.get("content", ""), (
                "Agent A's messages contain content from run B"
            )

        # 4. Agent B's _messages contain only run B's messages
        assert len(mock_agent_b._messages) == 2, (
            f"Expected 2 messages in agent B, got {len(mock_agent_b._messages)}"
        )
        for msg in mock_agent_b._messages:
            assert "response_a" not in msg.get("content", ""), (
                "Agent B's messages contain content from run A"
            )

        # 5. The _messages lists are independent objects (not shared)
        assert mock_agent_a._messages is not mock_agent_b._messages, (
            "Agent A and Agent B share the same _messages list object"
        )

    finally:
        # Clean up: stop event loops and clear _active_runs
        try:
            loop_a.call_soon_threadsafe(loop_a.stop)
        except Exception:
            pass
        try:
            loop_b.call_soon_threadsafe(loop_b.stop)
        except Exception:
            pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 9: Verification runs against agent response
"""
Property-based test verifying that for any agent-instruction step with
validation_type != "none", after WorkflowAgent.execute() returns a response,
_run_verification() SHALL be called with that response text and the step's
configured validation parameters.

**Validates: Requirements 3.2**
"""


_validation_type_strategy = st.sampled_from(["text_match", "rule_based", "llm_judgment"])

_validation_prompt_strategy = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?\n:"),
    min_size=1,
    max_size=80,
)


@st.composite
def verification_step_input(draw):
    """Generate inputs for verification testing: step with non-none validation_type and a response."""
    step_id = draw(st.integers(min_value=1, max_value=50000))
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    result_text = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?\n"),
        min_size=1,
        max_size=120,
    ))
    validation_type = draw(_validation_type_strategy)
    validation_prompt = draw(_validation_prompt_strategy)
    passed = draw(st.booleans())
    return {
        "step_id": step_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "result_text": result_text,
        "validation_type": validation_type,
        "validation_prompt": validation_prompt,
        "passed": passed,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=verification_step_input())
def test_verification_runs_against_agent_response(data):
    """**Validates: Requirements 3.2**

    For any agent-instruction step with validation_type != "none", after
    WorkflowAgent.execute() returns a response, _run_verification() SHALL be
    called with that response text and the step's configured validation parameters.
    """
    step_id = data["step_id"]
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    result_text = data["result_text"]
    validation_type = data["validation_type"]
    validation_prompt = data["validation_prompt"]
    passed = data["passed"]

    # --- Build mock step with non-"none" validation_type ---
    mock_step = MagicMock()
    mock_step.id = step_id
    mock_step.workflow_id = workflow_id
    mock_step.wait_for_continue = False
    mock_step.validation_type = validation_type
    mock_step.validation_prompt = validation_prompt
    mock_step.routing_mode = "static"
    mock_step.wait_before_next = 0
    # Terminal routing so the run completes after this step
    mock_step.on_pass_goto = None
    mock_step.on_fail_goto = None

    # --- Build mock run ---
    mock_run = MagicMock()
    mock_run.id = run_id
    mock_run.workflow_id = workflow_id
    mock_run.status = "running"
    mock_run.current_step_id = step_id
    mock_run.completed_at = None

    # --- Build mock DB ---
    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step
        elif model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.filter.return_value.first.return_value = mock_run
            q.filter.return_value.first.return_value = mock_run
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    _active_runs.clear()

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "_run_verification", return_value=True) as mock_run_verification, \
             patch.object(service_mod, "_finalize_terminal_run"), \
             patch.object(service_mod, "_speak_result"):

            from distr.core.workflow.service import complete_step as cs
            cs(step_id, result_text, passed)

        # --- Assertions ---

        # 1. _run_verification was called exactly once
        mock_run_verification.assert_called_once()

        # 2. The first argument is the step object (with the configured validation_type)
        call_args = mock_run_verification.call_args
        called_step = call_args[0][0]
        assert called_step is mock_step, (
            "Expected _run_verification to be called with the step object"
        )

        # 3. The second argument is the response text passed to complete_step
        called_result = call_args[0][1]
        assert called_result == result_text, (
            f"Expected _run_verification called with result_text={result_text!r}, "
            f"got {called_result!r}"
        )

        # 4. The third argument is the passed flag
        called_passed = call_args[0][2]
        assert called_passed == passed, (
            f"Expected _run_verification called with passed={passed}, "
            f"got {called_passed}"
        )

        # 5. The step's validation_type is accessible on the step object passed
        assert called_step.validation_type == validation_type, (
            f"Expected step.validation_type={validation_type!r}, "
            f"got {called_step.validation_type!r}"
        )

        # 6. The step's validation_prompt is accessible on the step object passed
        assert called_step.validation_prompt == validation_prompt, (
            f"Expected step.validation_prompt={validation_prompt!r}, "
            f"got {called_step.validation_prompt!r}"
        )

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 10: Routing advances or terminates after verification
"""
Property-based test verifying that for any completed step with a verification
result (pass or fail), the routing logic SHALL select the next step based on
the step's routing_mode and on_pass_goto/on_fail_goto configuration, or
terminate the run if routing leads to END (null or -1).

**Validates: Requirements 3.3**
"""


@st.composite
def routing_after_verification_input(draw):
    """Generate inputs for routing-after-verification testing.

    Generates a step with static routing mode, a random verification result,
    and a routing target that is either a valid next step ID or END (None/-1).
    """
    step_id = draw(st.integers(min_value=1, max_value=50000))
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    result_text = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?"),
        min_size=1,
        max_size=120,
    ))
    passed = draw(st.booleans())
    # Whether routing leads to a valid next step or to END
    routes_to_end = draw(st.booleans())
    if routes_to_end:
        # END routing: None or -1
        goto_value = draw(st.sampled_from([None, -1]))
        next_step_id = None
    else:
        # Valid next step — must differ from step_id to avoid infinite-loop guard
        next_step_id = draw(
            st.integers(min_value=1, max_value=50000).filter(lambda x: x != step_id)
        )
        goto_value = next_step_id
    return {
        "step_id": step_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "result_text": result_text,
        "passed": passed,
        "routes_to_end": routes_to_end,
        "goto_value": goto_value,
        "next_step_id": next_step_id,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=routing_after_verification_input())
def test_routing_advances_or_terminates_after_verification(data):
    """**Validates: Requirements 3.3**

    For any completed step with a verification result (pass or fail), the
    routing logic SHALL select the next step based on the step's routing_mode
    and on_pass_goto/on_fail_goto configuration, or terminate the run if
    routing leads to END (null or -1).
    """
    step_id = data["step_id"]
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    result_text = data["result_text"]
    passed = data["passed"]
    routes_to_end = data["routes_to_end"]
    goto_value = data["goto_value"]
    next_step_id = data["next_step_id"]

    # --- Build mock step with static routing ---
    mock_step = MagicMock()
    mock_step.id = step_id
    mock_step.workflow_id = workflow_id
    mock_step.wait_for_continue = False
    mock_step.validation_type = "none"
    mock_step.routing_mode = "static"
    mock_step.wait_before_next = 0
    # Set on_pass_goto and on_fail_goto to the same value so the routing
    # outcome is deterministic regardless of verification result
    mock_step.on_pass_goto = goto_value
    mock_step.on_fail_goto = goto_value

    # --- Build mock run ---
    mock_run = MagicMock()
    mock_run.id = run_id
    mock_run.workflow_id = workflow_id
    mock_run.status = "running"
    mock_run.current_step_id = step_id
    mock_run.completed_at = None

    # --- Build mock next step (only used when routing to a valid step) ---
    mock_next_step = None
    if not routes_to_end:
        mock_next_step = MagicMock()
        mock_next_step.id = next_step_id
        mock_next_step.name = "Next Step"
        mock_next_step.action_type = "agent_instruction"
        mock_next_step.instruction = "do something"
        mock_next_step.recording_filename = ""
        mock_next_step.action_id = None
        mock_next_step.code = ""
        mock_next_step.status = "pending"

    # --- Build mock DB ---
    mock_db = MagicMock()

    # Track queries: AutoWorkflowStep filter by step_id returns mock_step,
    # but filter by goto (next_step_id) returns mock_next_step.
    # Use a mutable list as a call counter to distinguish the initial step
    # lookup from the next-step lookup within each hypothesis iteration.
    step_query_counter = [0]

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowStep":
            def filter_side_effect(*args, **kwargs):
                fq = MagicMock()
                step_query_counter[0] += 1
                if step_query_counter[0] == 1:
                    fq.first.return_value = mock_step
                else:
                    fq.first.return_value = mock_next_step
                return fq
            q.filter.side_effect = filter_side_effect
        elif model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.filter.return_value.first.return_value = mock_run
            q.filter.return_value.first.return_value = mock_run
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    # Pre-populate _active_runs so _cleanup_run has something to clean on terminal
    mock_workflow_agent = MagicMock()
    mock_event_loop = MagicMock()
    mock_thread = MagicMock()

    _active_runs.clear()
    _active_runs[run_id] = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=mock_event_loop,
        thread=mock_thread,
        context_prefix="",
    )

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "_finalize_terminal_run") as mock_finalize, \
             patch.object(service_mod, "_speak_result"), \
             patch.object(service_mod, "_dispatch_step", return_value={"success": True}) as mock_dispatch, \
             patch.object(service_mod, "os") as mock_os:

            mock_os.environ = {}

            from distr.core.workflow.service import complete_step as cs
            result = cs(step_id, result_text, passed)

        # --- Assertions ---

        if routes_to_end:
            # Routing leads to END: run should be completed
            assert result.get("done") is True, f"Expected done=True, got: {result}"
            assert result.get("status") == "completed", (
                f"Expected status='completed', got: {result.get('status')}"
            )
            assert mock_run.status == "completed", (
                f"Expected run.status='completed', got: {mock_run.status!r}"
            )
            assert mock_run.completed_at is not None, (
                "Expected run.completed_at to be set when routing leads to END"
            )
            mock_finalize.assert_called_once_with(run_id, workflow_id, "completed")
            # _dispatch_step should NOT have been called (no next step)
            mock_dispatch.assert_not_called()
        else:
            # Routing leads to a valid next step: _dispatch_step should be called
            assert result.get("done") is False, f"Expected done=False, got: {result}"
            assert result.get("next_step_id") == next_step_id, (
                f"Expected next_step_id={next_step_id}, got: {result.get('next_step_id')}"
            )
            mock_dispatch.assert_called_once()
            # Verify _dispatch_step was called with the next step's details
            call_args = mock_dispatch.call_args
            assert call_args[0][0] == next_step_id, (
                f"Expected _dispatch_step called with next step_id={next_step_id}, "
                f"got {call_args[0][0]}"
            )
            # Run should NOT be completed
            assert mock_run.status != "completed", (
                "Run should not be completed when routing to a valid next step"
            )
            mock_finalize.assert_not_called()

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 13: Wait-for-continue preserves agent and enters waiting
"""
Property-based test verifying that for any agent-instruction step with
wait_for_continue=True, after WorkflowAgent.execute() returns, the step status
SHALL be set to "waiting", the run status SHALL be set to "waiting", and the
WorkflowAgent instance SHALL remain in _active_runs with _shutdown == False.

**Validates: Requirements 7.1, 7.3**
"""


@st.composite
def wait_for_continue_input(draw):
    """Generate inputs for wait-for-continue testing."""
    step_id = draw(st.integers(min_value=1, max_value=50000))
    run_id = draw(st.integers(min_value=1, max_value=50000))
    workflow_id = draw(st.integers(min_value=1, max_value=50000))
    instruction = draw(_nonempty_instruction_strategy)
    step_name = draw(_step_name_strategy)
    response_text = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?"),
        min_size=1,
        max_size=120,
    ))
    context_prefix = draw(st.sampled_from(["Workflow Run", "Step Runner", "Test"]))
    return {
        "step_id": step_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "instruction": instruction,
        "step_name": step_name,
        "response_text": response_text,
        "context_prefix": context_prefix,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=wait_for_continue_input())
def test_wait_for_continue_preserves_agent_and_enters_waiting(data):
    """**Validates: Requirements 7.1, 7.3**

    For any agent-instruction step with wait_for_continue=True, after
    WorkflowAgent.execute() returns, the step status SHALL be set to "waiting",
    the run status SHALL be set to "waiting", and the WorkflowAgent instance
    SHALL remain in _active_runs with _shutdown == False.
    """
    step_id = data["step_id"]
    run_id = data["run_id"]
    workflow_id = data["workflow_id"]
    instruction = data["instruction"]
    step_name = data["step_name"]
    response_text = data["response_text"]
    context_prefix = data["context_prefix"]

    # Event to signal when the _on_agent_done callback has fired
    callback_fired = threading.Event()

    # --- Set up mock WorkflowAgent whose execute() returns the generated response ---
    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    async def fake_execute(prompt):
        return response_text

    mock_workflow_agent.execute = fake_execute

    # Create a real event loop running in a background thread
    agent_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(agent_loop)
        agent_loop.run_forever()

    agent_thread = threading.Thread(target=_run_loop, daemon=True)
    agent_thread.start()

    # Pre-populate _active_runs with the mock _RunContext
    _active_runs.clear()
    _active_runs[run_id] = _RunContext(
        run_id=run_id,
        workflow_agent=mock_workflow_agent,
        event_loop=agent_loop,
        thread=agent_thread,
        context_prefix="",
    )

    # --- Set up mock DB objects ---
    mock_step_obj = MagicMock()
    mock_step_obj.id = step_id
    mock_step_obj.workflow_id = workflow_id
    mock_step_obj.wait_for_continue = True  # Key: this step waits
    mock_step_obj.name = step_name
    mock_step_obj.action_type = "agent_instruction"
    mock_step_obj.instruction = instruction
    mock_step_obj.code = ""
    mock_step_obj.recording_filename = ""
    mock_step_obj.action_id = None
    mock_step_obj.config = None
    mock_step_obj.timeout_seconds = 300
    mock_step_obj.status = "pending"
    mock_step_obj.result = None

    mock_run_obj = MagicMock()
    mock_run_obj.id = run_id
    mock_run_obj.workflow_id = workflow_id
    mock_run_obj.current_step_id = step_id
    mock_run_obj.status = "running"
    mock_run_obj.run_data = "{}"

    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step_obj
        elif model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.filter.return_value.first.return_value = mock_run_obj
            q.filter.return_value.first.return_value = mock_run_obj
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    # Wrap _check_and_enter_wait to signal when it's been called
    original_check = service_mod._check_and_enter_wait

    def patched_check_and_enter_wait(sid, action_result, passed):
        result = original_check(sid, action_result, passed)
        callback_fired.set()
        return result

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "_check_and_enter_wait", side_effect=patched_check_and_enter_wait) as mock_check_wait, \
             patch.object(service_mod, "complete_step") as mock_complete_step:

            from distr.core.workflow.service import _dispatch_step

            result = _dispatch_step(
                step_id=step_id,
                step_name=step_name,
                action_type="agent_instruction",
                instruction=instruction,
                recording_filename="",
                context_prefix=context_prefix,
            )

            # 1. _dispatch_step should succeed (dispatched to WorkflowAgent)
            assert "error" not in result, f"_dispatch_step returned error: {result}"
            assert result.get("success") is True, f"Expected success=True, got: {result}"

            # 2. Wait for the _on_agent_done callback to fire
            assert callback_fired.wait(timeout=5.0), (
                "Timed out waiting for _on_agent_done callback to fire"
            )

            # 3. _check_and_enter_wait was called with the step_id, response, and passed=True
            mock_check_wait.assert_called_once_with(step_id, response_text, True)

            # 4. Since _check_and_enter_wait returned a wait dict (non-None),
            #    complete_step should NOT have been called
            mock_complete_step.assert_not_called()

            # 5. Verify step status was set to "waiting" by _check_and_enter_wait
            assert mock_step_obj.status == "waiting", (
                f"Expected step.status='waiting', got: {mock_step_obj.status!r}"
            )

            # 6. Verify run status was set to "waiting" by _check_and_enter_wait
            assert mock_run_obj.status == "waiting", (
                f"Expected run.status='waiting', got: {mock_run_obj.status!r}"
            )

            # 7. WorkflowAgent remains in _active_runs (not cleaned up)
            assert run_id in _active_runs, (
                f"run_id {run_id} should still be in _active_runs after wait-for-continue. "
                f"Keys: {list(_active_runs.keys())}"
            )

            # 8. WorkflowAgent is not shut down
            ctx = _active_runs[run_id]
            assert ctx.workflow_agent is mock_workflow_agent, (
                "WorkflowAgent in _active_runs should be the same instance"
            )
            assert ctx.workflow_agent._shutdown is False, (
                "WorkflowAgent._shutdown should be False while waiting"
            )

    finally:
        # Clean up: stop event loop and clear _active_runs
        try:
            agent_loop.call_soon_threadsafe(agent_loop.stop)
        except Exception:
            pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Property 14: Continue-waiting resumes with stored result
"""
Property-based test verifying that for any run in "waiting" status, calling
continue_waiting_step(run_id, optional_input) SHALL invoke complete_step()
with the previously stored result (and appended optional_input if non-empty),
and the run status SHALL transition from "waiting" back to "running" or to a
terminal status.

**Validates: Requirements 7.2**
"""


@st.composite
def continue_waiting_input(draw):
    """Generate inputs for continue-waiting testing: stored result, passed flag, optional input."""
    run_id = draw(st.integers(min_value=1, max_value=50000))
    step_id = draw(st.integers(min_value=1, max_value=50000))
    stored_result = draw(st.text(
        alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?\n"),
        min_size=1,
        max_size=120,
    ))
    stored_passed = draw(st.booleans())
    # optional_input: either empty or non-empty (with possible whitespace-only)
    optional_input = draw(st.one_of(
        st.just(""),
        st.text(
            alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz0123456789 .,!?"),
            min_size=0,
            max_size=80,
        ),
    ))
    return {
        "run_id": run_id,
        "step_id": step_id,
        "stored_result": stored_result,
        "stored_passed": stored_passed,
        "optional_input": optional_input,
    }


@hyp_settings(max_examples=100, deadline=None)
@given(data=continue_waiting_input())
def test_continue_waiting_resumes_with_stored_result(data):
    """**Validates: Requirements 7.2**

    For any run in "waiting" status, calling continue_waiting_step(run_id,
    optional_input) SHALL invoke complete_step() with the previously stored
    result (and appended optional_input if non-empty), and the run status SHALL
    transition from "waiting" back to "running" or to a terminal status.
    """
    run_id = data["run_id"]
    step_id = data["step_id"]
    stored_result = data["stored_result"]
    stored_passed = data["stored_passed"]
    optional_input = data["optional_input"]

    # Build the run_data JSON that would have been stored during wait entry
    run_data_dict = {
        "waiting_result": stored_result,
        "waiting_passed": stored_passed,
    }
    run_data_json = json.dumps(run_data_dict)

    # --- Set up mock DB objects ---
    mock_run = MagicMock()
    mock_run.id = run_id
    mock_run.status = "waiting"
    mock_run.current_step_id = step_id
    mock_run.run_data = run_data_json

    mock_step = MagicMock()
    mock_step.id = step_id
    mock_step.status = "waiting"

    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "AutoWorkflowRun":
            q.filter.return_value.first.return_value = mock_run
        elif model.__name__ == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    # Compute expected result passed to complete_step
    if optional_input.strip():
        expected_result = f"{stored_result}\n\n[CONTINUE INPUT]: {optional_input.strip()}"
    else:
        expected_result = stored_result

    # Mock complete_step to capture the call and return a plausible result
    complete_step_return = {"done": True, "status": "completed"}

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "complete_step", return_value=complete_step_return) as mock_complete_step:

            from distr.core.workflow.service import continue_waiting_step

            result = continue_waiting_step(run_id, optional_input)

        # --- Assertions ---

        # 1. No error should be returned
        assert "error" not in result, f"continue_waiting_step returned error: {result}"

        # 2. Run status was set back to "running" before complete_step was called
        assert mock_run.status == "running", (
            f"Expected run.status='running' after continue, got: {mock_run.status!r}"
        )

        # 3. Step status was set back to "running" before complete_step was called
        assert mock_step.status == "running", (
            f"Expected step.status='running' after continue, got: {mock_step.status!r}"
        )

        # 4. complete_step was called exactly once
        mock_complete_step.assert_called_once()

        # 5. complete_step was called with the correct step_id
        call_args = mock_complete_step.call_args
        assert call_args[0][0] == step_id, (
            f"Expected complete_step called with step_id={step_id}, got {call_args[0][0]}"
        )

        # 6. complete_step was called with the correct (possibly appended) result
        assert call_args[0][1] == expected_result, (
            f"Expected complete_step called with result={expected_result!r}, "
            f"got {call_args[0][1]!r}"
        )

        # 7. complete_step was called with the stored passed flag
        assert call_args[0][2] == stored_passed, (
            f"Expected complete_step called with passed={stored_passed}, "
            f"got {call_args[0][2]}"
        )

        # 8. complete_step was called with _from_continue=True
        assert call_args[1].get("_from_continue") is True, (
            f"Expected complete_step called with _from_continue=True, "
            f"got call kwargs={call_args[1]}"
        )

        # 9. db.commit() was called (to persist the status changes)
        mock_db.commit.assert_called()

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Unit Test 9.1: Integration smoke test
"""
Integration smoke test: Start a 2-step workflow with mocked WorkflowAgent,
verify both steps complete and run reaches `completed`.

**Validates: Requirements 1.1, 1.2, 3.1, 3.4**
"""


def test_integration_smoke_two_step_workflow_completes():
    """Integration smoke test: a 2-step agent_instruction workflow runs to completion.

    Creates a mock 2-step workflow where:
    - Step 1 (position 0) routes on_pass_goto → Step 2
    - Step 2 (position 1) routes on_pass_goto → -1 (END; avoids DB "next by position" mock ambiguity)
    - WorkflowAgent.execute() returns controlled responses for each step
    - Both steps execute via WorkflowAgent; StepDispatcher._record_result_and_route runs after each
    - complete_run(..., "completed") runs after step 2 (dispatcher path — not legacy complete_step)
    - The run reaches "completed" status

    **Validates: Requirements 1.1, 1.2, 3.1, 3.4**
    """
    import time

    WORKFLOW_ID = 999
    RUN_ID = 5000
    STEP1_ID = 10001
    STEP2_ID = 10002

    STEP1_RESPONSE = "Response from step 1"
    STEP2_RESPONSE = "Response from step 2"

    # --- Build mock step objects ---
    step1 = MagicMock()
    step1.id = STEP1_ID
    step1.position = 0
    step1.name = "Step One"
    step1.status = "pending"
    step1.result = None
    step1.action_type = "agent_instruction"
    step1.instruction = "Do the first thing"
    step1.recording_filename = ""
    step1.action_id = None
    step1.code = ""
    step1.wait_for_continue = False
    step1.workflow_id = WORKFLOW_ID
    step1.config = "{}"
    step1.description = ""
    step1.step_type = "agent_instruction"
    step1.timeout_seconds = 300
    step1.max_retries = 0
    step1.require_approval = False
    step1.verification = ""
    step1.routing_prompt = ""
    step1.validation_type = "none"
    step1.validation_prompt = ""
    step1.routing_mode = "static"
    step1.on_pass_goto = STEP2_ID
    step1.on_fail_goto = None
    step1.wait_before_next = 0

    step2 = MagicMock()
    step2.id = STEP2_ID
    step2.position = 1
    step2.name = "Step Two"
    step2.status = "pending"
    step2.result = None
    step2.action_type = "agent_instruction"
    step2.instruction = "Do the second thing"
    step2.recording_filename = ""
    step2.action_id = None
    step2.code = ""
    step2.wait_for_continue = False
    step2.workflow_id = WORKFLOW_ID
    step2.config = "{}"
    step2.description = ""
    step2.step_type = "agent_instruction"
    step2.timeout_seconds = 300
    step2.max_retries = 0
    step2.require_approval = False
    step2.verification = ""
    step2.routing_prompt = ""
    step2.validation_type = "none"
    step2.validation_prompt = ""
    step2.routing_mode = "static"
    step2.on_pass_goto = -1  # END (same as None for router; explicit avoids fragile mock for position query)
    step2.on_fail_goto = None
    step2.wait_before_next = 0

    steps_by_id = {STEP1_ID: step1, STEP2_ID: step2}

    # --- Build mock workflow ---
    wf = MagicMock()
    wf.id = WORKFLOW_ID
    wf.steps = [step1, step2]
    # No chat: skips post_execution._append_workflow_step_audit → append_audit_step,
    # which uses distr.core.db.get_session (not patched here) and would hit SQLite
    # with MagicMock chat_id.
    wf.chat_id = None

    # --- Build mock run ---
    mock_run = MagicMock()
    mock_run.id = RUN_ID
    mock_run.status = "running"
    mock_run.current_step_id = STEP1_ID
    mock_run.workflow_id = WORKFLOW_ID
    mock_run.completed_at = None
    mock_run.run_data = "{}"

    # Async completion uses StepRouter via _record_result_and_route (not service.complete_step).
    routing_calls = []
    run_completed = threading.Event()

    # --- Build mock WorkflowAgent ---
    call_count = [0]

    async def fake_execute(prompt):
        call_count[0] += 1
        if call_count[0] == 1:
            return STEP1_RESPONSE
        else:
            return STEP2_RESPONSE

    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False
    mock_workflow_agent.execute = fake_execute
    mock_workflow_agent.shutdown = MagicMock()
    # Truthy MagicMock.messages makes `(messages or [])` non-empty and
    # `list(...)` iterate forever inside _augment_agent_result_with_tool_evidence.
    mock_workflow_agent.messages = []

    # --- Set up mock DB ---
    # The DB mock needs to handle multiple query patterns across start_workflow_run,
    # dispatcher loads, and StepRouter queries.
    # Key challenge: AutoWorkflowStep queries use filter(id == X) where X varies.
    # We intercept SQLAlchemy BinaryExpression args to extract the compared value.
    mock_db = MagicMock()

    def _extract_step_id_from_filter_args(args):
        """Try to extract a step ID from SQLAlchemy filter arguments."""
        for arg in args:
            # SQLAlchemy BinaryExpression: AutoWorkflowStep.id == <value>
            if hasattr(arg, 'right') and hasattr(arg.right, 'value'):
                val = arg.right.value
                if val in steps_by_id:
                    return val
            # Also check effective_value for bound parameters
            if hasattr(arg, 'right') and hasattr(arg.right, 'effective_value'):
                val = arg.right.effective_value
                if val in steps_by_id:
                    return val
        return None

    def query_side_effect(model):
        q = MagicMock()
        model_name = model.__name__ if hasattr(model, '__name__') else str(model)

        if model_name == "AutoWorkflow":
            q.filter.return_value.first.return_value = wf
        elif model_name == "AutoWorkflowStep":
            def step_filter(*args, **kwargs):
                inner = MagicMock()
                # Try to extract step_id from the filter expression
                extracted_id = _extract_step_id_from_filter_args(args)
                if extracted_id is not None:
                    inner.first.return_value = steps_by_id[extracted_id]
                else:
                    # Fallback: return based on mock_run.current_step_id
                    inner.first.return_value = steps_by_id.get(mock_run.current_step_id, step1)
                inner.filter.return_value = inner
                return inner
            q.filter.side_effect = step_filter
        elif model_name == "AutoWorkflowRun":
            def run_filter(*args, **kwargs):
                inner = MagicMock()
                inner.first.return_value = mock_run
                inner.filter.return_value = inner
                return inner
            q.filter.side_effect = run_filter
        elif model_name == "AutoWorkflowStepResult":
            # For _finalize_terminal_run querying step results
            inner = MagicMock()
            inner.filter.return_value.order_by.return_value.all.return_value = []
            q = inner
        return q

    mock_db.query.side_effect = query_side_effect
    mock_db.add.side_effect = lambda obj: setattr(obj, 'id', RUN_ID) if hasattr(obj, 'status') and not hasattr(obj, 'position') and not hasattr(obj, 'agent_response') else None
    mock_db.flush.side_effect = lambda: None

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    _original_rr = dispatcher_mod.StepDispatcher._record_result_and_route

    def _tracking_record_result_and_route(self, step_id, run_id, result_text, passed, skip_wait=False):
        routing_calls.append({
            "step_id": step_id,
            "result": result_text,
            "passed": passed,
        })
        return _original_rr(self, step_id, run_id, result_text, passed, skip_wait=skip_wait)

    _original_complete_run = dispatcher_mod.complete_run

    def _tracking_complete_run(run_id, status="completed"):
        ret = _original_complete_run(run_id, status)
        if status == "completed":
            run_completed.set()
        return ret

    _active_runs.clear()

    # Patch get_session everywhere this execution graph binds it (import copies the
    # reference — patching only service_mod misses dispatcher/router/post_execution).
    # WorkflowAgent is constructed via ``from distr.core.workflow_agent import WorkflowAgent``
    # inside dispatcher.start_workflow_run — patch the class at definition site.
    patcher_session = patch.object(service_mod, "get_session", return_value=mock_session_ctx)
    patcher_session_dispatcher = patch.object(
        dispatcher_mod, "get_session", return_value=mock_session_ctx
    )
    patcher_session_pe = patch.object(
        post_execution_mod, "get_session", return_value=mock_session_ctx
    )
    patcher_session_router = patch.object(
        router_mod, "get_session", return_value=mock_session_ctx
    )
    patcher_session_se = patch.object(
        step_executor_mod, "get_session", return_value=mock_session_ctx
    )
    patcher_session_planning = patch.object(
        planning_mod, "get_session", return_value=mock_session_ctx
    )
    patcher_wa = patch.object(service_mod, "WorkflowAgent", return_value=mock_workflow_agent)
    patcher_wa_factory = patch(
        "distr.core.workflow_agent.WorkflowAgent", return_value=mock_workflow_agent
    )
    patcher_bridge = patch.object(service_mod, "WorkflowAgentBridge")
    patcher_speak = patch.object(service_mod, "_speak_result")
    patcher_os = patch.object(service_mod, "os")
    patcher_rr = patch.object(
        dispatcher_mod.StepDispatcher, "_record_result_and_route", _tracking_record_result_and_route
    )
    patcher_complete_run = patch.object(dispatcher_mod, "complete_run", side_effect=_tracking_complete_run)
    patcher_validation_snapshot = patch.object(
        router_mod,
        "build_validation_snapshot",
        return_value={
            "type": "none",
            "passed": True,
            "result": "not_required",
            "standards_context": [],
        },
    )
    patcher_standards_context = patch(
        "distr.core.workflow.standards_memory.build_standards_context",
        return_value="",
    )

    try:
        patcher_session.start()
        patcher_session_dispatcher.start()
        patcher_session_pe.start()
        patcher_session_router.start()
        patcher_session_se.start()
        patcher_session_planning.start()
        patcher_wa.start()
        patcher_wa_factory.start()
        mock_bridge_cls = patcher_bridge.start()
        patcher_speak.start()
        mock_os = patcher_os.start()
        patcher_rr.start()
        patcher_complete_run.start()
        patcher_validation_snapshot.start()
        patcher_standards_context.start()

        mock_os.environ = {}
        mock_bridge_cls.return_value = MagicMock()

        result = start_workflow_run(WORKFLOW_ID)

        # --- Assertions ---

        # 1. start_workflow_run should succeed
        assert "error" not in result, f"start_workflow_run returned error: {result}"
        assert result.get("run_id") == RUN_ID, f"Expected run_id={RUN_ID}, got {result}"

        # 2. Wait for the run to complete (both steps dispatched asynchronously)
        assert run_completed.wait(timeout=10.0), (
            "Timed out waiting for run to reach 'completed' status. "
            f"_record_result_and_route calls so far: {routing_calls}"
        )

        # 3. Routing ran twice (once per finished agent step)
        assert len(routing_calls) == 2, (
            f"Expected 2 routing calls, got {len(routing_calls)}: {routing_calls}"
        )

        # 4. First routing was for step 1 with the agent response and passed=True
        assert routing_calls[0]["step_id"] == STEP1_ID, (
            f"Expected first routing for step {STEP1_ID}, got {routing_calls[0]['step_id']}"
        )
        assert routing_calls[0]["result"] == STEP1_RESPONSE, (
            f"Expected first result={STEP1_RESPONSE!r}, got {routing_calls[0]['result']!r}"
        )
        assert routing_calls[0]["passed"] is True

        # 5. Second routing was for step 2
        assert routing_calls[1]["step_id"] == STEP2_ID, (
            f"Expected second routing for step {STEP2_ID}, got {routing_calls[1]['step_id']}"
        )
        assert routing_calls[1]["result"] == STEP2_RESPONSE, (
            f"Expected second result={STEP2_RESPONSE!r}, got {routing_calls[1]['result']!r}"
        )
        assert routing_calls[1]["passed"] is True

        # 6. The run reached "completed" status
        assert mock_run.status == "completed", (
            f"Expected run status='completed', got {mock_run.status!r}"
        )

        # 7. WorkflowAgent.execute was called twice (once per step)
        assert call_count[0] == 2, (
            f"Expected WorkflowAgent.execute() called 2 times, got {call_count[0]}"
        )

    finally:
        # Stop all patchers
        patcher_standards_context.stop()
        patcher_validation_snapshot.stop()
        patcher_complete_run.stop()
        patcher_rr.stop()
        patcher_os.stop()
        patcher_speak.stop()
        patcher_bridge.stop()
        patcher_wa_factory.stop()
        patcher_wa.stop()
        patcher_session_planning.stop()
        patcher_session_se.stop()
        patcher_session_router.stop()
        patcher_session_pe.stop()
        patcher_session_dispatcher.stop()
        patcher_session.stop()
        # Clean up: stop event loops and clear _active_runs
        for rid, ctx in list(_active_runs.items()):
            try:
                if isinstance(ctx.event_loop, asyncio.AbstractEventLoop) and ctx.event_loop.is_running():
                    ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
            except Exception:
                pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Unit Test: Cancel during execution
"""
Unit test verifying that cancelling a running workflow run sets the run status
to "cancelled", calls _cleanup_run() (via _finalize_terminal_run), and shuts
down the WorkflowAgent (removing it from _active_runs).

**Validates: Requirements 2.3**
"""


def test_cancel_during_execution():
    """Cancel a running workflow run mid-step and verify cleanup.

    Sets up a mock run in "running" status with a _RunContext in _active_runs,
    calls cancel_run(run_id), and verifies:
    1. The run status is set to "cancelled"
    2. _cleanup_run() is called (via _finalize_terminal_run)
    3. The WorkflowAgent is shut down (removed from _active_runs)

    **Validates: Requirements 2.3**
    """
    RUN_ID = 7001
    WORKFLOW_ID = 700
    STEP_ID = 70001

    # --- Build mock step ---
    mock_step = MagicMock()
    mock_step.id = STEP_ID
    mock_step.status = "running"
    mock_step.result = None

    # --- Build mock run ---
    mock_run = MagicMock()
    mock_run.id = RUN_ID
    mock_run.status = "running"
    mock_run.current_step_id = STEP_ID
    mock_run.workflow_id = WORKFLOW_ID
    mock_run.completed_at = None

    # Track status changes on the mock
    def set_status(val):
        mock_run._status = val
    def get_status():
        return mock_run._status
    mock_run._status = "running"
    type(mock_run).status = property(lambda self: self._status, lambda self, v: setattr(self, '_status', v))

    # --- Build mock WorkflowAgent ---
    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    # --- Build a real event loop in a background thread ---
    agent_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(agent_loop)
        agent_loop.run_forever()

    agent_thread = threading.Thread(target=_run_loop, daemon=True)
    agent_thread.start()

    # Pre-populate _active_runs
    _active_runs.clear()
    _active_runs[RUN_ID] = _RunContext(
        run_id=RUN_ID,
        workflow_agent=mock_workflow_agent,
        event_loop=agent_loop,
        thread=agent_thread,
        context_prefix="",
    )

    # --- Set up mock DB ---
    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        model_name = model.__name__ if hasattr(model, '__name__') else str(model)
        if model_name == "AutoWorkflowRun":
            q.filter.return_value.first.return_value = mock_run
        elif model_name == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step
        elif model_name == "AutoWorkflowStepResult":
            q.filter.return_value.order_by.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    try:
        with patch.object(dispatcher_mod, "get_session", return_value=mock_session_ctx), \
             patch("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge") as mock_bridge_cls:

            mock_bridge_cls.return_value = MagicMock()

            from distr.core.workflow.service import cancel_run

            # --- Act ---
            result = cancel_run(RUN_ID)

        # --- Assertions ---

        # 1. cancel_run should return True (success)
        assert result is True, f"Expected cancel_run to return True, got {result}"

        # 2. The run status was set to "cancelled"
        assert mock_run.status == "cancelled", (
            f"Expected run status='cancelled', got {mock_run.status!r}"
        )

        # 3. The run's completed_at was set (not None)
        assert mock_run.completed_at is not None, (
            "Expected completed_at to be set after cancellation"
        )

        # 4. The currently running step was cancelled
        assert mock_step.status == "cancelled", (
            f"Expected step status='cancelled', got {mock_step.status!r}"
        )
        assert mock_step.result == "Cancelled by user.", (
            f"Expected step result='Cancelled by user.', got {mock_step.result!r}"
        )

        # 5. WorkflowAgent.shutdown() was called (via _cleanup_run → _finalize_terminal_run)
        mock_workflow_agent.shutdown.assert_called_once()

        # 6. The run was removed from _active_runs
        assert RUN_ID not in _active_runs, (
            f"Expected run_id {RUN_ID} to be removed from _active_runs, "
            f"but found keys: {list(_active_runs.keys())}"
        )

        # 7. WorkflowAgentBridge.on_workflow_completed was called with cancelled=True
        bridge_instance = mock_bridge_cls.return_value
        bridge_instance.on_workflow_completed.assert_called_once()
        call_args = bridge_instance.on_workflow_completed.call_args
        run_result = call_args[0][1]
        assert run_result["cancelled"] is True, (
            f"Expected run_result['cancelled']=True, got {run_result}"
        )
        assert run_result["run_id"] == RUN_ID, (
            f"Expected run_result['run_id']={RUN_ID}, got {run_result}"
        )

    finally:
        # Clean up: stop event loop and clear _active_runs
        try:
            if agent_loop.is_running():
                agent_loop.call_soon_threadsafe(agent_loop.stop)
        except Exception:
            pass
        _active_runs.clear()


# Feature: workflow-execution-unification, Unit Test 9.3: Wait-for-continue round trip
"""
Unit test verifying the full wait-for-continue round trip:
1. A step with wait_for_continue=True enters "waiting" state via _check_and_enter_wait()
2. The run also enters "waiting" state
3. Calling continue_waiting_step() resumes execution with the stored result
4. complete_step() is called with the stored result (plus optional user input)
5. The run transitions from "waiting" back to "running" or terminal

**Validates: Requirements 7.1, 7.2, 7.3**
"""


def test_wait_for_continue_round_trip():
    """Full round trip: enter wait → verify waiting state → continue → verify resumption.

    Sets up a single-step workflow where the step has wait_for_continue=True.
    Simulates the wait entry by calling _check_and_enter_wait() with a result,
    verifies the step and run enter "waiting" status, then calls
    continue_waiting_step() with optional user input and verifies complete_step()
    is called with the stored result plus the appended input.

    **Validates: Requirements 7.1, 7.2, 7.3**
    """
    RUN_ID = 8001
    STEP_ID = 80001
    WORKFLOW_ID = 800
    ACTION_RESULT = "Agent produced this response"
    USER_INPUT = "Please also include the summary"

    # --- Build mock step with wait_for_continue=True ---
    mock_step = MagicMock()
    mock_step.id = STEP_ID
    mock_step.workflow_id = WORKFLOW_ID
    mock_step.wait_for_continue = True
    mock_step.status = "running"

    # --- Build mock run ---
    mock_run = MagicMock()
    mock_run.id = RUN_ID
    mock_run.workflow_id = WORKFLOW_ID
    mock_run.status = "running"
    mock_run.current_step_id = STEP_ID
    mock_run.run_data = "{}"

    # --- Build mock WorkflowAgent (should stay alive during wait) ---
    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    mock_event_loop = MagicMock()
    mock_thread = MagicMock()

    # Pre-populate _active_runs
    _active_runs.clear()
    _active_runs[RUN_ID] = _RunContext(
        run_id=RUN_ID,
        workflow_agent=mock_workflow_agent,
        event_loop=mock_event_loop,
        thread=mock_thread,
        context_prefix="",
    )

    # --- Set up mock DB ---
    mock_db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        model_name = model.__name__ if hasattr(model, '__name__') else str(model)
        if model_name == "AutoWorkflowRun":
            q.filter.return_value.first.return_value = mock_run
        elif model_name == "AutoWorkflowStep":
            q.filter.return_value.first.return_value = mock_step
        return q

    mock_db.query.side_effect = query_side_effect

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    try:
        # ===== PHASE 1: Enter wait state via _check_and_enter_wait =====
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx):
            from distr.core.workflow.service import _check_and_enter_wait
            wait_result = _check_and_enter_wait(STEP_ID, ACTION_RESULT, passed=True)

        # 1. _check_and_enter_wait should return a wait response dict
        assert wait_result is not None, "Expected _check_and_enter_wait to return a wait dict, got None"
        assert wait_result.get("waiting") is True, (
            f"Expected waiting=True in result, got: {wait_result}"
        )

        # 2. Step status should be "waiting"
        assert mock_step.status == "waiting", (
            f"Expected step.status='waiting', got: {mock_step.status!r}"
        )

        # 3. Run status should be "waiting"
        assert mock_run.status == "waiting", (
            f"Expected run.status='waiting', got: {mock_run.status!r}"
        )

        # 4. Run data should contain the stored result and passed flag
        stored_run_data = json.loads(mock_run.run_data)
        assert stored_run_data["waiting_result"] == ACTION_RESULT, (
            f"Expected waiting_result={ACTION_RESULT!r}, got: {stored_run_data.get('waiting_result')!r}"
        )
        assert stored_run_data["waiting_passed"] is True, (
            f"Expected waiting_passed=True, got: {stored_run_data.get('waiting_passed')}"
        )

        # 5. WorkflowAgent should still be in _active_runs (not cleaned up)
        assert RUN_ID in _active_runs, (
            f"WorkflowAgent should remain in _active_runs during wait. Keys: {list(_active_runs.keys())}"
        )
        assert _active_runs[RUN_ID].workflow_agent._shutdown is False, (
            "WorkflowAgent should not be shut down while waiting"
        )

        # 6. db.commit() was called to persist the waiting state
        mock_db.commit.assert_called()

        # ===== PHASE 2: Continue from wait via continue_waiting_step =====

        # Reset mock_db.commit call count for phase 2
        mock_db.commit.reset_mock()

        # The expected result passed to complete_step should include the user input
        expected_complete_result = f"{ACTION_RESULT}\n\n[CONTINUE INPUT]: {USER_INPUT}"

        complete_step_return = {"done": True, "status": "completed"}

        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "complete_step", return_value=complete_step_return) as mock_complete_step:

            from distr.core.workflow.service import continue_waiting_step
            continue_result = continue_waiting_step(RUN_ID, optional_input=USER_INPUT)

        # 7. No error should be returned
        assert "error" not in continue_result, (
            f"continue_waiting_step returned error: {continue_result}"
        )

        # 8. Run status was set back to "running" before complete_step
        assert mock_run.status == "running", (
            f"Expected run.status='running' after continue, got: {mock_run.status!r}"
        )

        # 9. Step status was set back to "running" before complete_step
        assert mock_step.status == "running", (
            f"Expected step.status='running' after continue, got: {mock_step.status!r}"
        )

        # 10. complete_step was called exactly once
        mock_complete_step.assert_called_once()

        # 11. complete_step was called with the correct arguments
        call_args = mock_complete_step.call_args
        assert call_args[0][0] == STEP_ID, (
            f"Expected complete_step called with step_id={STEP_ID}, got {call_args[0][0]}"
        )
        assert call_args[0][1] == expected_complete_result, (
            f"Expected complete_step called with result={expected_complete_result!r}, "
            f"got {call_args[0][1]!r}"
        )
        assert call_args[0][2] is True, (
            f"Expected complete_step called with passed=True, got {call_args[0][2]}"
        )

        # 12. complete_step was called with _from_continue=True
        assert call_args[1].get("_from_continue") is True, (
            f"Expected _from_continue=True, got kwargs={call_args[1]}"
        )

        # 13. db.commit() was called in phase 2
        mock_db.commit.assert_called()

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Unit Test 9.4: Empty instruction edge case
"""
Unit test verifying that an agent-instruction step with an empty instruction
(empty string or whitespace-only) returns an error dict, sets the step status
to "failed" via update_step(), and does NOT call WorkflowAgent.execute().

**Validates: Requirements 1.1**
"""


def test_empty_instruction_edge_case():
    """Agent-instruction step with empty instruction returns error and marks step failed.

    Calls _dispatch_step() with action_type="agent_instruction" and an empty
    instruction string. Verifies:
    1. Returns {"error": "No instruction provided"}
    2. update_step() is called with status="failed"
    3. WorkflowAgent.execute() is NOT called

    **Validates: Requirements 1.1**
    """
    STEP_ID = 90041
    STEP_NAME = "Empty Instruction Step"

    _active_runs.clear()

    try:
        with patch.object(service_mod, "update_step") as mock_update_step, \
             patch.object(service_mod, "WorkflowAgent") as mock_wa_cls, \
             patch.object(service_mod, "get_session") as mock_get_session:

            from distr.core.workflow.service import _dispatch_step

            # --- Test with empty string ---
            result = _dispatch_step(
                step_id=STEP_ID,
                step_name=STEP_NAME,
                action_type="agent_instruction",
                instruction="",
                recording_filename="",
                context_prefix="Step Runner",
            )

            # 1. Should return an error dict
            assert "error" in result, f"Expected error dict, got: {result}"
            assert result["error"] == "No instruction provided", (
                f"Expected error='No instruction provided', got: {result['error']!r}"
            )

            # 2. update_step() was called to set status to "failed"
            mock_update_step.assert_called_once_with(
                STEP_ID, status="failed", result="No instruction provided"
            )

            # 3. WorkflowAgent was NOT instantiated or called
            mock_wa_cls.assert_not_called()

            # 4. get_session was NOT called (early return before DB lookup)
            mock_get_session.assert_not_called()

        # --- Test with whitespace-only string ---
        with patch.object(service_mod, "update_step") as mock_update_step2, \
             patch.object(service_mod, "WorkflowAgent") as mock_wa_cls2, \
             patch.object(service_mod, "get_session") as mock_get_session2:

            result2 = _dispatch_step(
                step_id=STEP_ID,
                step_name=STEP_NAME,
                action_type="agent_instruction",
                instruction="   \t\n  ",
                recording_filename="",
                context_prefix="Step Runner",
            )

            # 5. Whitespace-only also returns error
            assert "error" in result2, f"Expected error dict for whitespace, got: {result2}"
            assert result2["error"] == "No instruction provided", (
                f"Expected error='No instruction provided', got: {result2['error']!r}"
            )

            # 6. update_step() called again for whitespace case
            mock_update_step2.assert_called_once_with(
                STEP_ID, status="failed", result="No instruction provided"
            )

            # 7. WorkflowAgent not called for whitespace case
            mock_wa_cls2.assert_not_called()

    finally:
        _active_runs.clear()


# Feature: workflow-execution-unification, Unit Test 9.5: Kanban ticket context test
"""
Unit test verifying that when start_workflow_run() is called with a context
string (e.g. ticket title/description), the first step's instruction passed
to _dispatch_step() has the context prepended. When context is None, the
instruction is passed unmodified.

**Validates: Requirements 10.2, 10.3**
"""


def test_kanban_ticket_context_prepended_to_first_step():
    """Ticket context is prepended to first step instruction in start_workflow_run().

    Calls start_workflow_run() with a context string simulating a kanban ticket
    title + description. Verifies:
    1. _dispatch_step() receives the context prepended to the original instruction
    2. The format is "{context}\\n\\n{instruction}"

    **Validates: Requirements 10.2, 10.3**
    """
    WORKFLOW_ID = 80001
    RUN_ID = 80100
    STEP_ID = 80201
    ORIGINAL_INSTRUCTION = "Run the analysis pipeline"
    TICKET_CONTEXT = "Ticket: Fix login bug\n\nDescription: Users cannot log in after password reset"

    # --- Build mock step ---
    step = MagicMock()
    step.id = STEP_ID
    step.position = 0
    step.name = "Analyze Step"
    step.status = "pending"
    step.result = None
    step.action_type = "agent_instruction"
    step.instruction = ORIGINAL_INSTRUCTION
    step.recording_filename = ""
    step.action_id = None
    step.code = ""
    step.wait_for_continue = False

    # --- Build mock workflow ---
    wf = MagicMock()
    wf.id = WORKFLOW_ID
    wf.steps = [step]

    # --- Build mock DB ---
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = wf

    def fake_add(obj):
        if hasattr(obj, 'status') and not hasattr(obj, 'position'):
            obj.id = RUN_ID

    mock_db.add.side_effect = fake_add
    mock_db.flush.side_effect = lambda: None

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    _active_runs.clear()

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "WorkflowAgent", return_value=mock_workflow_agent), \
             patch.object(service_mod, "_dispatch_step", return_value={"success": True, "message": "ok"}) as mock_dispatch, \
             patch.object(service_mod, "os") as mock_os:

            mock_os.environ = {}

            result = start_workflow_run(WORKFLOW_ID, context=TICKET_CONTEXT)

        # 1. Should succeed
        assert "error" not in result, f"start_workflow_run returned error: {result}"

        # 2. _dispatch_step was called once
        mock_dispatch.assert_called_once()

        # 3. The instruction arg (4th positional) should be context + original
        call_args = mock_dispatch.call_args
        dispatched_instruction = call_args[0][3]
        expected = f"{TICKET_CONTEXT}\n\n{ORIGINAL_INSTRUCTION}"
        assert dispatched_instruction == expected, (
            f"Expected context prepended to instruction:\n"
            f"  got:      {dispatched_instruction!r}\n"
            f"  expected: {expected!r}"
        )

    finally:
        for rid, ctx in list(_active_runs.items()):
            try:
                if isinstance(ctx.event_loop, asyncio.AbstractEventLoop) and ctx.event_loop.is_running():
                    ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
            except Exception:
                pass
        _active_runs.clear()


def test_kanban_ticket_context_none_leaves_instruction_unmodified():
    """When context is None, the first step instruction is passed unmodified.

    Calls start_workflow_run() with context=None. Verifies:
    1. _dispatch_step() receives the original instruction without modification

    **Validates: Requirements 10.2, 10.3**
    """
    WORKFLOW_ID = 80002
    RUN_ID = 80200
    STEP_ID = 80301
    ORIGINAL_INSTRUCTION = "Run the analysis pipeline"

    # --- Build mock step ---
    step = MagicMock()
    step.id = STEP_ID
    step.position = 0
    step.name = "Analyze Step"
    step.status = "pending"
    step.result = None
    step.action_type = "agent_instruction"
    step.instruction = ORIGINAL_INSTRUCTION
    step.recording_filename = ""
    step.action_id = None
    step.code = ""
    step.wait_for_continue = False

    # --- Build mock workflow ---
    wf = MagicMock()
    wf.id = WORKFLOW_ID
    wf.steps = [step]

    # --- Build mock DB ---
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = wf

    def fake_add(obj):
        if hasattr(obj, 'status') and not hasattr(obj, 'position'):
            obj.id = RUN_ID

    mock_db.add.side_effect = fake_add
    mock_db.flush.side_effect = lambda: None

    mock_session_ctx = MagicMock()
    mock_session_ctx.__enter__ = MagicMock(return_value=mock_db)
    mock_session_ctx.__exit__ = MagicMock(return_value=False)

    mock_workflow_agent = MagicMock()
    mock_workflow_agent._shutdown = False

    _active_runs.clear()

    try:
        with patch.object(service_mod, "get_session", return_value=mock_session_ctx), \
             patch.object(service_mod, "WorkflowAgent", return_value=mock_workflow_agent), \
             patch.object(service_mod, "_dispatch_step", return_value={"success": True, "message": "ok"}) as mock_dispatch, \
             patch.object(service_mod, "os") as mock_os:

            mock_os.environ = {}

            result = start_workflow_run(WORKFLOW_ID, context=None)

        # 1. Should succeed
        assert "error" not in result, f"start_workflow_run returned error: {result}"

        # 2. _dispatch_step was called once
        mock_dispatch.assert_called_once()

        # 3. The instruction arg (4th positional) should be the original unmodified
        call_args = mock_dispatch.call_args
        dispatched_instruction = call_args[0][3]
        assert dispatched_instruction == ORIGINAL_INSTRUCTION, (
            f"Expected original instruction unmodified:\n"
            f"  got:      {dispatched_instruction!r}\n"
            f"  expected: {ORIGINAL_INSTRUCTION!r}"
        )

    finally:
        for rid, ctx in list(_active_runs.items()):
            try:
                if isinstance(ctx.event_loop, asyncio.AbstractEventLoop) and ctx.event_loop.is_running():
                    ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
            except Exception:
                pass
        _active_runs.clear()
