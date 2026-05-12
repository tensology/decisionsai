"""Tests for the new Step Runner API route logic (Task 9).

Tests validation endpoint logic, code generation endpoint, test-code endpoint,
step CRUD updates, and results history inclusion.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from distr.core.workflow_engine.validation import StepValidator


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

    @patch("distr.core.workflow_engine.code_generator.CodeGeneratorService._call_coding_llm")
    def test_generate_code_returns_code(self, mock_llm):
        from distr.core.workflow_engine.code_generator import CodeGeneratorService
        from distr.core.workflow_engine.step_types import StepType

        mock_llm.return_value = "print('hello world')"
        svc = CodeGeneratorService()
        code = svc.generate_code("print hello world", StepType.EXECUTE_CODE)
        assert code == "print('hello world')"
        mock_llm.assert_called_once()

    @patch("distr.core.workflow_engine.code_generator.CodeGeneratorService._call_coding_llm")
    def test_generate_code_playwright(self, mock_llm):
        from distr.core.workflow_engine.code_generator import CodeGeneratorService
        from distr.core.workflow_engine.step_types import StepType

        mock_llm.return_value = "from playwright.sync_api import sync_playwright"
        svc = CodeGeneratorService()
        code = svc.generate_code("open google", StepType.PLAYWRIGHT)
        assert "playwright" in code
        mock_llm.assert_called_once()

    @patch("distr.core.workflow_engine.code_generator.CodeGeneratorService._call_coding_llm")
    def test_generate_code_llm_error_raises_runtime(self, mock_llm):
        from distr.core.workflow_engine.code_generator import CodeGeneratorService
        from distr.core.workflow_engine.step_types import StepType

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
        __import__("distr.core.workflow_engine.test_loop", fromlist=["TestLoopService"]).TestLoopService,
        "_run_in_subprocess",
    )
    def test_run_test_success(self, mock_subprocess):
        from distr.core.workflow_engine.test_loop import TestLoopService, ExecutionResult
        from distr.core.workflow_engine.step_types import StepType

        mock_subprocess.return_value = ExecutionResult(exit_code=0, stdout="ok", stderr="")
        svc = TestLoopService()
        result = svc.run_test("print('ok')", StepType.EXECUTE_CODE, {})
        assert result.success is True
        assert result.output == "ok"
        assert len(result.attempts) == 1

    @patch.object(
        __import__("distr.core.workflow_engine.test_loop", fromlist=["TestLoopService"]).TestLoopService,
        "_run_in_subprocess",
    )
    @patch("distr.core.workflow_engine.code_generator.CodeGeneratorService.fix_code")
    def test_run_test_failure_exhausts_attempts(self, mock_fix, mock_subprocess):
        from distr.core.workflow_engine.test_loop import TestLoopService, ExecutionResult
        from distr.core.workflow_engine.step_types import StepType

        mock_subprocess.return_value = ExecutionResult(exit_code=1, stdout="", stderr="error")
        mock_fix.return_value = "print('fixed')"
        svc = TestLoopService()
        result = svc.run_test("bad code", StepType.EXECUTE_CODE, {})
        assert result.success is False
        assert len(result.attempts) == 3

    def test_test_result_includes_code_and_attempts(self):
        from distr.core.workflow_engine.test_loop import TestResult
        result = TestResult(
            success=True,
            code="print('ok')",
            attempts=[{"attempt": 1}],
            output="ok",
        )
        assert result.code == "print('ok')"
        assert len(result.attempts) == 1


# ---------------------------------------------------------------------------
# Sub-task 9.4: Step CRUD - current workflow service API
# ---------------------------------------------------------------------------

class TestStepCRUDNewFields:
    """Test that update_step handles config/code and generic fields."""

    @patch("distr.core.workflow.service.get_session")
    def test_update_step_with_instruction_and_code(self, mock_get_session):
        from distr.core.workflow.service import update_step

        mock_step = MagicMock()
        mock_step.id = 1
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_step
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = update_step(1, instruction="run ls", code="print('hello')")
        assert result is True
        assert mock_step.instruction == "run ls"
        assert mock_step.code == "print('hello')"
        mock_db.commit.assert_called_once()

    @patch("distr.core.workflow.service.get_session")
    def test_update_step_with_status_field(self, mock_get_session):
        from distr.core.workflow.service import update_step

        mock_step = MagicMock()
        mock_step.id = 1
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_step
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        result = update_step(1, status="completed")
        assert result is True
        assert mock_step.status == "completed"
        mock_db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Sub-task 9.5: Results history - current workflow run history API
# ---------------------------------------------------------------------------

class TestResultsHistory:
    """Test get_run_history includes modern workflow metadata fields."""

    @patch("distr.core.workflow.service.get_session")
    def test_get_run_history_includes_phase_source_and_links(self, mock_get_session):
        from distr.core.workflow.service import get_run_history

        mock_run = MagicMock()
        mock_run.id = 10
        mock_run.started_at = None
        mock_run.completed_at = None
        mock_run.status = "running"
        mock_run.current_step_id = 3
        mock_run.board_id = 7
        mock_run.ticket_id = 9
        mock_run.run_data = json.dumps({
            "phase": "execution",
            "source_type": "kanban",
            "result_packet": {
                "status": "completed",
                "summary": "Workflow run evidence captured.",
                "audit": {"final_verdict": "pass", "rationale": "Validated."},
                "artifacts": {
                    "screenshots": ["/tmp/decisions/workflow_screenshots/step_1.png"],
                    "logs": ["/tmp/decisions/logs/workflow_10.log"],
                },
                "execution": {
                    "action_trace": [
                        {"action_type": "click", "description": "open menu", "result": "ok"},
                    ],
                    "validation_snapshots": [
                        {
                            "validation_type": "text_match",
                            "expected": "success visible",
                            "verdict": "pass",
                        },
                    ],
                },
            },
        })

        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_run]
        mock_db = MagicMock()
        mock_db.query.return_value = mock_query
        mock_get_session.return_value.__enter__ = MagicMock(return_value=mock_db)
        mock_get_session.return_value.__exit__ = MagicMock(return_value=False)

        rows = get_run_history(1, limit=5)
        assert len(rows) == 1
        assert rows[0]["id"] == 10
        assert rows[0]["phase"] == "execution"
        assert rows[0]["source_type"] == "kanban"
        assert rows[0]["board_id"] == 7
        assert rows[0]["ticket_id"] == 9
        assert rows[0]["result_packet"]["summary"] == "Workflow run evidence captured."
        assert rows[0]["result_packet"]["artifacts"]["screenshots"] == [
            "/tmp/decisions/workflow_screenshots/step_1.png"
        ]
        assert rows[0]["result_packet"]["execution"]["action_trace"][0]["action_type"] == "click"
        assert rows[0]["result_packet"]["execution"]["validation_snapshots"][0]["expected"] == "success visible"
