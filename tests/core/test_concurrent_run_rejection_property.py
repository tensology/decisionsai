# Feature: workflow-step-runner-unification, Property 13: Concurrent run rejection
"""
Property-based test verifying that when a workflow already has an active run
(status ``running`` or ``waiting``), attempting to start a second run is
rejected with an error.  Concurrent runs of *different* workflows are allowed.

**Validates: Requirements 4.9**
"""

import contextlib
from datetime import datetime
from unittest.mock import patch, MagicMock

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import (  # noqa: F401 — ensure models registered
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)
from distr.core.workflow.service import start_workflow_run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _create_workflow_with_step(factory):
    """Insert a workflow with one step and return the workflow id."""
    session = factory()
    wf = AutoWorkflow(name="Test Workflow", status="active", workflow_type="manual")
    session.add(wf)
    session.flush()
    step = AutoWorkflowStep(
        workflow_id=wf.id,
        position=0,
        name="Step 1",
        action_type="agent_instruction",
        instruction="Do something",
    )
    session.add(step)
    session.flush()
    wf_id = wf.id
    session.commit()
    session.close()
    return wf_id


def _create_active_run(factory, workflow_id, status):
    """Insert an active run for the given workflow with the given status."""
    session = factory()
    run = AutoWorkflowRun(
        workflow_id=workflow_id,
        status=status,
        started_at=datetime.utcnow(),
    )
    session.add(run)
    session.flush()
    run_id = run.id
    session.commit()
    session.close()
    return run_id


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_active_status_strategy = st.sampled_from(["running", "waiting"])


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


class TestConcurrentRunRejection:
    """Property 13: Concurrent run rejection."""

    @settings(max_examples=100, deadline=None)
    @given(active_status=_active_status_strategy)
    def test_second_run_rejected_when_active_run_exists(self, active_status):
        """**Validates: Requirements 4.9**

        For any workflow with an active run (status ``running`` or
        ``waiting``), attempting to start a second run SHALL be rejected
        with an error indicating a run is already in progress.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        wf_id = _create_workflow_with_step(factory)
        _create_active_run(factory, wf_id, active_status)

        with patch(
            "distr.core.workflow.service.get_session", patched_get_session
        ):
            result = start_workflow_run(wf_id)

        assert "error" in result, (
            f"Expected error for concurrent run with active status "
            f"'{active_status}', got: {result}"
        )
        assert "already in progress" in result["error"].lower(), (
            f"Error message should mention 'already in progress', "
            f"got: {result['error']}"
        )

        # Verify no new run was created
        session = factory()
        run_count = session.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == wf_id,
        ).count()
        session.close()
        # Should still be exactly 1 (the original active run)
        assert run_count == 1, (
            f"Expected exactly 1 run (the original), got {run_count}"
        )

    @settings(max_examples=100, deadline=None)
    @given(
        active_status=_active_status_strategy,
        num_other_workflows=st.integers(min_value=1, max_value=3),
    )
    def test_different_workflows_can_run_concurrently(
        self, active_status, num_other_workflows
    ):
        """**Validates: Requirements 4.9**

        Concurrent runs of *different* workflows SHALL be allowed.
        A workflow with an active run should not block runs of other
        workflows.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        # Create workflow A with an active run
        wf_a_id = _create_workflow_with_step(factory)
        _create_active_run(factory, wf_a_id, active_status)

        # Create other workflows (B, C, ...) without active runs
        other_wf_ids = []
        for _ in range(num_other_workflows):
            wf_id = _create_workflow_with_step(factory)
            other_wf_ids.append(wf_id)

        # Mock out WorkflowAgent and threading to avoid side effects
        mock_agent = MagicMock()
        mock_loop = MagicMock()
        mock_loop.run_forever = MagicMock()

        with patch(
            "distr.core.workflow.service.get_session", patched_get_session
        ), patch(
            "distr.core.workflow.service.WorkflowAgent", return_value=mock_agent
        ), patch(
            "distr.core.workflow.service.asyncio.new_event_loop",
            return_value=mock_loop,
        ), patch(
            "distr.core.workflow.service.threading.Thread",
            return_value=MagicMock(),
        ), patch(
            "distr.core.workflow.service._dispatch_step",
            return_value={"status": "running"},
        ), patch(
            "distr.core.workflow.service.os.environ", {}
        ):
            for other_id in other_wf_ids:
                result = start_workflow_run(other_id)
                assert "error" not in result, (
                    f"Workflow {other_id} should be allowed to run while "
                    f"workflow {wf_a_id} has an active run (status "
                    f"'{active_status}'), but got error: {result}"
                )
