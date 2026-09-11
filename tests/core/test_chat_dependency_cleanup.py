from __future__ import annotations

import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base, Chat, ChatTurnEvent
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowRun,
    DevelopmentCommand,
    DevelopmentPlanRevision,
    DevelopmentWorkItem,
    StudioArtifact,
)


def _factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=True)


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


def test_recycled_chat_id_does_not_inherit_development_state(monkeypatch):
    from distr.core.chat import ChatService

    factory = _factory()
    monkeypatch.setattr("distr.core.chat.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr(
        "distr.core.orchestrator.get_session", lambda: _session_ctx(factory)
    )

    with _session_ctx(factory) as session:
        old = Chat(title="Deleted thread", provider="ollama", model_name="old")
        session.add(old)
        session.flush()
        old_id = int(old.id)
        workflow = AutoWorkflow(name="Old task workflow", chat_id=old_id)
        session.add(workflow)
        session.flush()
        run = AutoWorkflowRun(
            workflow_id=workflow.id,
            chat_id=old_id,
            status="running",
        )
        session.add_all(
            [
                run,
                StudioArtifact(
                    chat_id=old_id,
                    workflow_id=workflow.id,
                    artifact_type="note",
                    title="Old artifact",
                ),
                DevelopmentPlanRevision(
                    chat_id=old_id,
                    workflow_id=workflow.id,
                    revision=1,
                    instruction="Old plan",
                ),
                DevelopmentCommand(chat_id=old_id, content="Old command"),
                DevelopmentWorkItem(
                    chat_id=old_id,
                    identity_key="old-thread",
                    workflow_id=workflow.id,
                    time_accumulated_seconds=7200,
                    time_paused=False,
                ),
                ChatTurnEvent(
                    chat_id=old_id,
                    turn_id=old_id,
                    sequence=1,
                    event_id="old-running-event",
                    event_type="status",
                    status="running",
                ),
            ]
        )
        session.flush()
        session.delete(old)

    chat_id, _ = ChatService.create_new_chat(
        llm_provider="ollama",
        llm_model="new",
        title="New thread",
    )

    assert chat_id == old_id
    with _session_ctx(factory) as session:
        assert session.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).count() == 0
        assert session.query(StudioArtifact).filter_by(chat_id=chat_id).count() == 0
        assert session.query(DevelopmentPlanRevision).filter_by(chat_id=chat_id).count() == 0
        assert session.query(DevelopmentCommand).filter_by(chat_id=chat_id).count() == 0
        assert session.query(AutoWorkflowRun).filter_by(chat_id=chat_id).count() == 0
        assert session.query(AutoWorkflow).filter_by(chat_id=chat_id).count() == 0
        assert session.query(ChatTurnEvent).filter_by(chat_id=chat_id).count() == 0
        current = session.get(Chat, chat_id)
        assert current.title == "New thread"
        assert current.model_name == "new"
