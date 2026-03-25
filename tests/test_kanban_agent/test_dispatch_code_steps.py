# Feature: kanban-agent-workflow
"""
Property tests for code step dispatch and Playwright validation.

- Property 18: Playwright validation exit code mapping (Validates: Requirements 13.9)
- Property 19: Code step dispatch executes via TestLoopService (Validates: Requirements 14.1, 14.2, 14.5, 14.6)
- Property 20: Code generation fallback when code is empty (Validates: Requirements 14.3)
"""
import contextlib
from dataclasses import dataclass
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import (  # noqa: F401
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)


def _make_session_factory():
    """Create an in-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    """SessionContext-compatible context manager for patching get_session."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass
class MockExecResult:
    """Mock for TestLoopService execution results."""
    exit_code: int
    stdout: str
    stderr: str


class TestPlaywrightValidationExitCodeMapping:
    """Property 18: Playwright validation exit code mapping.

    *For any* step with validation_type="playwright" and non-empty validation_code,
    when the validation script executes with exit code 0 the verification should
    return True, and when it executes with a non-zero exit code the verification
    should return False.

    **Validates: Requirements 13.9**
    """

    @given(
        exit_code=st.integers(min_value=0, max_value=255),
        validation_code=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()),
        output=st.text(max_size=100),
    )
    @settings(max_examples=50, deadline=None)
    def test_playwright_exit_code_mapping(self, exit_code, validation_code, output):
        """Exit code 0 → True, non-zero → False for playwright validation."""
        from distr.core.workflow.service import _run_verification

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            # Create a step with playwright validation
            session = factory()
            try:
                wf = AutoWorkflow(name="test-wf")
                session.add(wf)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="pw-step",
                    action_type="playwright",
                    validation_type="playwright",
                    validation_code=validation_code,
                )
                session.add(step)
                session.commit()
                session.refresh(step)

                mock_result = MockExecResult(exit_code=exit_code, stdout=output, stderr="")

                mock_tls_instance = MagicMock()
                mock_tls_instance._execute_playwright.return_value = mock_result

                with patch(
                    "distr.core.step_runner.test_loop.TestLoopService",
                    return_value=mock_tls_instance,
                ):
                    result = _run_verification(step, "some result", True)

                    if exit_code == 0:
                        assert result is True, f"Expected True for exit_code=0, got {result}"
                    else:
                        assert result is False, f"Expected False for exit_code={exit_code}, got {result}"
            finally:
                session.close()



class TestCodeStepDispatchViaTestLoopService:
    """Property 19: Code step dispatch executes via TestLoopService.

    *For any* step with action_type "execute_code" or "playwright" and non-empty
    code, _dispatch_step should execute the code via TestLoopService (not send it
    as text to the agent). The step result success should equal (exit_code == 0).

    **Validates: Requirements 14.1, 14.2, 14.5, 14.6**
    """

    @given(
        action_type=st.sampled_from(["execute_code", "playwright"]),
        code=st.text(min_size=1, max_size=500).filter(lambda s: s.strip()),
        exit_code=st.integers(min_value=0, max_value=255),
        stdout_text=st.text(max_size=100),
        stderr_text=st.text(max_size=100),
    )
    @settings(max_examples=50, deadline=None)
    def test_code_dispatch_uses_test_loop_service(
        self, action_type, code, exit_code, stdout_text, stderr_text
    ):
        """_dispatch_step calls TestLoopService for code/playwright steps."""
        from distr.core.workflow.service import _dispatch_step

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            # Create workflow + step in DB so complete_step can find it
            session = factory()
            try:
                wf = AutoWorkflow(name="test-wf")
                session.add(wf)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="code-step",
                    action_type=action_type,
                    code=code,
                    status="running",
                )
                session.add(step)
                session.commit()
                session.refresh(step)
                step_id = step.id
            finally:
                session.close()

            mock_result = MockExecResult(
                exit_code=exit_code, stdout=stdout_text, stderr=stderr_text
            )

            mock_tls_instance = MagicMock()
            mock_tls_instance._execute_python.return_value = mock_result
            mock_tls_instance._execute_playwright.return_value = mock_result

            with patch(
                "distr.core.step_runner.test_loop.TestLoopService",
                return_value=mock_tls_instance,
            ):
                # Also mock complete_step to avoid side effects
                with patch(
                    "distr.core.workflow.service.complete_step"
                ) as mock_complete:
                    mock_complete.return_value = {"done": True, "status": "passed" if exit_code == 0 else "failed"}

                    result = _dispatch_step(
                        step_id=step_id,
                        step_name="code-step",
                        action_type=action_type,
                        instruction="",
                        recording_filename="",
                        code=code,
                    )

                    # Verify TestLoopService was used (not signal_manager)
                    assert "success" in result, f"Expected success, got {result}"

                    # _dispatch_step strips the code before passing to TestLoopService
                    stripped_code = code.strip()
                    if action_type == "execute_code":
                        mock_tls_instance._execute_python.assert_called_once_with(stripped_code)
                    else:
                        mock_tls_instance._execute_playwright.assert_called_once_with(stripped_code)

                    # Verify complete_step was called with correct passed flag
                    mock_complete.assert_called_once()
                    call_args = mock_complete.call_args
                    assert call_args[0][0] == step_id  # step_id
                    assert call_args[0][2] == (exit_code == 0)  # passed



