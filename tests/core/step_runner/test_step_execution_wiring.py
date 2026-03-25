"""Unit tests for task 16: validation before execution and type-specific executors.

Tests cover:
- 16.1: Validation is called before execution; failures mark step as failed and skip
- 16.2: Type-specific routing for run_command, http_request, execute_code, playwright, play_recording
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from distr.app.step_runner import StepRunnerMixin, _DIRECT_EXECUTION_TYPES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orch(session_id=1, steps_data=None, prior_results=None, session_instruction="Do stuff"):
    """Build a minimal orchestration dict."""
    signal = MagicMock()
    if steps_data is None:
        steps_data = [{"id": 10, "title": "Step 1", "instruction": "Run something"}]
    return {
        "session_id": session_id,
        "steps_data": steps_data,
        "prior_results": prior_results or [],
        "session_instruction": session_instruction,
        "signal_send_text_input": signal,
        "is_retry": False,
        "retry_count": 0,
        "max_retries": 2,
        "on_failure": "skip",
        "is_verification_step": False,
        "any_step_succeeded": False,
        "current_index": 0,
        "chat_id": None,
        "session_type": "instruction",
        "run_id": None,
    }


def _make_db_session(context_rules=None, workflow_input=None):
    return SimpleNamespace(
        id=1,
        context_rules=context_rules,
        workflow_input=(
            json.dumps(workflow_input) if isinstance(workflow_input, dict) else workflow_input
        ),
    )


def _make_db_step(step_id=10, step_type="run_command", config=None):
    return SimpleNamespace(
        id=step_id,
        step_type=step_type,
        config=json.dumps(config) if isinstance(config, dict) else config,
    )


def _patch_db(mock_get_session, db_session, db_step):
    """Wire up the mock DB context manager to return our objects."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.side_effect = [db_session, db_step]
    mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
    mock_get_session.return_value.__exit__ = MagicMock(return_value=False)
    return mock_db


# ===========================================================================
# 16.1 — Validation before execution
# ===========================================================================

