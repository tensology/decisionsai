"""Regression test for deleting workflow steps with result history."""
import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, AutoWorkflowStepResult
from distr.core.workflow import service as workflow_service


def test_delete_step_removes_dependent_results_without_nulling_step_id():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def session_ctx():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with session_ctx() as db:
        wf = AutoWorkflow(name="wf")
        db.add(wf)
        db.flush()
        step = AutoWorkflowStep(workflow_id=wf.id, position=0, name="s1")
        db.add(step)
        db.flush()
        db.add(AutoWorkflowStepResult(step_id=step.id, status="passed", agent_response="ok"))
        step_id = step.id

    original_get_session = workflow_service.get_session
    workflow_service.get_session = session_ctx
    try:
        assert workflow_service.delete_step(step_id) is True
    finally:
        workflow_service.get_session = original_get_session

    with session_ctx() as db:
        assert db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).count() == 0
        assert db.query(AutoWorkflowStepResult).filter(AutoWorkflowStepResult.step_id == step_id).count() == 0
