"""Tests for the new Step Runner API route logic (Task 9).

Tests validation endpoint logic, code generation endpoint, test-code endpoint,
step CRUD updates, and results history inclusion.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from distr.core.step_runner.validation import StepValidator


# ---------------------------------------------------------------------------
# Sub-task 9.1: Validation endpoint logic
# ---------------------------------------------------------------------------

class TestValidationEndpoint:
    """Test the validation logic used by POST /step-runner/validate."""

    def test_valid_run_command_returns_no_errors(self):
        errors = StepValidator().validate("run_command", {"command": "echo hello"})
        assert errors == []

    def test_invalid_run_command_returns_errors(self):
        errors = StepValidator().validate("run_command", {"command": ""})
        assert len(errors) == 1
        assert errors[0].field == "command"

    def test_valid_http_request_returns_no_errors(self):
        errors = StepValidator().validate("http_request", {"url": "https://example.com"})
        assert errors == []

    def test_invalid_http_request_missing_url(self):
        errors = StepValidator().validate("http_request", {"url": ""})
        assert len(errors) >= 1
        assert any(e.field == "url" for e in errors)

    def test_invalid_http_request_bad_method(self):
        errors = StepValidator().validate("http_request", {
            "url": "https://example.com",
            "method": "INVALID",
        })
        assert any(e.field == "method" for e in errors)

    def test_valid_execute_code_with_instruction(self):
        errors = StepValidator().validate("execute_code", {"instruction": "print hello"})
        assert errors == []

    def test_valid_execute_code_with_code(self):
        errors = StepValidator().validate("execute_code", {"code": "print('hello')"})
        assert errors == []

    def test_invalid_execute_code_empty(self):
        errors = StepValidator().validate("execute_code", {"instruction": "", "code": ""})
        assert len(errors) >= 1

    def test_valid_playwright_with_instruction(self):
        errors = StepValidator().validate("playwright", {"instruction": "go to google"})
        assert errors == []

    def test_invalid_playwright_empty(self):
        errors = StepValidator().validate("playwright", {"instruction": "", "code": ""})
        assert len(errors) >= 1

    def test_unknown_step_type(self):
        errors = StepValidator().validate("nonexistent_type", {})
        assert len(errors) == 1
        assert errors[0].field == "step_type"

    def test_valid_play_recording_with_id(self):
        errors = StepValidator().validate("play_recording", {"recording_id": 1})
        assert errors == []

    def test_invalid_play_recording_empty(self):
        errors = StepValidator().validate("play_recording", {})
        assert len(errors) >= 1
        assert any(e.field == "recording" for e in errors)

    def test_validation_errors_have_field_and_message(self):
        """All validation errors must have non-empty field and message."""
        errors = StepValidator().validate("run_command", {})
        for e in errors:
            assert e.field and len(e.field) > 0
            assert e.message and len(e.message) > 0


# ---------------------------------------------------------------------------
# Sub-task 9.2: Code generation endpoint logic
# ---------------------------------------------------------------------------

class TestCodeGenerationEndpoint:
    """Test the code generation logic used by POST /step-runner/generate-code."""

    @patch("distr.core.step_runner.code_generator.CodeGeneratorService._call_coding_llm")
    def test_generate_code_returns_code(self, mock_llm):
        from distr.core.step_runner.code_generator import CodeGeneratorService
        from distr.core.step_runner.step_types import StepType

        mock_llm.return_value = "print('hello world')"
        svc = CodeGeneratorService()
        code = svc.generate_code("print hello world", StepType.EXECUTE_CODE)
        assert code == "print('hello world')"
        mock_llm.assert_called_once()

    @patch("distr.core.step_runner.code_generator.CodeGeneratorService._call_coding_llm")
    def test_generate_code_playwright(self, mock_llm):
        from distr.core.step_runner.code_generator import CodeGeneratorService
        from distr.core.step_runner.step_types import StepType

        mock_llm.return_value = "from playwright.sync_api import sync_playwright"
        svc = CodeGeneratorService()
        code = svc.generate_code("open google", StepType.PLAYWRIGHT)
        assert "playwright" in code
        mock_llm.assert_called_once()

    @patch("distr.core.step_runner.code_generator.CodeGeneratorService._call_coding_llm")
    def test_generate_code_llm_error_raises_runtime(self, mock_llm):
        from distr.core.step_runner.code_generator import CodeGeneratorService
        from distr.core.step_runner.step_types import StepType

        mock_llm.side_effect = RuntimeError("LLM unreachable")
        svc = CodeGeneratorService()
        with pytest.raises(RuntimeError, match="LLM unreachable"):
            svc.generate_code("do something", StepType.EXECUTE_CODE)


# ---------------------------------------------------------------------------
# Sub-task 9.3: Test-code endpoint logic
# ---------------------------------------------------------------------------

class TestTestCodeEndpoint:
    """Test the test-code logic used by POST /step-runner/test-code."""

    @patch.object(
        __import__("distr.core.step_runner.test_loop", fromlist=["TestLoopService"]).TestLoopService,
        "_run_in_subprocess",
    )
    def test_run_test_success(self, mock_subprocess):
        from distr.core.step_runner.test_loop import TestLoopService, ExecutionResult
        from distr.core.step_runner.step_types import StepType

        mock_subprocess.return_value = ExecutionResult(exit_code=0, stdout="ok", stderr="")
        svc = TestLoopService()
        result = svc.run_test("print('ok')", StepType.EXECUTE_CODE, {})
        assert result.success is True
        assert result.output == "ok"
        assert len(result.attempts) == 1

    @patch.object(
        __import__("distr.core.step_runner.test_loop", fromlist=["TestLoopService"]).TestLoopService,
        "_run_in_subprocess",
    )
    @patch("distr.core.step_runner.code_generator.CodeGeneratorService.fix_code")
    def test_run_test_failure_exhausts_attempts(self, mock_fix, mock_subprocess):
        from distr.core.step_runner.test_loop import TestLoopService, ExecutionResult
        from distr.core.step_runner.step_types import StepType

        mock_subprocess.return_value = ExecutionResult(exit_code=1, stdout="", stderr="error")
        mock_fix.return_value = "print('fixed')"
        svc = TestLoopService()
        result = svc.run_test("bad code", StepType.EXECUTE_CODE, {})
        assert result.success is False
        assert len(result.attempts) == 3

    def test_test_result_includes_code_and_attempts(self):
        from distr.core.step_runner.test_loop import TestResult
        result = TestResult(
            success=True,
            code="print('ok')",
            attempts=[{"attempt": 1}],
            output="ok",
        )
        assert result.code == "print('ok')"
        assert len(result.attempts) == 1


# ---------------------------------------------------------------------------
# Sub-task 9.4: Step CRUD - new fields
# ---------------------------------------------------------------------------

class TestStepCRUDNewFields:
    """Test that update_step_status handles config and code fields."""

    @patch("distr.core.step_runner.service.get_session")
    def test_update_step_status_with_config_and_code(self, mock_get_session):
        from distr.core.step_runner.service import update_step_status

        mock_step = MagicMock()
        mock_step.id = 1
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_step
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = update_step_status(
            1,
            config='{"command": "ls"}',
            code="print('hello')",
        )
        assert result is True
        assert mock_step.config == '{"command": "ls"}'
        assert mock_step.code == "print('hello')"

    @patch("distr.core.step_runner.service.get_session")
    def test_update_step_status_without_new_fields(self, mock_get_session):
        from distr.core.step_runner.service import update_step_status

        mock_step = MagicMock()
        mock_step.id = 1
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_step
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = update_step_status(1, status="completed")
        assert result is True
        assert mock_step.status == "completed"


# ---------------------------------------------------------------------------
# Sub-task 9.5: Results History - run history for all session types
# ---------------------------------------------------------------------------

class TestResultsHistory:
    """Test that get_session_with_steps includes runs for all session types."""

    @patch("distr.core.step_runner.service.get_run_history")
    @patch("distr.core.step_runner.service.get_session")
    def test_get_session_includes_runs_for_instruction_type(self, mock_get_session, mock_run_history):
        from distr.core.step_runner.service import get_session_with_steps

        mock_run_history.return_value = [{"id": 1, "status": "completed"}]

        mock_session = MagicMock()
        mock_session.id = 1
        mock_session.instruction = "test"
        mock_session.status = "completed"
        mock_session.chat_id = None
        mock_session.session_type = "instruction"
        mock_session.schedule = None
        mock_session.next_run_at = None
        mock_session.last_run_at = None
        mock_session.schedule_time = None
        mock_session.schedule_days = None
        mock_session.timezone = None
        mock_session.enabled = True
        mock_session.created_date = None
        mock_session.context_rules = None
        mock_session.workflow_input = None
        mock_session.steps = []

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_session
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = get_session_with_steps(1)
        assert result is not None
        # Runs should be included for instruction type (not just scheduled)
        assert "runs" in result
        mock_run_history.assert_called_once_with(1, 5)
