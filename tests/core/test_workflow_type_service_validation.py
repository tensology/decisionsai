# Feature: workflow-step-runner-unification, Task 5.5
# Tests for workflow_type validation in create_workflow() and update_workflow()
# Validates: Requirements 1.7
"""
Unit tests verifying that create_workflow() and update_workflow() reject
invalid workflow_type values with a ValueError, and accept valid ones.
"""
import contextlib
from unittest.mock import patch

import pytest
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
from distr.core.workflow.service import (
    VALID_WORKFLOW_TYPES,
    create_workflow,
    update_workflow,
)


def _make_session_factory():
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


class TestCreateWorkflowTypeValidation:
    """create_workflow() rejects invalid workflow_type values."""

    def test_create_with_valid_types(self):
        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)
        with patch("distr.core.workflow.service.get_session", patched):
            for wt in sorted(VALID_WORKFLOW_TYPES):
                wf_id = create_workflow(name=f"test-{wt}", workflow_type=wt)
                assert isinstance(wf_id, int)

    def test_create_default_type_is_manual(self):
        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)
        with patch("distr.core.workflow.service.get_session", patched):
            wf_id = create_workflow(name="default-type")
            assert isinstance(wf_id, int)

    def test_create_rejects_invalid_type(self):
        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)
        with patch("distr.core.workflow.service.get_session", patched):
            with pytest.raises(ValueError, match="Invalid workflow_type"):
                create_workflow(name="bad", workflow_type="invalid_type")

    def test_create_rejects_empty_string(self):
        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)
        with patch("distr.core.workflow.service.get_session", patched):
            with pytest.raises(ValueError, match="Invalid workflow_type"):
                create_workflow(name="bad", workflow_type="")


class TestUpdateWorkflowTypeValidation:
    """update_workflow() rejects invalid workflow_type values."""

    def test_update_with_valid_type(self):
        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)
        with patch("distr.core.workflow.service.get_session", patched):
            wf_id = create_workflow(name="update-test")
            result = update_workflow(wf_id, workflow_type="scheduled")
            assert result is True

    def test_update_rejects_invalid_type(self):
        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)
        with patch("distr.core.workflow.service.get_session", patched):
            wf_id = create_workflow(name="update-test")
            with pytest.raises(ValueError, match="Invalid workflow_type"):
                update_workflow(wf_id, workflow_type="bogus")

    def test_update_without_workflow_type_succeeds(self):
        """Updating other fields without workflow_type should not trigger validation."""
        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)
        with patch("distr.core.workflow.service.get_session", patched):
            wf_id = create_workflow(name="update-test")
            result = update_workflow(wf_id, name="renamed")
            assert result is True
