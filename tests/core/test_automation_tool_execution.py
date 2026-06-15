"""Tests for automation presets, tool execution, and engagement gates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_automation_presets_include_daily_plan():
    from distr.core.automation_presets import get_automation_preset, list_automation_presets

    presets = list_automation_presets()
    assert len(presets) == 10
    assert any(row["preset_id"] == "daily_plan" for row in presets)
    assert any(row["preset_id"] == "whatsapp_to_tickets" for row in presets)
    assert any(row["preset_id"] == "timesheet_export_25th" for row in presets)
    preset = get_automation_preset("daily_plan")
    assert preset is not None
    assert preset["action_config"]["tool"] == "proactive_orchestrator"
    assert preset["action_config"]["args"]["action"] == "daily_plan"
    whatsapp = get_automation_preset("whatsapp_to_tickets")
    assert whatsapp is not None
    assert whatsapp["automation_type"] == "scheduled_instruction"


def test_run_automation_tool_proactive_orchestrator(monkeypatch):
    from distr.core.automation_tool_runner import run_automation_tool

    monkeypatch.setattr(
        "distr.core.agent.tools.system.proactive_orchestrator.ProactiveOrchestratorTool._run",
        lambda self, **kwargs: {
            "success": True,
            "spoken_summary": "Here is your plan.",
            "markdown": "# Plan",
            "action": "daily_plan",
        },
    )
    result = run_automation_tool(
        "proactive_orchestrator",
        {"action": "daily_plan", "format": "summary"},
    )
    assert result["success"] is True
    assert "plan" in result["output"].lower()


def test_engagement_gate_blocks_daily_plan_opt_out():
    from distr.core.engagement_gates import proactive_delivery_blocked, record_daily_plan_opt_out

    record_daily_plan_opt_out(source="test")
    blocked, reason = proactive_delivery_blocked(
        delivery_kind="automation_tool",
        body="Daily plan",
        manual=False,
        preset_id="daily_plan",
    )
    assert blocked is True
    assert reason == "daily_plan_opt_out"
    blocked_manual, _ = proactive_delivery_blocked(
        delivery_kind="automation_tool",
        body="Daily plan",
        manual=True,
        preset_id="daily_plan",
    )
    assert blocked_manual is False


def test_tool_bound_automation_dispatches_directly(tmp_path, monkeypatch):
    import json

    from distr.core.automation_orchestrator import dispatch_automation_to_current_chat
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    db_path = tmp_path / "tool_auto.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from distr.core.db import init_db

    init_db()

    marker = {
        "decisions_surface": "automation",
        "automation_type": "tool_action",
        "preset_id": "daily_plan",
        "action_config": {
            "tool": "proactive_orchestrator",
            "args": {"action": "daily_plan"},
        },
        "schedule": {"kind": "daily", "time": "09:00"},
    }
    with get_session() as session:
        wf = AutoWorkflow(
            name="Daily plan",
            description="test",
            status="active",
            workflow_type="scheduled",
            context_rules=json.dumps(marker),
            schedule_enabled=True,
            schedule_preset="daily",
            schedule_time="09:00",
            created_date=datetime.utcnow(),
            modified_date=datetime.utcnow(),
        )
        session.add(wf)
        session.flush()
        session.add(
            AutoWorkflowStep(
                workflow_id=wf.id,
                position=0,
                name="Automation Instruction",
                action_type="agent_instruction",
                step_type="agent_instruction",
                instruction="Build today's plan.",
                config="{}",
            )
        )
        session.commit()
        workflow_id = wf.id

    emitted = []
    monkeypatch.setattr(
        "distr.core.automation_tool_runner.run_automation_tool",
        lambda tool, args: {
            "success": True,
            "output": "Plan ready.",
            "spoken_summary": "Plan ready.",
            "raw": {},
        },
    )
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.resolve_current_agent_chat_id",
        lambda settings=None: 42,
    )
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.emit_to_agent_chat",
        lambda *args, **kwargs: emitted.append((args, kwargs)),
    )

    from distr.core.automation_orchestrator import serialize_automation_workflow

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        automation = serialize_automation_workflow(wf)

    result = dispatch_automation_to_current_chat(automation, manual=True, speak=False)
    assert result["status"] == "completed"
    assert emitted
    assert "Plan ready" in emitted[0][0][1]


def test_proactive_orchestrator_uses_configured_daily_plan_automation(monkeypatch):
    from distr.core.agent.tools.system.proactive_orchestrator import ProactiveOrchestratorTool

    calls = []
    monkeypatch.setattr(
        "distr.core.automation_resolver.find_automation_for_daily_plan",
        lambda **kwargs: {
            "id": "wf_9",
            "name": "Daily plan",
            "workflow_id": 9,
            "action_config": {"tool": "proactive_orchestrator", "args": {"action": "daily_plan"}},
            "instruction": "Build plan",
        },
    )
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.dispatch_automation_to_current_chat",
        lambda automation, **kwargs: calls.append((automation, kwargs))
        or {"status": "completed", "summary": "Ran automation."},
    )

    tool = ProactiveOrchestratorTool()
    result = tool._resolve_daily_plan_result(format="summary")
    assert result["execution_mode"] == "automation_preset"
    assert calls


def test_legacy_planner_proactive_tasks_disabled_on_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'proactive.sqlite3'}")
    from distr.core.db import init_db
    from distr.core.db.proactive import ProactiveTask, ensure_system_proactive_tasks

    init_db()
    from distr.core.db import get_session

    with get_session() as session:
        ensure_system_proactive_tasks(session)
        names = {row.name: row.enabled for row in session.query(ProactiveTask).all()}
    assert names.get("Morning Brief") is False
    assert names.get("Day Planner") is False


def test_automations_presets_api():
    from distr.gui.web.routes.automations import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)
    resp = client.get("/api/automations/presets")
    assert resp.status_code == 200
    presets = resp.json()["presets"]
    assert any(row["preset_id"] == "daily_plan" for row in presets)


def test_create_daily_plan_preset_automation(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'create_preset.sqlite3'}")
    from distr.core.db import init_db

    init_db()
    from distr.gui.web.routes.automations import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)
    resp = client.post(
        "/api/automations",
        json={
            "name": "",
            "preset_id": "daily_plan",
            "schedule": {"kind": "daily", "time": "09:30"},
        },
    )
    assert resp.status_code == 200
    automation = resp.json()["automation"]
    assert automation["preset_id"] == "daily_plan"
    assert automation["action_config"]["tool"] == "proactive_orchestrator"
    assert automation["schedule"]["time"] == "09:30"
