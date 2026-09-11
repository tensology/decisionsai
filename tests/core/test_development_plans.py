from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.workflow import AutoWorkflow
from distr.core.workflow import development_plans


@pytest.fixture()
def plan_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'plans.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(development_plans, "get_session", get_session)
    with get_session() as db:
        chat = Chat(title="Plan revisions")
        workflow = AutoWorkflow(name="Reusable development workflow")
        db.add_all([chat, workflow])
        db.commit()
        return {"chat_id": chat.id, "workflow_id": workflow.id}


def test_new_plan_revision_supersedes_the_previous_active_version(plan_db):
    first = development_plans.create_plan_revision(
        chat_id=plan_db["chat_id"],
        workflow_id=plan_db["workflow_id"],
        instruction="Plan the change",
        workflow={"name": "Development", "steps": [{"id": 1, "name": "Inspect"}]},
        mode="plan",
        status="draft",
    )
    second = development_plans.create_plan_revision(
        chat_id=plan_db["chat_id"],
        workflow_id=plan_db["workflow_id"],
        instruction="Revise the plan with browser evidence",
        workflow={"name": "Development", "steps": [{"id": 2, "name": "Verify"}]},
        mode="plan",
        status="draft",
    )

    revisions = development_plans.list_plan_revisions(plan_db["chat_id"])
    assert [item["revision"] for item in revisions] == [2, 1]
    assert revisions[0]["id"] == second["id"]
    assert revisions[0]["status"] == "draft"
    assert revisions[1]["id"] == first["id"]
    assert revisions[1]["status"] == "superseded"
    assert revisions[0]["snapshot"]["steps"][0]["name"] == "Verify"


def test_plan_transition_enforces_review_before_execution(plan_db):
    plan = development_plans.create_plan_revision(
        chat_id=plan_db["chat_id"],
        workflow_id=plan_db["workflow_id"],
        instruction="Plan safely",
        workflow={"name": "Development", "steps": []},
        mode="plan",
        status="draft",
    )

    with pytest.raises(ValueError, match="cannot move"):
        development_plans.transition_plan_revision(plan["id"], "executing")
    approved = development_plans.transition_plan_revision(plan["id"], "approved")
    executing = development_plans.transition_plan_revision(plan["id"], "executing")
    assert approved["approved_at"] is not None
    assert executing["started_at"] is not None
