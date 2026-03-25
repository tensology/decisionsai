# Feature: kanban-agent-workflow, Property 28: Seed data idempotency
"""
Property 28: Seed data idempotency

*For any* workflow that already has steps, running the seed data script should
not modify the existing steps or create duplicates. *For any* workflow with
zero steps, running the seed script should populate it with the expected
number of steps.

**Validates: Requirements 17.1, 17.10**
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
from distr.core.db.seed_workflows import seed_workflows, WORKFLOW_SEEDS


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


class TestSeedDataIdempotency:
    """Property 28: Seed data idempotency."""

    @given(
        workflow_name=st.sampled_from(list(WORKFLOW_SEEDS.keys())),
    )
    @settings(max_examples=50, deadline=None)
    def test_seed_populates_empty_workflow_then_idempotent(self, workflow_name):
        """
        For a workflow with zero steps, seed populates it.
        Running seed again does not change the step count.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.db.seed_workflows.get_session", patched_get_session):
            # Create an empty workflow with the given name
            session = factory()
            wf = AutoWorkflow(name=workflow_name, status="draft")
            session.add(wf)
            session.commit()
            wf_id = wf.id
            session.close()

            # First seed — should populate
            seed_workflows()

            # Check steps were created
            session = factory()
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
            step_count_after_first = len(wf.steps)
            expected_count = len(WORKFLOW_SEEDS[workflow_name])
            assert step_count_after_first == expected_count, (
                f"Expected {expected_count} steps for '{workflow_name}', got {step_count_after_first}"
            )
            # Record step IDs and names for comparison
            first_step_ids = sorted([s.id for s in wf.steps])
            first_step_names = sorted([s.name for s in wf.steps])
            session.close()

            # Second seed — should be idempotent
            seed_workflows()

            session = factory()
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
            step_count_after_second = len(wf.steps)
            second_step_ids = sorted([s.id for s in wf.steps])
            second_step_names = sorted([s.name for s in wf.steps])
            session.close()

            assert step_count_after_second == step_count_after_first, (
                f"Step count changed from {step_count_after_first} to {step_count_after_second} on second seed"
            )
            assert first_step_ids == second_step_ids, "Step IDs changed on second seed"
            assert first_step_names == second_step_names, "Step names changed on second seed"

    @given(
        workflow_name=st.sampled_from(list(WORKFLOW_SEEDS.keys())),
        existing_step_name=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, deadline=None)
    def test_seed_skips_workflow_with_existing_steps(self, workflow_name, existing_step_name):
        """
        For a workflow that already has steps, seed should not modify them.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.db.seed_workflows.get_session", patched_get_session):
            # Create a workflow with a pre-existing step
            session = factory()
            wf = AutoWorkflow(name=workflow_name, status="draft")
            session.add(wf)
            session.flush()
            existing_step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=0,
                name=existing_step_name,
                action_type="agent_instruction",
            )
            session.add(existing_step)
            session.commit()
            wf_id = wf.id
            existing_step_id = existing_step.id
            session.close()

            # Run seed — should skip this workflow
            seed_workflows()

            session = factory()
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
            assert len(wf.steps) == 1, (
                f"Expected 1 step (pre-existing), got {len(wf.steps)}"
            )
            assert wf.steps[0].id == existing_step_id
            assert wf.steps[0].name == existing_step_name
            session.close()