class TestValidationBeforeExecution:
    """Verify that validation is called before step execution and failures are handled."""

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_invalid_run_command_fails_step(self, mock_get_session, mock_assemble):
        """A run_command step with empty command fails validation and is marked failed."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="run_command", config={})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._finish_step_runner_orchestration = MagicMock()

        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        # Step should be marked as failed with validation error
        fail_calls = [c for c in mixin._set_step_status.call_args_list if c[0][1] == "failed"]
        assert len(fail_calls) >= 1
        result_text = fail_calls[0].kwargs.get("result", "")
        assert "Validation failed" in result_text
        assert "command" in result_text.lower()

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_invalid_http_request_fails_step(self, mock_get_session, mock_assemble):
        """An http_request step with empty URL fails validation."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=20, step_type="http_request", config={"url": ""})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={"url": ""})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._finish_step_runner_orchestration = MagicMock()

        orch = _make_orch(steps_data=[{"id": 20, "title": "HTTP Step", "instruction": "fetch"}])
        mixin._send_step_runner_instruction(orch, 0)

        fail_calls = [c for c in mixin._set_step_status.call_args_list if c[0][1] == "failed"]
        assert len(fail_calls) >= 1
        result_text = fail_calls[0].kwargs.get("result", "")
        assert "Validation failed" in result_text
        assert "url" in result_text.lower()

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.step_runner.service.build_step_context_prompt", return_value="prompt")
    @patch("distr.core.db.get_session")
    def test_valid_run_command_proceeds(self, mock_get_session, mock_build, mock_assemble):
        """A run_command step with valid config passes validation and proceeds to execution."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="run_command", config={"command": "echo hello"})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={"command": "echo hello"})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._execute_step_directly = MagicMock()

        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        # Should NOT be marked as failed
        fail_calls = [c for c in mixin._set_step_status.call_args_list if c[0][1] == "failed"]
        assert len(fail_calls) == 0
        # Should proceed to direct execution
        mixin._execute_step_directly.assert_called_once()

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.step_runner.service.build_step_context_prompt", return_value="prompt")
    @patch("distr.core.db.get_session")
    def test_agent_instruction_skips_validation(self, mock_get_session, mock_build, mock_assemble):
        """Agent instruction steps are not validated (they go straight to agent)."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="agent_instruction", config={})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(workflow_rules="rules")
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        # Signal should be emitted (agent instruction path)
        orch["signal_send_text_input"].emit.assert_called_once()

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_validation_failure_skips_to_next_step(self, mock_get_session, mock_assemble):
        """When validation fails and there's a next step, orchestration advances."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="run_command", config={})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._reset_step_runner_timeout = MagicMock()
        # Prevent recursive call from actually doing anything complex
        mixin._finish_step_runner_orchestration = MagicMock()

        steps = [
            {"id": 10, "title": "Step 1", "instruction": "bad step"},
            {"id": 11, "title": "Step 2", "instruction": "next step"},
        ]
        orch = _make_orch(steps_data=steps)
        # Mock the recursive _send_step_runner_instruction to avoid infinite loop
        original_send = mixin._send_step_runner_instruction
        call_count = [0]
        def mock_send(o, idx, prompt=None):
            call_count[0] += 1
            if call_count[0] == 1:
                original_send(o, idx, prompt)
            # Don't recurse further
        mixin._send_step_runner_instruction = mock_send

        mixin._send_step_runner_instruction(orch, 0)

        # Step 10 should be failed, step 11 should be set to running
        status_calls = mixin._set_step_status.call_args_list
        failed_calls = [c for c in status_calls if c[0] == (10, "failed") or (len(c[0]) >= 2 and c[0][0] == 10 and c[0][1] == "failed")]
        running_calls = [c for c in status_calls if len(c[0]) >= 2 and c[0][0] == 11 and c[0][1] == "running"]
        assert len(failed_calls) >= 1
        assert len(running_calls) >= 1


# ===========================================================================
# 16.2 — Type-specific execution routing
# ===========================================================================

class TestTypeSpecificRouting:
    """Verify that non-agent step types are routed to direct execution."""

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_run_command_routes_to_direct_execution(self, mock_get_session, mock_assemble):
        """run_command steps are routed to _execute_step_directly."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="run_command", config={"command": "echo hi"})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={"command": "echo hi"})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._execute_step_directly = MagicMock()

        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        mixin._execute_step_directly.assert_called_once_with(orch, 0, "run_command", mock_ctx)
        # Signal should NOT be emitted (not going to agent)
        orch["signal_send_text_input"].emit.assert_not_called()

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_http_request_routes_to_direct_execution(self, mock_get_session, mock_assemble):
        """http_request steps are routed to _execute_step_directly."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="http_request", config={"url": "https://example.com"})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={"url": "https://example.com"})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._execute_step_directly = MagicMock()

        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        mixin._execute_step_directly.assert_called_once_with(orch, 0, "http_request", mock_ctx)

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_execute_code_routes_to_direct_execution(self, mock_get_session, mock_assemble):
        """execute_code steps are routed to _execute_step_directly."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="execute_code", config={"code": "print('hi')"})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={"code": "print('hi')"})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._execute_step_directly = MagicMock()

        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        mixin._execute_step_directly.assert_called_once_with(orch, 0, "execute_code", mock_ctx)

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_playwright_routes_to_direct_execution(self, mock_get_session, mock_assemble):
        """playwright steps are routed to _execute_step_directly."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="playwright", config={"code": "print('pw')"})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={"code": "print('pw')"})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._execute_step_directly = MagicMock()

        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        mixin._execute_step_directly.assert_called_once_with(orch, 0, "playwright", mock_ctx)

    @patch("distr.core.step_runner.context_assembly.assemble_step_context")
    @patch("distr.core.db.get_session")
    def test_play_recording_routes_to_direct_execution(self, mock_get_session, mock_assemble):
        """play_recording steps are routed to _execute_step_directly."""
        from distr.core.step_runner.context_assembly import StepInputContext

        db_session = _make_db_session()
        db_step = _make_db_step(step_id=10, step_type="play_recording", config={"recording_name": "my_rec"})
        _patch_db(mock_get_session, db_session, db_step)

        mock_ctx = StepInputContext(step_config={"recording_name": "my_rec"})
        mock_assemble.return_value = mock_ctx

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._execute_step_directly = MagicMock()

        orch = _make_orch()
        mixin._send_step_runner_instruction(orch, 0)

        mixin._execute_step_directly.assert_called_once_with(orch, 0, "play_recording", mock_ctx)


class TestDirectExecutionTypes:
    """Test the constant that defines which types are directly executed."""

    def test_direct_execution_types(self):
        assert _DIRECT_EXECUTION_TYPES == {
            "run_command", "http_request", "execute_code", "playwright", "play_recording"
        }


class TestExecRunCommand:
    """Test the _exec_run_command static method."""

    def test_successful_command(self):
        result, success = StepRunnerMixin._exec_run_command({"command": "echo hello"})
        assert success is True
        assert "hello" in result

    def test_failing_command(self):
        result, success = StepRunnerMixin._exec_run_command({"command": "exit 1"})
        assert success is False

    def test_timeout(self):
        result, success = StepRunnerMixin._exec_run_command({
            "command": "sleep 10",
            "timeout_seconds": 1,
        })
        assert success is False
        assert "timed out" in result.lower()

    def test_empty_command(self):
        # Empty command still runs (shell handles it), but we test it doesn't crash
        result, success = StepRunnerMixin._exec_run_command({"command": ""})
        # Behavior depends on shell, but should not raise


class TestExecExecuteCode:
    """Test the _exec_execute_code static method."""

    def test_successful_code(self):
        result, success = StepRunnerMixin._exec_execute_code(
            {"code": "print('hello from code')"},
            {"id": 1, "code": ""},
        )
        assert success is True
        assert "hello from code" in result

    def test_failing_code(self):
        result, success = StepRunnerMixin._exec_execute_code(
            {"code": "raise ValueError('boom')"},
            {"id": 1, "code": ""},
        )
        assert success is False
        assert "boom" in result

    def test_no_code(self):
        result, success = StepRunnerMixin._exec_execute_code(
            {"code": ""},
            {"id": 1, "code": ""},
        )
        assert success is False
        assert "No code" in result

    def test_code_from_step_data_fallback(self):
        """If config has no code, falls back to step_data code."""
        result, success = StepRunnerMixin._exec_execute_code(
            {},
            {"id": 1, "code": "print('from step data')"},
        )
        assert success is True
        assert "from step data" in result


class TestValidateStepConfig:
    """Test the _validate_step_config method."""

    def test_valid_config_returns_true(self):
        from distr.core.step_runner.context_assembly import StepInputContext

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._skip_to_next_step = MagicMock()

        ctx = StepInputContext(step_config={"command": "echo hi"})
        orch = _make_orch()
        result = mixin._validate_step_config(orch, 0, "run_command", ctx)
        assert result is True
        mixin._set_step_status.assert_not_called()

    def test_invalid_config_returns_false(self):
        from distr.core.step_runner.context_assembly import StepInputContext

        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._skip_to_next_step = MagicMock()

        ctx = StepInputContext(step_config={})
        orch = _make_orch()
        result = mixin._validate_step_config(orch, 0, "run_command", ctx)
        assert result is False
        mixin._set_step_status.assert_called_once()
        mixin._skip_to_next_step.assert_called_once()


class TestPlayRecordingExecution:
    """Test the _execute_play_recording method."""

    @patch("distr.app.step_runner.signal_manager")
    def test_play_by_name(self, mock_signal_manager):
        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._cancel_step_runner_timeout = MagicMock()
        mixin._advance_step_runner_orchestration = MagicMock()

        orch = _make_orch()
        config = {"recording_name": "my_recording"}
        mixin._execute_play_recording(orch, 0, config)

        mock_signal_manager.play_action_by_name.emit.assert_called_once_with("my_recording")
        mixin._set_step_status.assert_called_once()
        assert mixin._set_step_status.call_args[0][1] == "completed"

    def test_no_recording_specified(self):
        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._skip_to_next_step = MagicMock()

        orch = _make_orch()
        config = {}
        mixin._execute_play_recording(orch, 0, config)

        fail_calls = [c for c in mixin._set_step_status.call_args_list if c[0][1] == "failed"]
        assert len(fail_calls) == 1
        mixin._skip_to_next_step.assert_called_once()


class TestOnDirectStepCompleted:
    """Test the _on_direct_step_completed callback."""

    def test_success_advances_orchestration(self):
        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._cancel_step_runner_timeout = MagicMock()
        mixin._advance_step_runner_orchestration = MagicMock()

        orch = _make_orch()
        mixin._step_runner_orchestration = orch

        mixin._on_direct_step_completed(orch, 0, "output text", True)

        mixin._set_step_status.assert_called_once_with(10, "completed", result="output text")
        assert orch["any_step_succeeded"] is True
        mixin._advance_step_runner_orchestration.assert_called_once()

    def test_failure_triggers_error_handler(self):
        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()
        mixin._handle_step_runner_error = MagicMock()

        orch = _make_orch()
        mixin._step_runner_orchestration = orch

        mixin._on_direct_step_completed(orch, 0, "error occurred", False)

        mixin._set_step_status.assert_called_once_with(10, "failed", result="error occurred")
        mixin._handle_step_runner_error.assert_called_once()

    def test_cancelled_orchestration_is_ignored(self):
        """If orchestration was cancelled while thread was running, callback is a no-op."""
        mixin = StepRunnerMixin()
        mixin._set_step_status = MagicMock()

        orch = _make_orch()
        mixin._step_runner_orchestration = None  # Cancelled

        mixin._on_direct_step_completed(orch, 0, "output", True)

        mixin._set_step_status.assert_not_called()
