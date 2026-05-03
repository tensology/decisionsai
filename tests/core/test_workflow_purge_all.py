"""Tests for purge_all_workflows()."""

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
)
from distr.core.workflow.service import purge_all_workflows


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


def _patch_service_session(factory):
    return patch(
        "distr.core.workflow.service.get_session",
        lambda: _session_ctx(factory),
    )


class TestPurgeAllWorkflows:
    def test_deletes_non_audit_keeps_audit_by_default(self):
        factory = _make_session_factory()
        session = factory()
        session.add(AutoWorkflow(name="junk", workflow_type="manual"))
        session.add(AutoWorkflow(name="audit trail", workflow_type="audit"))
        session.commit()
        session.close()

        with _patch_service_session(factory):
            removed = purge_all_workflows(include_audit=False)

        assert removed == 1
        session = factory()
        rows = session.query(AutoWorkflow).order_by(AutoWorkflow.id).all()
        assert len(rows) == 1
        assert rows[0].workflow_type == "audit"
        session.close()

    def test_include_audit_removes_everything(self):
        factory = _make_session_factory()
        session = factory()
        session.add(AutoWorkflow(name="a", workflow_type="manual"))
        session.add(AutoWorkflow(name="b", workflow_type="audit"))
        session.commit()
        session.close()

        with _patch_service_session(factory):
            removed = purge_all_workflows(include_audit=True)

        assert removed == 2
        session = factory()
        assert session.query(AutoWorkflow).count() == 0
        session.close()

    def test_null_workflow_type_is_deleted_when_not_audit(self):
        factory = _make_session_factory()
        session = factory()
        session.add(AutoWorkflow(name="legacy"))
        session.commit()
        wf = session.query(AutoWorkflow).first()
        wf.workflow_type = None
        session.commit()
        session.close()

        with _patch_service_session(factory):
            removed = purge_all_workflows(include_audit=False)

        assert removed == 1
        session = factory()
        assert session.query(AutoWorkflow).count() == 0
        session.close()

    def test_purge_deletes_workflow_with_step_results(self):
        factory = _make_session_factory()
        session = factory()
        wf = AutoWorkflow(name="with results", workflow_type="manual")
        session.add(wf)
        session.flush()
        step = AutoWorkflowStep(workflow_id=wf.id, position=0, name="S1")
        session.add(step)
        session.flush()
        run = AutoWorkflowRun(workflow_id=wf.id, status="completed")
        session.add(run)
        session.flush()
        session.add(
            AutoWorkflowStepResult(step_id=step.id, run_id=run.id, status="passed")
        )
        session.commit()
        session.close()

        with _patch_service_session(factory):
            removed = purge_all_workflows(include_audit=False)

        assert removed == 1
        session = factory()
        assert session.query(AutoWorkflow).count() == 0
        assert session.query(AutoWorkflowStepResult).count() == 0
        session.close()
