"""Unit tests for distr.core.step_runner.test_loop.TestLoopService."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from distr.core.step_runner.step_types import StepType
from distr.core.step_runner.test_loop import (
    ExecutionResult,
    TestLoopService,
    TestResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(fix_side_effects=None):
    """Create a TestLoopService with a mocked CodeGeneratorService."""
    mock_cg = MagicMock()
    if fix_side_effects is not None:
        mock_cg.fix_code.side_effect = fix_side_effects
    svc = TestLoopService(code_generator=mock_cg)
    return svc, mock_cg


# ---------------------------------------------------------------------------
# _execute_python
# ---------------------------------------------------------------------------

class TestExecutePython:
    def test_successful_script(self):
        svc = TestLoopService()
        result = svc._execute_python("print('hello')")
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.stderr == ""

    def test_failing_script(self):
        svc = TestLoopService()
        result = svc._execute_python("import sys; sys.exit(1)")
        assert result.exit_code == 1

    def test_syntax_error(self):
        svc = TestLoopService()
        result = svc._execute_python("def bad(")
        assert result.exit_code != 0
        assert result.stderr != ""

    def test_timeout(self):
        svc = TestLoopService()
        result = svc._execute_python("import time; time.sleep(10)", timeout=1)
        assert result.exit_code == 1
        assert "timed out" in result.stderr.lower()

    def test_captures_stderr(self):
        svc = TestLoopService()
        result = svc._execute_python(
            "import sys; sys.stderr.write('oops'); sys.exit(0)"
        )
        assert result.exit_code == 0
        assert "oops" in result.stderr


# ---------------------------------------------------------------------------
# _execute_playwright
# ---------------------------------------------------------------------------

class TestExecutePlaywright:
    def test_headless_env_var_true(self):
        """When headless=True, PLAYWRIGHT_HEADLESS should be '1'."""
        svc = TestLoopService()
        code = "import os; print(os.environ.get('PLAYWRIGHT_HEADLESS', 'missing'))"
        result = svc._execute_playwright(code, headless=True, timeout=30)
        assert result.exit_code == 0
        assert "1" in result.stdout

    def test_headless_env_var_false(self):
        """When headless=False, PLAYWRIGHT_HEADLESS should be '0'."""
        svc = TestLoopService()
        code = "import os; print(os.environ.get('PLAYWRIGHT_HEADLESS', 'missing'))"
        result = svc._execute_playwright(code, headless=False, timeout=30)
        assert result.exit_code == 0
        assert "0" in result.stdout

    def test_timeout_default_is_120(self):
        """Default timeout for playwright is 120s (we just verify the param exists)."""
        import inspect
        sig = inspect.signature(TestLoopService._execute_playwright)
        assert sig.parameters["timeout"].default == 120

    def test_injects_headless_preamble(self):
        """The _HEADLESS variable should be injected and accessible."""
        svc = TestLoopService()
        code = "print(_HEADLESS)"
        result = svc._execute_playwright(code, headless=True, timeout=30)
        assert result.exit_code == 0
        assert "True" in result.stdout


# ---------------------------------------------------------------------------
# run_test — success on first attempt
# ---------------------------------------------------------------------------

class TestRunTestSuccess:
    def test_success_first_attempt(self):
        svc, mock_cg = _make_service()
        code = "print('ok')"
        result = svc.run_test(code, StepType.EXECUTE_CODE, {"instruction": "test"})

        assert result.success is True
        assert len(result.attempts) == 1
        assert result.attempts[0]["exit_code"] == 0
        assert "ok" in result.output
        mock_cg.fix_code.assert_not_called()

    def test_success_returns_original_code(self):
        svc, _ = _make_service()
        code = "print('hello')"
        result = svc.run_test(code, StepType.EXECUTE_CODE, {})
        assert result.code == code


# ---------------------------------------------------------------------------
# run_test — fix loop
# ---------------------------------------------------------------------------

class TestRunTestFixLoop:
    def test_fix_succeeds_on_second_attempt(self):
        """First attempt fails, fix_code returns working code, second succeeds."""
        fixed_code = "print('fixed')"
        svc, mock_cg = _make_service(fix_side_effects=[fixed_code])

        bad_code = "import sys; sys.exit(1)"
        result = svc.run_test(
            bad_code, StepType.EXECUTE_CODE, {"instruction": "do stuff"}
        )

        assert result.success is True
        assert result.code == fixed_code
        assert len(result.attempts) == 2
        assert result.attempts[0]["exit_code"] != 0
        assert result.attempts[1]["exit_code"] == 0
        mock_cg.fix_code.assert_called_once()

    def test_all_attempts_fail(self):
        """All 3 attempts fail — returns failure with all attempt logs."""
        svc, mock_cg = _make_service(
            fix_side_effects=[
                "import sys; sys.exit(2)",  # fix attempt 1 → still fails
                "import sys; sys.exit(3)",  # fix attempt 2 → still fails
            ]
        )

        bad_code = "import sys; sys.exit(1)"
        result = svc.run_test(
            bad_code, StepType.EXECUTE_CODE, {"instruction": "do stuff"}
        )

        assert result.success is False
        assert len(result.attempts) == 3
        # fix_code called twice (after attempt 1 and 2, not after attempt 3)
        assert mock_cg.fix_code.call_count == 2

    def test_max_attempts_is_three(self):
        assert TestLoopService.MAX_FIX_ATTEMPTS == 3

    def test_fix_code_runtime_error_stops_loop(self):
        """If fix_code raises RuntimeError, loop stops early."""
        svc, mock_cg = _make_service(
            fix_side_effects=RuntimeError("LLM unreachable")
        )

        bad_code = "import sys; sys.exit(1)"
        result = svc.run_test(
            bad_code, StepType.EXECUTE_CODE, {"instruction": "do stuff"}
        )

        assert result.success is False
        assert len(result.attempts) == 1  # stopped after first failure


# ---------------------------------------------------------------------------
# run_test — config extraction
# ---------------------------------------------------------------------------

class TestRunTestConfig:
    def test_extracts_instruction_from_config(self):
        """instruction is passed to fix_code from config dict."""
        fixed_code = "print('ok')"
        svc, mock_cg = _make_service(fix_side_effects=[fixed_code])

        bad_code = "import sys; sys.exit(1)"
        svc.run_test(
            bad_code,
            StepType.EXECUTE_CODE,
            {"instruction": "my instruction"},
        )

        call_kwargs = mock_cg.fix_code.call_args
        assert call_kwargs[1]["instruction"] == "my instruction" or \
               call_kwargs.kwargs.get("instruction") == "my instruction"

    def test_extracts_headless_from_config(self):
        """For Playwright steps, headless flag is read from config."""
        svc, _ = _make_service()
        code = "import os; print(os.environ.get('PLAYWRIGHT_HEADLESS', 'missing'))"
        result = svc.run_test(
            code, StepType.PLAYWRIGHT, {"headless": False}
        )
        assert result.success is True
        assert "0" in result.output

    def test_headless_defaults_to_true(self):
        """When headless not in config, defaults to True."""
        svc, _ = _make_service()
        code = "import os; print(os.environ.get('PLAYWRIGHT_HEADLESS', 'missing'))"
        result = svc.run_test(code, StepType.PLAYWRIGHT, {})
        assert result.success is True
        assert "1" in result.output


# ---------------------------------------------------------------------------
# run_test — attempt log structure
# ---------------------------------------------------------------------------

class TestAttemptLogStructure:
    def test_attempt_log_keys(self):
        svc, _ = _make_service()
        result = svc.run_test("print('hi')", StepType.EXECUTE_CODE, {})
        attempt = result.attempts[0]
        assert "attempt" in attempt
        assert "code" in attempt
        assert "exit_code" in attempt
        assert "stdout" in attempt
        assert "stderr" in attempt

    def test_attempt_numbers_sequential(self):
        svc, mock_cg = _make_service(
            fix_side_effects=[
                "import sys; sys.exit(1)",
                "print('ok')",
            ]
        )
        bad_code = "import sys; sys.exit(1)"
        result = svc.run_test(bad_code, StepType.EXECUTE_CODE, {"instruction": ""})
        for i, attempt in enumerate(result.attempts):
            assert attempt["attempt"] == i + 1


# ---------------------------------------------------------------------------
# Dataclass sanity
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_execution_result_fields(self):
        r = ExecutionResult(exit_code=0, stdout="out", stderr="err")
        assert r.exit_code == 0
        assert r.stdout == "out"
        assert r.stderr == "err"

    def test_test_result_fields(self):
        r = TestResult(success=True, code="x", attempts=[], output="y")
        assert r.success is True
        assert r.code == "x"
        assert r.attempts == []
        assert r.output == "y"
