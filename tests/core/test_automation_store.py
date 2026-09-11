"""Tests for first-class automation storage (separate from workflows)."""

from __future__ import annotations

import json
from datetime import datetime

from distr.core.automation.store import (
    create_automation,
    delete_all_automations,
    delete_automation,
    is_automation_workflow,
    list_automations,
    list_due_automations,
    migrate_legacy_automation_workflows,
    public_id,
)
from distr.core.db import get_session
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, DevelopmentWorkItem
from distr.core.workflow.service import list_workflows


def _legacy_automation_workflow(name: str = "Daily plan") -> int:
    now = datetime.utcnow().replace(microsecond=0)
    with get_session() as session:
        workflow = AutoWorkflow(
            name=name,
            description="legacy automation",
            status="active",
            workflow_type="scheduled",
            schedule_enabled=True,
            schedule_preset="daily",
            schedule_time="09:00",
            schedule_days="1",
            next_run_at=now,
            context_rules=json.dumps(
                {
                    "decisions_surface": "automation",
                    "automation_type": "scheduled_instruction",
                    "preset_id": "daily_plan",
                    "schedule": {"kind": "daily", "time": "09:00"},
                }
            ),
            created_date=now,
            modified_date=now,
        )
        session.add(workflow)
        session.flush()
        session.add(
            AutoWorkflowStep(
                workflow_id=workflow.id,
                position=0,
                name="Automation Instruction",
                action_type="agent_instruction",
                step_type="agent_instruction",
                instruction="Plan my day",
            )
        )
        session.commit()
        return int(workflow.id)


def test_create_automation_uses_auto_id_not_workflow_row():
    automation = create_automation(
        name="Test automation",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Say hello",
        preset_id="",
        schedule={"kind": "daily", "time": "10:00"},
        action_config={},
    )
    assert automation["id"].startswith("auto_")
    assert automation["record_id"] is not None
    assert automation["workflow_id"] is None
    with get_session() as session:
        row = session.query(Automation).filter(Automation.id == automation["record_id"]).first()
        assert row is not None
        assert row.instruction == "Say hello"


def test_channel_intake_is_event_only_even_if_a_clock_schedule_is_present():
    automation = create_automation(
        name="WhatsApp intake",
        automation_type="channel_intake",
        status="active",
        instruction="Create a ticket from the incoming message.",
        preset_id="",
        schedule={"kind": "daily", "time": "10:00"},
        action_config={"source_config": {"source": "whatsapp"}},
    )
    with get_session() as session:
        row = session.get(Automation, automation["record_id"])
        assert row.schedule_enabled is False
        row.schedule_enabled = True
        row.next_run_at = datetime(2000, 1, 1)
        session.commit()

    assert all(item["id"] != automation["id"] for item in list_due_automations())


def test_one_time_schedule_round_trips_in_explicit_timezone():
    automation = create_automation(
        name="New York reminder",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Send the reminder.",
        preset_id="",
        schedule={
            "kind": "once",
            "run_at": "2030-01-15T09:00",
            "timezone": "America/New_York",
        },
        action_config={},
    )

    assert automation["schedule"]["run_at"] == "2030-01-15T09:00"
    assert automation["schedule"]["timezone"] == "America/New_York"
    assert automation["next_run_at"].startswith("2030-01-15T14:00:00")


def test_weekdays_alias_is_not_silently_saved_as_daily():
    automation = create_automation(
        name="Weekday alias",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Run on work days.",
        preset_id="",
        schedule={"kind": "weekdays", "time": "08:00"},
        action_config={},
    )

    assert automation["schedule"]["kind"] == "weekly"
    assert automation["schedule"]["days"] == "1,2,3,4,5"


def test_delete_automation_never_cascades_to_an_unowned_chat():
    from distr.core.db import Chat

    with get_session() as session:
        unrelated = Chat(title="Unrelated conversation", params="{}")
        session.add(unrelated)
        session.commit()
        unrelated_id = int(unrelated.id)

    automation = create_automation(
        name="Malicious binding",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Do work.",
        preset_id="",
        schedule={"kind": "daily", "time": "10:00"},
        action_config={"development_chat_id": unrelated_id},
        thread_chat_id=unrelated_id,
    )

    assert delete_automation(automation["id"]) is True
    with get_session() as session:
        assert session.get(Chat, unrelated_id) is not None


def test_delete_all_automations_removes_first_class_and_legacy_rows():
    first = create_automation(
        name="Bulk delete one",
        automation_type="scheduled_instruction",
        status="active",
        instruction="One",
        preset_id="",
        schedule={"kind": "daily", "time": "10:00"},
        action_config={},
    )
    second = create_automation(
        name="Bulk delete two",
        automation_type="scheduled_instruction",
        status="paused",
        instruction="Two",
        preset_id="",
        schedule={"kind": "weekly", "time": "11:00", "days": "1"},
        action_config={},
    )
    legacy_id = _legacy_automation_workflow("Bulk delete legacy")

    result = delete_all_automations()

    assert result["deleted"] >= 3
    assert result["automations"] >= 2
    assert result["legacy_workflows"] >= 1
    with get_session() as session:
        assert session.get(Automation, first["record_id"]) is None
        assert session.get(Automation, second["record_id"]) is None
        assert session.get(AutoWorkflow, legacy_id) is None
    assert list_automations() == []


