# Feature: kanban-agent-workflow, Property 14: Step result history ordering
"""
Property 14: Step result history ordering

*For any* workflow step with N execution results, querying the result history
should return records ordered by `created_at` descending. The count should
equal min(N, limit).

**Validates: Requirements 9.2**
"""
import contextlib
from datetime import datetime, timedelta
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



# Strategy: generate a list of 1-30 unique timestamps and a limit between 1 and 40
_timestamps_st = st.lists(
    st.integers(min_value=0, max_value=100_000),
    min_size=1,
    max_size=30,
    unique=True,
)
_limit_st = st.integers(min_value=1, max_value=40)


class TestStepResultHistoryOrdering:
    """Property 14: Step result history ordering."""

    @given(offsets=_timestamps_st, limit=_limit_st)
    @settings(max_examples=50, deadline=None)
    def test_results_ordered_descending_and_count_matches(self, offsets, limit):
        """
        Adding N results with varying timestamps, then calling get_step_results
        should return them ordered by created_at descending, with count == min(N, limit).
        """
        from distr.core.workflow.service import (
            create_workflow,
            add_step,
            get_step_results,
        )

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            wf_id = create_workflow(name="history-wf")
            step_id = add_step(wf_id, name="history-step", action_type="agent_instruction")
            assert step_id is not None

            # Insert N result records with distinct created_at timestamps
            base_time = datetime(2025, 1, 1)
            session = factory()
            try:
                for offset_minutes in offsets:
                    result = AutoWorkflowStepResult(
                        step_id=step_id,
                        run_id=None,
                        agent_response=f"result-{offset_minutes}",
                        status="passed",
                        created_at=base_time + timedelta(minutes=offset_minutes),
                    )
                    session.add(result)
                session.commit()
            finally:
                session.close()

            # Query via the service function
            results = get_step_results(step_id, limit=limit)

            n = len(offsets)
            expected_count = min(n, limit)
            assert len(results) == expected_count

            # Verify descending order by created_at
            timestamps = [r["created_at"] for r in results]
            for i in range(len(timestamps) - 1):
                assert timestamps[i] >= timestamps[i + 1], (
                    f"Results not in descending order: {timestamps[i]} < {timestamps[i + 1]}"
                )
