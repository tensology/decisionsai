# Feature: workflow-step-runner-unification, Property 10: Default workflow list excludes audit type
"""
Property-based test verifying that the default workflow list (no type filter)
excludes ``audit`` type workflows, and that providing a ``workflow_type``
filter returns only workflows matching that type.

**Validates: Requirements 8.1**
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
from distr.core.workflow.service import list_workflows
from distr.core.workflow.service import update_workflow_order


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

    def test_default_list_orders_lifecycle_workflows_before_recency(self):
        """Lifecycle workflow tabs keep the human execution order."""
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        now = datetime.utcnow().replace(microsecond=0)
        rows = [
            AutoWorkflow(name="Deploy: Ship PR Until Green", workflow_type="manual", modified_date=now),
            AutoWorkflow(name="Polish: Verify and Ship", workflow_type="manual", modified_date=now + timedelta(minutes=1)),
            AutoWorkflow(name="Development: Ticket to Implementation", workflow_type="manual", modified_date=now + timedelta(minutes=2)),
            AutoWorkflow(name="Ideation: Brief to Board", workflow_type="manual", modified_date=now + timedelta(minutes=3)),
        ]
        session = factory()
        session.add_all(rows)
        session.commit()
        session.close()

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            result = list_workflows(limit=10)

        assert [row["name"].split(":", 1)[0] for row in result[:4]] == [
            "Ideation",
            "Development",
            "Polish",
            "Deploy",
        ]

    def test_saved_workflow_order_overrides_default_lifecycle_order(self):
        """Dragged workflow tabs persist their custom order."""
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        session = factory()
        rows = [
            AutoWorkflow(name="Ideation: Brief to Board", workflow_type="manual"),
            AutoWorkflow(name="Development: Ticket to Implementation", workflow_type="manual"),
            AutoWorkflow(name="Polish: Verify and Ship", workflow_type="manual"),
            AutoWorkflow(name="Deploy: Ship PR Until Green", workflow_type="manual"),
        ]
        session.add_all(rows)
        session.commit()
        ids = [int(row.id) for row in rows]
        session.close()

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            assert update_workflow_order([ids[3], ids[2], ids[1], ids[0]]) is True
            result = list_workflows(limit=10)

        assert [row["id"] for row in result[:4]] == [ids[3], ids[2], ids[1], ids[0]]

    def test_default_list_excludes_internal_execution_workflows(self):
        """Internal project execution ledgers are not user workflow definitions."""
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        session = factory()
        visible = AutoWorkflow(name="Real workflow", status="active", workflow_type="manual")
        project_cli = AutoWorkflow(name="[Project: App] run tests", status="completed", workflow_type="project_cli")
        pi_agent = AutoWorkflow(name="[Project: App] fix ticket", status="completed", workflow_type="pi_agent")
        session.add_all([visible, project_cli, pi_agent])
        session.commit()
        visible_id = visible.id
        project_cli_id = project_cli.id
        pi_agent_id = pi_agent.id
        session.close()

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            default_result = list_workflows()
            project_cli_result = list_workflows(workflow_type="project_cli")

        assert {row["id"] for row in default_result} == {visible_id}
        assert {row["id"] for row in project_cli_result} == {project_cli_id}
        assert pi_agent_id not in {row["id"] for row in default_result}

    def test_default_list_includes_review_and_deploy_workflows(self):
        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        session = factory()
        review = AutoWorkflow(name="Independent review", status="active", workflow_type="review")
        deploy = AutoWorkflow(name="Release deployment", status="active", workflow_type="deploy")
        session.add_all([review, deploy])
        session.commit()
        expected = {review.id, deploy.id}
        session.close()

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            result = list_workflows()

        assert {row["id"] for row in result} == expected

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