class TestCodeGenerationFallback:
    """Property 20: Code generation fallback when code is empty.

    *For any* step with action_type "execute_code" or "playwright" that has an
    instruction but no code, _dispatch_step should invoke
    CodeGeneratorService.generate_code() with the instruction before executing.

    **Validates: Requirements 14.3**
    """

    @given(
        action_type=st.sampled_from(["execute_code", "playwright"]),
        instruction=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()),
    )
    @settings(max_examples=50, deadline=None)
    def test_code_generation_fallback(self, action_type, instruction):
        """When code is empty but instruction exists, CodeGeneratorService is called."""
        from distr.core.workflow.service import _dispatch_step
        from distr.core.step_runner.step_types import StepType

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            # Create workflow + step in DB
            session = factory()
            try:
                wf = AutoWorkflow(name="test-wf")
                session.add(wf)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="gen-step",
                    action_type=action_type,
                    instruction=instruction,
                    code="",  # empty code
                    status="running",
                )
                session.add(step)
                session.commit()
                session.refresh(step)
                step_id = step.id
            finally:
                session.close()

            generated_code = "print('generated')"
            mock_exec = MockExecResult(exit_code=0, stdout="generated", stderr="")

            mock_cgs_instance = MagicMock()
            mock_cgs_instance.generate_code.return_value = generated_code

            mock_tls_instance = MagicMock()
            mock_tls_instance._execute_python.return_value = mock_exec
            mock_tls_instance._execute_playwright.return_value = mock_exec

            with patch(
                "distr.core.step_runner.code_generator.CodeGeneratorService",
                return_value=mock_cgs_instance,
            ):
                with patch(
                    "distr.core.step_runner.test_loop.TestLoopService",
                    return_value=mock_tls_instance,
                ):
                    with patch(
                        "distr.core.workflow.service.complete_step"
                    ) as mock_complete:
                        mock_complete.return_value = {"done": True, "status": "passed"}

                        result = _dispatch_step(
                            step_id=step_id,
                            step_name="gen-step",
                            action_type=action_type,
                            instruction=instruction,
                            recording_filename="",
                            code="",  # empty code triggers fallback
                        )

                        # Verify CodeGeneratorService.generate_code was called
                        mock_cgs_instance.generate_code.assert_called_once()
                        call_args = mock_cgs_instance.generate_code.call_args
                        assert call_args[0][0] == instruction  # instruction arg
                        expected_step_type = (
                            StepType.PLAYWRIGHT if action_type == "playwright" else StepType.EXECUTE_CODE
                        )
                        assert call_args[0][1] == expected_step_type

                        # Verify the generated code was then executed
                        assert "success" in result, f"Expected success, got {result}"
                        if action_type == "execute_code":
                            mock_tls_instance._execute_python.assert_called_once_with(generated_code)
                        else:
                            mock_tls_instance._execute_playwright.assert_called_once_with(generated_code)
