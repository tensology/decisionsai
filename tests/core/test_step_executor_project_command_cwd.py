from __future__ import annotations

import json

import distr.core.db.workflow  # noqa: F401
from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.workflow.step_executor import StepExecutorMixin
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def test_run_command_uses_run_project_folder_when_working_directory_missing(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _get_session():
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            session = factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        return ctx()

    monkeypatch.setattr("distr.core.workflow.step_executor.get_session", _get_session)

    session = factory()
    workflow = AutoWorkflow(name="Command cwd", description="")
    session.add(workflow)
    session.flush()
    run = AutoWorkflowRun(
        workflow_id=workflow.id,
        run_data=json.dumps({"project_folder": str(tmp_path)}),
    )
    session.add(run)
    session.commit()

    result = StepExecutorMixin()._run_command({"command": "pwd"}, run_id=run.id)

    assert result["passed"] is True
    assert result["output"].strip() == str(tmp_path)
