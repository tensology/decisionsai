# Feature: kanban-agent-workflow
"""
Property tests for WAIT/CONTINUE execution primitive.

- Property 23: WAIT/CONTINUE step enters waiting status (Validates: Requirements 16.3, 16.4, 16.5)
- Property 24: CONTINUE resumes waiting step and triggers routing (Validates: Requirements 16.6, 16.7)
- Property 25: CONTINUE rejects non-waiting runs (Validates: Requirements 16.8)
- Property 26: CONTINUE with optional input appends to stored result (Validates: Requirements 16.9)
- Property 27: wait_for_continue round-trip persistence (Validates: Requirements 16.13)
"""
import contextlib
import json
from unittest.mock import patch, MagicMock

from hypothesis import given, settings
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


class TestWaitContinueEntersWaitingStatus:
    """Property 23: WAIT/CONTINUE step enters waiting status.

    *For any* step with `wait_for_continue=True` that completes its action
    successfully, the step status should be set to `waiting` and the associated
    run status should be set to `waiting`. The action result should be stored
    in `run_data` for later retrieval.

    **Validates: Requirements 16.3, 16.4, 16.5**
    """

    @given(
        action_result=st.text(min_size=0, max_size=500),
        passed=st.booleans(),
    )
    @settings(max_examples=50, deadline=None)
    def test_check_and_enter_wait(self, action_result, passed):
        """_check_and_enter_wait sets step/run to waiting and stores result."""
        from distr.core.workflow.service import _check_and_enter_wait

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            # Set up workflow, step with wait_for_continue=True, and a running run
            session = factory()
            try:
                wf = AutoWorkflow(name="wait-wf")
                session.add(wf)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="wait-step",
                    action_type="execute_code",
                    wait_for_continue=True,
                    status="running",
                )
                session.add(step)
                session.flush()
                run = AutoWorkflowRun(
                    workflow_id=wf.id,
                    status="running",
                    current_step_id=step.id,
                )
                session.add(run)
                session.commit()
                step_id = step.id
                run_id = run.id
            finally:
                session.close()

            # Call _check_and_enter_wait
            result = _check_and_enter_wait(step_id, action_result, passed)

            # Should return a wait response
            assert result is not None
            assert result["success"] is True
            assert result["waiting"] is True

            # Verify step status is now "waiting"
            session = factory()
            try:
                step = session.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.id == step_id
                ).first()
                assert step.status == "waiting"

                # Verify run status is now "waiting"
                run = session.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.id == run_id
                ).first()
                assert run.status == "waiting"

                # Verify action result stored in run_data
                run_data = json.loads(run.run_data or "{}")
                assert run_data["waiting_result"] == action_result
                assert run_data["waiting_passed"] == passed
            finally:
                session.close()



class TestContinueResumesWaitingStep:
    """Property 24: CONTINUE resumes waiting step and triggers routing.

    *For any* workflow run in `waiting` status with a waiting step, calling
    `continue_waiting_step()` should set the run status back to `running`,
    call `complete_step()` with the stored result, and proceed with normal
    validation and routing.

    **Validates: Requirements 16.6, 16.7**
    """

    @given(
        stored_result=st.text(min_size=0, max_size=500),
        stored_passed=st.booleans(),
    )
    @settings(max_examples=50, deadline=None)
    def test_continue_resumes_waiting_step(self, stored_result, stored_passed):
        """continue_waiting_step restores result and calls complete_step."""
        from distr.core.workflow.service import continue_waiting_step

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            # Set up a run in waiting status with stored run_data
            session = factory()
            try:
                wf = AutoWorkflow(name="wait-wf")
                session.add(wf)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="wait-step",
                    action_type="agent_instruction",
                    wait_for_continue=True,
                    status="waiting",
                )
                session.add(step)
                session.flush()
                run_data = json.dumps({
                    "waiting_result": stored_result,
                    "waiting_passed": stored_passed,
                })
                run = AutoWorkflowRun(
                    workflow_id=wf.id,
                    status="waiting",
                    current_step_id=step.id,
                    run_data=run_data,
                )
                session.add(run)
                session.commit()
                run_id = run.id
                step_id = step.id
            finally:
                session.close()

            # Mock complete_step to capture the call
            with patch(
                "distr.core.workflow.service.complete_step"
            ) as mock_complete:
                mock_complete.return_value = {"done": True, "status": "completed"}

                result = continue_waiting_step(run_id, optional_input="")

                # complete_step should have been called with stored result
                mock_complete.assert_called_once()
                call_args = mock_complete.call_args
                assert call_args[0][0] == step_id  # step_id
                assert call_args[0][1] == stored_result  # result
                assert call_args[0][2] == stored_passed  # passed
                assert call_args[1].get("_from_continue", call_args[0][3] if len(call_args[0]) > 3 else None) is True or \
                    (len(call_args[0]) > 3 and call_args[0][3] is True)

            # Verify run and step were set back to running before complete_step
            session = factory()
            try:
                run = session.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.id == run_id
                ).first()
                # After complete_step mock, the status was set to running
                # by continue_waiting_step before calling complete_step
                assert run.status == "running"

                step = session.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.id == step_id
                ).first()
                assert step.status == "running"
            finally:
                session.close()


