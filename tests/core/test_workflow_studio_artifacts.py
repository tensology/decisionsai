from __future__ import annotations

import contextlib
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.workflow import studio_artifacts


def test_seed_studio_artifacts_creates_durable_planning_bundle(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(studio_artifacts, "get_session", get_session)
    created = studio_artifacts.seed_studio_artifacts(
        chat_id=17,
        workflow_id=44,
        execution_profile="code",
        prompt="Rebuild the workflow UI",
        steps=[{"id": 71, "name": "Inspect", "action_type": "agent_instruction"}],
    )

    assert [item["artifact_type"] for item in created] == [
        "brief",
        "interface",
        "architecture",
        "data_model",
        "tasks",
        "verification",
    ]
    task_artifact = next(item for item in created if item["artifact_type"] == "tasks")
    assert json.loads(task_artifact["content"])[0]["name"] == "Inspect"
    assert studio_artifacts.seed_studio_artifacts(
        chat_id=17,
        workflow_id=44,
        execution_profile="code",
        prompt="Duplicate request",
        steps=[],
    ) == created
