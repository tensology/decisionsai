# Feature: workflow-step-runner-unification, Property 10: Default workflow list excludes audit type
"""
Property-based test verifying that the default workflow list (no type filter)
excludes ``audit`` type workflows, and that providing a ``workflow_type``
filter returns only workflows matching that type.

**Validates: Requirements 8.1**
"""

import contextlib
from datetime import datetime
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
from distr.core.workflow.service import list_workflows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKFLOW_TYPES = ["manual", "instruction", "scheduled", "audit"]


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

_workflow_record_strategy = st.fixed_dictionaries({
    "workflow_type": st.sampled_from(WORKFLOW_TYPES),
})

_workflow_set_strategy = st.lists(
    _workflow_record_strategy, min_size=1, max_size=15,
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


class TestDefaultListExcludesAudit:
    """Property 10: Default workflow list excludes audit type."""

    @settings(max_examples=100, deadline=None)
    @given(workflow_records=_workflow_set_strategy)
    def test_default_list_excludes_audit(self, workflow_records):
        """**Validates: Requirements 8.1**

        For any set of AutoWorkflow records with mixed workflow_type values,
        the default list query (no type filter) SHALL return only workflows
        where workflow_type is not 'audit'.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        # Insert workflows into the in-memory DB.
        session = factory()
        created = []
        for i, rec in enumerate(workflow_records):
            wf = AutoWorkflow(
                name=f"Workflow {i}",
                status="active",
                workflow_type=rec["workflow_type"],
            )
            session.add(wf)
            session.flush()
            created.append({"id": wf.id, "workflow_type": rec["workflow_type"]})
        session.commit()
        session.close()

        # Expected: all non-audit workflow IDs
        expected_ids = {
            c["id"] for c in created if c["workflow_type"] != "audit"
        }

        with patch(
            "distr.core.workflow.service.get_session", patched_get_session
        ):
            result = list_workflows()

        actual_ids = {r["id"] for r in result}
        assert actual_ids == expected_ids, (
            f"Default list should exclude audit workflows. "
            f"Expected {expected_ids}, got {actual_ids}. "
            f"Records: {created}"
        )

        # Also verify no audit workflows in results
        for r in result:
            wf_type = next(
                c["workflow_type"] for c in created if c["id"] == r["id"]
            )
            assert wf_type != "audit", (
                f"Audit workflow {r['id']} should not appear in default list"
            )

    @settings(max_examples=100, deadline=None)
    @given(workflow_records=_workflow_set_strategy)
    def test_type_filter_returns_only_audit(self, workflow_records):
        """**Validates: Requirements 8.1**

        When workflow_type='audit' filter is provided, the list SHALL return
        only workflows matching that type.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        session = factory()
        created = []
        for i, rec in enumerate(workflow_records):
            wf = AutoWorkflow(
                name=f"Workflow {i}",
                status="active",
                workflow_type=rec["workflow_type"],
            )
            session.add(wf)
            session.flush()
            created.append({"id": wf.id, "workflow_type": rec["workflow_type"]})
        session.commit()
        session.close()

        expected_ids = {
            c["id"] for c in created if c["workflow_type"] == "audit"
        }

        with patch(
            "distr.core.workflow.service.get_session", patched_get_session
        ):
            result = list_workflows(workflow_type="audit")

        actual_ids = {r["id"] for r in result}
        assert actual_ids == expected_ids, (
            f"Audit filter should return only audit workflows. "
            f"Expected {expected_ids}, got {actual_ids}. "
            f"Records: {created}"
        )

    @settings(max_examples=100, deadline=None)
    @given(workflow_records=_workflow_set_strategy)
    def test_type_filter_returns_only_manual(self, workflow_records):
        """**Validates: Requirements 8.1**

        When workflow_type='manual' filter is provided, the list SHALL return
        only workflows matching that type.
        """
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        session = factory()
        created = []
        for i, rec in enumerate(workflow_records):
            wf = AutoWorkflow(
                name=f"Workflow {i}",
                status="active",
                workflow_type=rec["workflow_type"],
            )
            session.add(wf)
            session.flush()
            created.append({"id": wf.id, "workflow_type": rec["workflow_type"]})
        session.commit()
        session.close()

        expected_ids = {
            c["id"] for c in created if c["workflow_type"] == "manual"
        }

        with patch(
            "distr.core.workflow.service.get_session", patched_get_session
        ):
            result = list_workflows(workflow_type="manual")

        actual_ids = {r["id"] for r in result}
        assert actual_ids == expected_ids, (
            f"Manual filter should return only manual workflows. "
            f"Expected {expected_ids}, got {actual_ids}. "
            f"Records: {created}"
        )