class TestContinueRejectsNonWaitingRuns:
    """Property 25: CONTINUE rejects non-waiting runs.

    *For any* workflow run that is NOT in `waiting` status (running, completed,
    failed, cancelled), calling `continue_waiting_step()` should return an error
    with status code 409 and leave the run status unchanged.

    **Validates: Requirements 16.8**
    """

    @given(
        run_status=st.sampled_from(["running", "completed", "failed", "cancelled"]),
    )
    @settings(max_examples=50, deadline=None)
    def test_continue_rejects_non_waiting(self, run_status):
        """continue_waiting_step returns 409 for non-waiting runs."""
        from distr.core.workflow.service import continue_waiting_step

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            # Set up a run in a non-waiting status
            session = factory()
            try:
                wf = AutoWorkflow(name="test-wf")
                session.add(wf)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="step",
                    status="running",
                )
                session.add(step)
                session.flush()
                run = AutoWorkflowRun(
                    workflow_id=wf.id,
                    status=run_status,
                    current_step_id=step.id,
                )
                session.add(run)
                session.commit()
                run_id = run.id
            finally:
                session.close()

            result = continue_waiting_step(run_id)

            # Should return error with 409
            assert "error" in result
            assert result["status_code"] == 409

            # Run status should be unchanged
            session = factory()
            try:
                run = session.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.id == run_id
                ).first()
                assert run.status == run_status
            finally:
                session.close()


class TestContinueWithOptionalInput:
    """Property 26: CONTINUE with optional input appends to stored result.

    *For any* workflow run in `waiting` status, calling `continue_waiting_step()`
    with non-empty optional input should append the input to the stored action
    result before calling `complete_step()`.

    **Validates: Requirements 16.9**
    """

    @given(
        stored_result=st.text(min_size=0, max_size=300),
        optional_input=st.text(min_size=1, max_size=300).filter(lambda s: s.strip()),
        stored_passed=st.booleans(),
    )
    @settings(max_examples=50, deadline=None)
    def test_continue_appends_optional_input(self, stored_result, optional_input, stored_passed):
        """Optional input is appended to stored result as [CONTINUE INPUT]."""
        from distr.core.workflow.service import continue_waiting_step

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            session = factory()
            try:
                wf = AutoWorkflow(name="test-wf")
                session.add(wf)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=0,
                    name="wait-step",
                    action_type="agent_instruction",
                    wait_for_continue=True,
                    status="waiting",
                )
                session.add(step)
                session.flush()
                run_data = json.dumps({
                    "waiting_result": stored_result,
                    "waiting_passed": stored_passed,
                })
                run = AutoWorkflowRun(
                    workflow_id=wf.id,
                    status="waiting",
                    current_step_id=step.id,
                    run_data=run_data,
                )
                session.add(run)
                session.commit()
                run_id = run.id
                step_id = step.id
            finally:
                session.close()

            with patch(
                "distr.core.workflow.service.complete_step"
            ) as mock_complete:
                mock_complete.return_value = {"done": True, "status": "completed"}

                continue_waiting_step(run_id, optional_input=optional_input)

                # Verify complete_step was called with appended input
                mock_complete.assert_called_once()
                call_args = mock_complete.call_args
                result_passed = call_args[0][1]  # the result string

                expected = f"{stored_result}\n\n[CONTINUE INPUT]: {optional_input.strip()}"
                assert result_passed == expected, (
                    f"Expected result to contain appended input.\n"
                    f"Got: {result_passed!r}\n"
                    f"Expected: {expected!r}"
                )


class TestWaitForContinueRoundTripPersistence:
    """Property 27: wait_for_continue round-trip persistence.

    *For any* AutoWorkflowStep with `wait_for_continue` set to True, exporting
    the workflow via `export_workflow()` and re-importing via `import_workflow()`
    should preserve the `wait_for_continue=True` value.

    **Validates: Requirements 16.13**
    """

    @given(
        wait_for_continue=st.just(True),
        step_name=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    )
    @settings(max_examples=50, deadline=None)
    def test_wait_for_continue_round_trip(self, wait_for_continue, step_name):
        """export_workflow → import_workflow preserves wait_for_continue=True."""
        from distr.core.workflow.service import (
            create_workflow,
            add_step,
            update_step,
            export_workflow,
            import_workflow,
            get_workflow,
        )

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            # Create workflow with a step that has wait_for_continue=True
            wf_id = create_workflow(name="roundtrip-wf")
            step_id = add_step(wf_id, name=step_name, action_type="agent_instruction")
            assert step_id is not None
            assert update_step(step_id, wait_for_continue=True) is True

            # Export
            exported = export_workflow(wf_id)
            assert exported is not None
            assert len(exported["steps"]) == 1
            assert exported["steps"][0]["wait_for_continue"] is True

            # Import
            new_wf_id = import_workflow(exported)
            assert new_wf_id is not None

            # Verify the imported workflow preserves wait_for_continue
            new_wf = get_workflow(new_wf_id)
            assert new_wf is not None
            assert len(new_wf["steps"]) == 1
            assert new_wf["steps"][0]["wait_for_continue"] is True
