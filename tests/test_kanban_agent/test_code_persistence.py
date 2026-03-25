# Feature: kanban-agent-workflow, Property 16: Code field round-trip persistence
"""
Property 16: Code field round-trip persistence

*For any* non-empty code string saved to an AutoWorkflowStep's `code` field
via the update API, reloading the step should return the identical code string.

**Validates: Requirements 11.8**
"""
import contextlib
from unittest.mock import patch

from hypothesis import given, settings
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


class TestCodeFieldRoundTrip:
    """Property 16: Code field round-trip persistence."""

    @given(code=st.text(min_size=1, max_size=5000))
    @settings(max_examples=50, deadline=None)
    def test_code_round_trip(self, code):
        """Saving a code string via update_step and reloading returns the identical string."""
        from distr.core.workflow.service import (
            create_workflow,
            add_step,
            update_step,
            get_workflow,
        )

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            wf_id = create_workflow(name="test-wf")
            step_id = add_step(wf_id, name="code-step", action_type="execute_code")
            assert step_id is not None

            # Save code via update_step
            assert update_step(step_id, code=code) is True

            # Reload the workflow and find the step
            wf = get_workflow(wf_id)
            assert wf is not None
            steps = wf["steps"]
            assert len(steps) == 1
            assert steps[0]["code"] == code


# Feature: kanban-agent-workflow, Property 17: Schedule disable round-trip
class TestScheduleDisableRoundTrip:
    """Property 17: Schedule disable round-trip

    *For any* workflow with `schedule_enabled=true`, sending a PATCH with
    `schedule_enabled=false` and then reloading the workflow should show
    `schedule_enabled=false`.

    **Validates: Requirements 12.3**
    """

    @given(final_state=st.booleans())
    @settings(max_examples=50, deadline=None)
    def test_schedule_disable_round_trip(self, final_state):
        """Setting schedule_enabled from True to any boolean persists correctly."""
        from distr.core.workflow.service import (
            create_workflow,
            update_workflow,
            get_workflow,
        )

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            wf_id = create_workflow(name="sched-wf")

            # Start with schedule_enabled=True
            assert update_workflow(wf_id, schedule_enabled=True) is True
            wf = get_workflow(wf_id)
            assert wf is not None
            assert wf["schedule_enabled"] is True

            # PATCH to final_state (True→False or True→True)
            assert update_workflow(wf_id, schedule_enabled=final_state) is True

            # Reload and verify
            wf = get_workflow(wf_id)
            assert wf is not None
            assert wf["schedule_enabled"] is final_state
