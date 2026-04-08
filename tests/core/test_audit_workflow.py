# Feature: workflow-step-runner-unification, Task 5.2
"""
Unit tests for get_or_create_audit_workflow() and append_audit_step().

**Validates: Requirements 7.1, 7.2, 7.3**
"""

import contextlib
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)
from distr.core.workflow.service import (
    get_or_create_audit_workflow,
    append_audit_step,
)


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
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _patch_get_session(factory):
    """Return a patcher that replaces get_session in the workflow service."""
    return patch(
        "distr.core.workflow.service.get_session",
        lambda: _session_ctx(factory),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetOrCreateAuditWorkflow:
    """Tests for get_or_create_audit_workflow()."""

    def test_creates_audit_workflow_when_none_exists(self):
        factory = _make_session_factory()
        with _patch_get_session(factory):
            wf_id = get_or_create_audit_workflow(chat_id=42)
        assert wf_id is not None

        session = factory()
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
        assert wf.workflow_type == "audit"
        assert wf.chat_id == 42
        assert wf.status == "active"
        session.close()

    def test_returns_existing_audit_workflow(self):
        factory = _make_session_factory()
        with _patch_get_session(factory):
            wf_id_1 = get_or_create_audit_workflow(chat_id=7)
            wf_id_2 = get_or_create_audit_workflow(chat_id=7)
        assert wf_id_1 == wf_id_2

    def test_different_chats_get_different_workflows(self):
        factory = _make_session_factory()
        with _patch_get_session(factory):
            wf_id_1 = get_or_create_audit_workflow(chat_id=1)
            wf_id_2 = get_or_create_audit_workflow(chat_id=2)
        assert wf_id_1 != wf_id_2


class TestAppendAuditStep:
    """Tests for append_audit_step()."""

    def test_appends_step_to_audit_workflow(self):
        factory = _make_session_factory()
        with _patch_get_session(factory):
            ok = append_audit_step(
                chat_id=10,
                tool_name="web_search",
                instruction="Search for cats",
                result="Found 5 results",
                status="completed",
            )
        assert ok is True

        session = factory()
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.chat_id == 10).first()
        assert wf is not None
        assert len(wf.steps) == 1
        step = wf.steps[0]
        assert step.tool_used == "web_search"
        assert step.name == "Web Search"
        assert step.instruction == "Search for cats"
        assert step.result == "Found 5 results"
        session.close()

    def test_truncates_instruction_to_500_chars(self):
        factory = _make_session_factory()
        long_instruction = "x" * 1000
        with _patch_get_session(factory):
            append_audit_step(
                chat_id=11,
                tool_name="tool",
                instruction=long_instruction,
                result="ok",
            )

        session = factory()
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.chat_id == 11).first()
        step = wf.steps[0]
        assert len(step.instruction) <= 500
        session.close()

    def test_truncates_result_to_2000_chars(self):
        factory = _make_session_factory()
        long_result = "r" * 5000
        with _patch_get_session(factory):
            append_audit_step(
                chat_id=12,
                tool_name="tool",
                instruction="do stuff",
                result=long_result,
            )

        session = factory()
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.chat_id == 12).first()
        step = wf.steps[0]
        # 2000 chars + "..." = 2003
        assert len(step.result) <= 2003
        assert step.result.endswith("...")
        session.close()

    def test_routing_path_stored_without_truncation(self):
        factory = _make_session_factory()
        long_path = "path/" * 2000  # 10000 chars
        with _patch_get_session(factory):
            append_audit_step(
                chat_id=13,
                tool_name="tool",
                instruction="do stuff",
                result="ok",
                routing_path=long_path,
            )

        session = factory()
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.chat_id == 13).first()
        step = wf.steps[0]
        assert step.routing_path == long_path
        session.close()

    def test_routing_path_not_embedded_in_instruction(self):
        """The new implementation stores routing_path in its own field,
        NOT embedded in the instruction field (unlike the old step_runner)."""
        factory = _make_session_factory()
        with _patch_get_session(factory):
            append_audit_step(
                chat_id=14,
                tool_name="tool",
                instruction="do stuff",
                result="ok",
                routing_path="some/routing/path",
            )

        session = factory()
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.chat_id == 14).first()
        step = wf.steps[0]
        # routing_path should NOT be in the instruction
        assert "some/routing/path" not in step.instruction
        # But it should be in the routing_path field
        assert step.routing_path == "some/routing/path"
        session.close()

    def test_increments_position_for_multiple_steps(self):
        factory = _make_session_factory()
        with _patch_get_session(factory):
            append_audit_step(chat_id=15, tool_name="t1", instruction="i1", result="r1")
            append_audit_step(chat_id=15, tool_name="t2", instruction="i2", result="r2")
            append_audit_step(chat_id=15, tool_name="t3", instruction="i3", result="r3")

        session = factory()
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.chat_id == 15).first()
        positions = [s.position for s in wf.steps]
        assert positions == [0, 1, 2]
        session.close()
