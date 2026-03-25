"""Property-based tests for TestLoopService using Hypothesis.

Covers Properties 7–8 from the design document:
  - Property 7: Test-fix loop termination bound (Task 5.3)
  - Property 8: Successful execution on exit code 0 (Task 5.4)
"""

from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from distr.core.step_runner.step_types import StepType
from distr.core.step_runner.test_loop import (
    ExecutionResult,
    TestLoopService,
    TestResult,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Random code strings (non-empty)
random_code = st.text(min_size=1, max_size=200)

# Random stdout/stderr output
random_output = st.text(max_size=500)

# Step types that the test loop handles
code_step_types = st.sampled_from([StepType.EXECUTE_CODE, StepType.PLAYWRIGHT])

# Non-zero exit codes
nonzero_exit_codes = st.integers(min_value=1, max_value=255)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service_with_mock_cg(fix_responses):
    """Create a TestLoopService with a mocked CodeGeneratorService.

    *fix_responses* is a list of code strings that fix_code returns
    sequentially.
    """
    mock_cg = MagicMock()
    mock_cg.fix_code.side_effect = list(fix_responses)
    svc = TestLoopService(code_generator=mock_cg)
    return svc, mock_cg


# ---------------------------------------------------------------------------
# Property 7: Test-fix loop termination bound
# **Validates: Requirements 4.4, 4.5**
#
# For any code string and any sequence of LLM fix responses, the Test_Loop
# must terminate after at most 3 execution attempts, returning the final
# code and all attempt logs regardless of whether any attempt succeeded.
# ---------------------------------------------------------------------------


class TestProperty7TerminationBound:
    """Property 7: Test-fix loop termination bound."""

    @given(
        initial_code=random_code,
        fix_code_1=random_code,
        fix_code_2=random_code,
        exit_code_1=nonzero_exit_codes,
        exit_code_2=nonzero_exit_codes,
        exit_code_3=nonzero_exit_codes,
        stderr_1=random_output,
        stderr_2=random_output,
        stderr_3=random_output,
        step_type=code_step_types,
    )
    @settings(max_examples=100)
    def test_always_terminates_within_max_attempts(
        self,
        initial_code,
        fix_code_1,
        fix_code_2,
        exit_code_1,
        exit_code_2,
        exit_code_3,
        stderr_1,
        stderr_2,
        stderr_3,
        step_type,
    ):
        """**Validates: Requirements 4.4, 4.5**

        When _execute_python/_execute_playwright always returns a non-zero
        exit code, the loop must terminate after exactly MAX_FIX_ATTEMPTS
        (3) attempts and return all attempt logs.
        """
        exec_results = [
            ExecutionResult(exit_code=exit_code_1, stdout="", stderr=stderr_1),
            ExecutionResult(exit_code=exit_code_2, stdout="", stderr=stderr_2),
            ExecutionResult(exit_code=exit_code_3, stdout="", stderr=stderr_3),
        ]

        svc, mock_cg = _make_service_with_mock_cg([fix_code_1, fix_code_2])

        with patch.object(svc, "_execute_python", side_effect=exec_results), \
             patch.object(svc, "_execute_playwright", side_effect=exec_results):
            result = svc.run_test(
                initial_code, step_type, {"instruction": "test"}
            )

        assert len(result.attempts) <= TestLoopService.MAX_FIX_ATTEMPTS
        assert len(result.attempts) == 3
        assert result.success is False
        # All attempt logs are present
        for i, attempt in enumerate(result.attempts):
            assert attempt["attempt"] == i + 1
            assert "code" in attempt
            assert "exit_code" in attempt
            assert "stdout" in attempt
            assert "stderr" in attempt

    @given(
        initial_code=random_code,
        fix_code_1=random_code,
        fix_code_2=random_code,
        step_type=code_step_types,
    )
    @settings(max_examples=100)
    def test_attempts_never_exceed_max(
        self,
        initial_code,
        fix_code_1,
        fix_code_2,
        step_type,
    ):
        """**Validates: Requirements 4.4, 4.5**

        Regardless of the fix responses, the number of attempts in the
        result must never exceed MAX_FIX_ATTEMPTS.
        """
        # All executions fail
        fail_result = ExecutionResult(exit_code=1, stdout="", stderr="error")
        exec_results = [fail_result, fail_result, fail_result]

        svc, _ = _make_service_with_mock_cg([fix_code_1, fix_code_2])

        with patch.object(svc, "_execute_python", side_effect=exec_results), \
             patch.object(svc, "_execute_playwright", side_effect=exec_results):
            result = svc.run_test(
                initial_code, step_type, {"instruction": ""}
            )

        assert len(result.attempts) <= TestLoopService.MAX_FIX_ATTEMPTS

    @given(
        initial_code=random_code,
        fix_code_1=random_code,
        fix_code_2=random_code,
        step_type=code_step_types,
    )
    @settings(max_examples=100)
    def test_result_contains_final_code(
        self,
        initial_code,
        fix_code_1,
        fix_code_2,
        step_type,
    ):
        """**Validates: Requirements 4.4, 4.5**

        When all attempts fail, the result must contain the last attempted
        code (the second fix response, since fix is called after attempts
        1 and 2 but not after attempt 3).
        """
        fail_result = ExecutionResult(exit_code=1, stdout="", stderr="err")
        exec_results = [fail_result, fail_result, fail_result]

        svc, _ = _make_service_with_mock_cg([fix_code_1, fix_code_2])

        with patch.object(svc, "_execute_python", side_effect=exec_results), \
             patch.object(svc, "_execute_playwright", side_effect=exec_results):
            result = svc.run_test(
                initial_code, step_type, {"instruction": ""}
            )

        # The final code should be the last fix response (fix_code_2)
        assert result.code == fix_code_2


# ---------------------------------------------------------------------------
# Property 8: Successful execution on exit code 0
# **Validates: Requirements 4.2**
#
# For any code execution that produces exit code 0, the Test_Loop must
# return a result with success=True and include the stdout output.
# ---------------------------------------------------------------------------


class TestProperty8SuccessOnExitCode0:
    """Property 8: Successful execution on exit code 0."""

    @given(
        code=random_code,
        stdout_text=random_output,
        step_type=code_step_types,
    )
    @settings(max_examples=100)
    def test_exit_code_0_returns_success_true(
        self,
        code,
        stdout_text,
        step_type,
    ):
        """**Validates: Requirements 4.2**

        When _execute_python/_execute_playwright returns exit_code=0, the
        result must have success=True and output must contain the stdout.
        """
        success_result = ExecutionResult(
            exit_code=0, stdout=stdout_text, stderr=""
        )

        svc = TestLoopService(code_generator=MagicMock())

        with patch.object(svc, "_execute_python", return_value=success_result), \
             patch.object(svc, "_execute_playwright", return_value=success_result):
            result = svc.run_test(code, step_type, {"instruction": "test"})

        assert result.success is True
        assert result.output == stdout_text

    @given(
        code=random_code,
        stdout_text=random_output,
        step_type=code_step_types,
    )
    @settings(max_examples=100)
    def test_success_on_first_attempt_has_single_attempt(
        self,
        code,
        stdout_text,
        step_type,
    ):
        """**Validates: Requirements 4.2**

        When the first execution succeeds, the result should have exactly
        one attempt and fix_code should never be called.
        """
        success_result = ExecutionResult(
            exit_code=0, stdout=stdout_text, stderr=""
        )

        svc = TestLoopService(code_generator=MagicMock())

        with patch.object(svc, "_execute_python", return_value=success_result), \
             patch.object(svc, "_execute_playwright", return_value=success_result):
            result = svc.run_test(code, step_type, {"instruction": ""})

        assert result.success is True
        assert len(result.attempts) == 1
        assert result.attempts[0]["exit_code"] == 0
        svc.code_generator.fix_code.assert_not_called()

    @given(
        initial_code=random_code,
        fix_code_str=random_code,
        stdout_text=random_output,
        step_type=code_step_types,
    )
    @settings(max_examples=100)
    def test_success_after_fix_returns_fixed_code(
        self,
        initial_code,
        fix_code_str,
        stdout_text,
        step_type,
    ):
        """**Validates: Requirements 4.2**

        When the first attempt fails but the fix succeeds (exit_code=0),
        the result must have success=True, contain the fixed code, and
        include the stdout from the successful attempt.
        """
        fail_result = ExecutionResult(exit_code=1, stdout="", stderr="error")
        success_result = ExecutionResult(
            exit_code=0, stdout=stdout_text, stderr=""
        )

        svc, mock_cg = _make_service_with_mock_cg([fix_code_str])

        exec_results = [fail_result, success_result]

        with patch.object(svc, "_execute_python", side_effect=exec_results), \
             patch.object(svc, "_execute_playwright", side_effect=exec_results):
            result = svc.run_test(
                initial_code, step_type, {"instruction": "test"}
            )

        assert result.success is True
        assert result.code == fix_code_str
        assert result.output == stdout_text
        assert len(result.attempts) == 2
