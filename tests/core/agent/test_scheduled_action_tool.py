import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
from distr.core.db.workflow import AutoWorkflow


def _session_ctx_factory():
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

    return session_ctx, factory


def test_scheduled_action_tool_is_registered_for_voice_routing():
    from distr.core.agent.tool_intents import forced_tool_names_for_text
    from distr.core.agent.tools.loader import _get_tool_definitions

    assert ("ScheduledActionTool", {}) in _get_tool_definitions()
    assert "scheduled_action" in forced_tool_names_for_text("list my scheduled actions")
    assert "scheduled_action" in forced_tool_names_for_text("cancel scheduled action 12")
    assert "scheduled_action" in forced_tool_names_for_text("reschedule action 12 for weekdays at 10:15")


def test_scheduled_action_tool_creates_lists_reschedules_and_cancels(monkeypatch):
    from distr.core.agent.tools.step_runner.workflow_tools import ScheduledActionTool

    session_ctx, factory = _session_ctx_factory()
    monkeypatch.setattr("distr.core.workflow.service.get_session", session_ctx)

    tool = ScheduledActionTool()
    created = tool._run(
        action="create",
        title="Open dashboard",
        schedule={"kind": "weekdays", "time": "08:30", "timezone": "Africa/Johannesburg"},
        desktop_action={"type": "open_app", "app_name": "Chrome"},
        target_context={"app_name": "Chrome"},
        safety={"bring_app_to_front": True},
    )

    assert "scheduled" in created.lower()
    assert "Open dashboard" in created
    assert "REFERENCE" in created

    with factory() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.workflow_type == "scheduled").one()
        workflow_id = wf.id
        assert wf.schedule_preset == "weekly"
        assert wf.schedule_days == "1,2,3,4,5"
        assert wf.schedule_time == "08:30"
        assert wf.schedule_timezone == "Africa/Johannesburg"
        assert wf.steps[0].action_type == "computer_use"
        assert "Open Chrome" in wf.steps[0].instruction

    listed = tool._run(action="list")
    assert "Open dashboard" in listed
    assert "weekdays" in listed.lower()

    rescheduled = tool._run(
        action="reschedule",
        workflow_id=workflow_id,
        schedule={"kind": "daily", "time": "10:15", "timezone": "Africa/Johannesburg"},
    )
    assert "rescheduled" in rescheduled.lower()

    with factory() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).one()
        assert wf.schedule_preset == "daily"
        assert wf.schedule_time == "10:15"

    cancelled = tool._run(action="cancel", workflow_id=workflow_id)
    assert "cancelled" in cancelled.lower()

    with factory() as db:
        assert db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first() is None


def test_scheduled_action_tool_can_cancel_by_title(monkeypatch):
    from distr.core.agent.tools.step_runner.workflow_tools import ScheduledActionTool

    session_ctx, factory = _session_ctx_factory()
    monkeypatch.setattr("distr.core.workflow.service.get_session", session_ctx)

    tool = ScheduledActionTool()
    tool._run(
        action="create",
        title="Press Enter",
        schedule={"kind": "daily", "time": "13:05"},
        desktop_action={"type": "keypress", "key": "Enter"},
    )
    tool._run(
        action="create",
        title="Open Chrome",
        schedule={"kind": "daily", "time": "08:30"},
        desktop_action={"type": "open_app", "app_name": "Chrome"},
    )

    cancelled = tool._run(action="cancel", title="Enter")

    assert "cancelled" in cancelled.lower()
    assert "Press Enter" in cancelled
    with factory() as db:
        remaining = db.query(AutoWorkflow).filter(AutoWorkflow.workflow_type == "scheduled").all()
        assert [wf.name for wf in remaining] == ["Open Chrome"]


def test_scheduled_action_tool_uses_last_created_action_when_id_is_omitted(monkeypatch):
    from distr.core.agent.tools.step_runner.workflow_tools import ScheduledActionTool

    session_ctx, factory = _session_ctx_factory()
    monkeypatch.setattr("distr.core.workflow.service.get_session", session_ctx)

    tool = ScheduledActionTool()
    tool._run(
        action="create",
        title="Press Enter",
        schedule={"kind": "daily", "time": "13:05"},
        desktop_action={"type": "keypress", "key": "Enter"},
    )

    disabled = tool._run(action="disable")

    assert "disabled" in disabled.lower()
    assert "Press Enter" in disabled
    with factory() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.workflow_type == "scheduled").one()
        assert wf.schedule_enabled is False
