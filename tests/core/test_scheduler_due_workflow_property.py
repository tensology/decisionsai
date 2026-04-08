# Feature: workflow-step-runner-unification, Property 7: Scheduler due-workflow filtering
"""
Property-based test verifying that `get_due_scheduled_workflows()` returns
exactly those workflows where `schedule_enabled` is True, `schedule_preset`
is set (not None), AND `next_run_at` is at or before the current time — and
no others.

**Validates: Requirements 5.1**
"""

import contextlib
from datetime import datetime, timedelta
from unittest.mock import patch

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
from distr.core.workflow.scheduler import get_due_scheduled_workflows


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


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A reference "now" for the test — fixed so we can reason about before/after.
_REFERENCE_NOW = datetime(2025, 6, 15, 12, 0, 0)

# Strategy for a single workflow record's scheduling attributes.
_workflow_record_strategy = st.fixed_dictionaries({
    "schedule_enabled": st.booleans(),
    "schedule_preset": st.one_of(
        st.none(),
        st.sampled_from(["hourly", "daily", "weekly"]),
    ),
    "next_run_at": st.one_of(
        st.none(),
        # Past or exactly now (due)
        st.integers(min_value=1, max_value=720).map(
            lambda mins: _REFERENCE_NOW - timedelta(minutes=mins)
        ),
        # Exactly now
        st.just(_REFERENCE_NOW),
        # Future (not due)
        st.integers(min_value=1, max_value=720).map(
            lambda mins: _REFERENCE_NOW + timedelta(minutes=mins)
        ),
    ),
})

# Strategy for a list of 1–10 workflow records.
_workflow_set_strategy = st.lists(
    _workflow_record_strategy, min_size=1, max_size=10
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


class TestSchedulerDueWorkflowFiltering:
    """Property 7: Scheduler due-workflow filtering."""

    @settings(max_examples=100, deadline=None)
    @given(workflow_records=_workflow_set_strategy)
    def test_returns_exactly_due_workflows(self, workflow_records):
        """**Validates: Requirements 5.1**

        For any set of AutoWorkflow records with varying schedule_enabled,
        schedule_preset, and next_run_at values, get_due_scheduled_workflows()
        SHALL return exactly those workflows where schedule_enabled is True,
        schedule_preset is not None, AND next_run_at is at or before the
        current time — and no others.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        # Insert workflows into the in-memory DB.
        session = factory()
        created_ids = []
        for rec in workflow_records:
            wf = AutoWorkflow(
                name="Test Workflow",
                status="active",
                schedule_enabled=rec["schedule_enabled"],
                schedule_preset=rec["schedule_preset"],
                next_run_at=rec["next_run_at"],
            )
            session.add(wf)
            session.flush()
            created_ids.append(wf.id)
        session.commit()
        session.close()

        # Compute expected due IDs: enabled + preset set + next_run_at <= now.
        expected_due_ids = set()
        for wf_id, rec in zip(created_ids, workflow_records):
            if (
                rec["schedule_enabled"] is True
                and rec["schedule_preset"] is not None
                and rec["next_run_at"] is not None
                and rec["next_run_at"] <= _REFERENCE_NOW
            ):
                expected_due_ids.add(wf_id)

        # Patch get_session and freeze "now" to our reference time.
        with patch(
            "distr.core.workflow.scheduler.get_session", patched_get_session
        ), patch(
            "distr.core.workflow.scheduler.datetime"
        ) as mock_dt:
            mock_dt.utcnow.return_value = _REFERENCE_NOW
            # Ensure the mock still allows comparison operations by
            # keeping the real datetime class for everything else.
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            result = get_due_scheduled_workflows()

        actual_due_ids = {r["id"] for r in result}
        assert actual_due_ids == expected_due_ids, (
            f"Expected due IDs {expected_due_ids}, got {actual_due_ids}. "
            f"Records: {list(zip(created_ids, workflow_records))}"
        )
