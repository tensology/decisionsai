"""Tests for first-class automation storage (separate from workflows)."""

from __future__ import annotations

import json
from datetime import datetime

from distr.core.automation.store import (
    create_automation,
    is_automation_workflow,
    list_automations,
    migrate_legacy_automation_workflows,
    public_id,
)
from distr.core.db import get_session
from distr.core.db.automation import Automation
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
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