def test_board_owned_automation_has_one_persistent_thread_without_a_ticket():
    from distr.core.automation_orchestrator import ensure_automation_thread
    from distr.core.db import Chat

    with get_session() as session:
        project = Project(name="Board project")
        workflow = AutoWorkflow(name="Independent workflow", workflow_type="manual", status="active")
        session.add_all([project, workflow])
        session.flush()
        board = KanbanBoard(name="Owned board", default_project_id=project.id)
        session.add(board)
        session.commit()
        board_id = int(board.id)
        project_id = int(project.id)
        workflow_id = int(workflow.id)

    automation = create_automation(
        name="Board check",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Check the board.",
        preset_id="",
        schedule={"kind": "daily", "time": "10:00"},
        action_config={},
        board_id=board_id,
        project_id=project_id,
        linked_workflow_id=workflow_id,
    )
    first_chat_id = ensure_automation_thread(automation)
    automation["thread_chat_id"] = first_chat_id
    assert ensure_automation_thread(automation) == first_chat_id

    with get_session() as session:
        item = session.query(DevelopmentWorkItem).filter_by(chat_id=first_chat_id).one()
        assert item.board_key == f"decisions:{board_id}"
        assert item.source_type == "automation"
        assert item.local_ticket_id is None
        assert item.workflow_id == workflow_id
        assert session.get(Chat, first_chat_id).project_id == project_id

    automation["action_config"] = {
        **automation.get("action_config", {}),
        "model_provider": "openai",
        "model": "gpt-test",
        "reasoning_effort": "high",
    }
    assert ensure_automation_thread(automation) == first_chat_id
    with get_session() as session:
        thread = session.get(Chat, first_chat_id)
        assert thread.provider == "openai"
        assert thread.model_name == "gpt-test"
        assert thread.route_mode == "manual"

    automation["action_config"].update({"model_provider": "", "model": ""})
    assert ensure_automation_thread(automation) == first_chat_id
    with get_session() as session:
        thread = session.get(Chat, first_chat_id)
        assert thread.provider is None
        assert thread.model_name is None
        assert thread.route_mode == "auto"

    assert delete_automation(automation["id"]) is True
    with get_session() as session:
        assert session.get(Chat, first_chat_id) is None
        assert session.get(AutoWorkflow, workflow_id) is not None


def test_legacy_automation_migrates_and_leaves_workflows_list():
    workflow_id = _legacy_automation_workflow("Daily plan migrate test")
    with get_session() as session:
        workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        assert workflow is not None
        assert is_automation_workflow(workflow)

    migrated = migrate_legacy_automation_workflows()
    assert migrated >= 1

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        assert wf is not None
        assert wf.schedule_enabled is False
        auto_row = session.query(Automation).filter(Automation.legacy_workflow_id == workflow_id).first()
        assert auto_row is not None
        auto_id = auto_row.id

    automations = [row for row in list_automations() if row.get("workflow_id") == workflow_id]
    assert len(automations) == 1
    assert automations[0]["id"] == public_id(auto_id)

    workflows = list_workflows(limit=50)
    assert all(w["id"] != workflow_id for w in workflows)


def test_list_workflows_excludes_automation_surface_rows():
    _legacy_automation_workflow("Still visible until migrated")
    before = list_workflows(limit=50)
    names = {w["name"] for w in before}
    assert "Still visible until migrated" not in names


def test_startup_reconciliation_closes_orphaned_automation_runs():
    from distr.core.automation.scheduler import reconcile_automation_startup_state

    automation = create_automation(
        name="Interrupted startup recovery",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Recover this run.",
        preset_id="",
        schedule={"kind": "daily", "time": "09:00"},
        action_config={},
    )
    record_id = int(automation["record_id"])
    with get_session() as session:
        run = AutomationRun(
            automation_id=record_id,
            status="running",
            run_data=json.dumps({"message": "working"}),
        )
        session.add(run)
        session.commit()
        run_id = int(run.id)

    result = reconcile_automation_startup_state()

    assert result["recovered_runs"] >= 1
    with get_session() as session:
        recovered = session.get(AutomationRun, run_id)
        assert recovered is not None
        assert recovered.status == "failed"
        assert recovered.completed_at is not None
        data = json.loads(recovered.run_data)
        assert data["recovered_on_startup"] is True
        assert "DecisionsAI stopped" in data["summary"]
