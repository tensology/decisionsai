from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]


def test_ticket_board_no_longer_exposes_checkin_agent_controls():
    html = (ROOT / "distr/gui/web/templates/kanban/kanban.html").read_text(encoding="utf-8")
    board_js = (ROOT / "distr/gui/web/static/kanban/js/kanban_board.js").read_text(encoding="utf-8")
    kanban_js = (ROOT / "distr/gui/web/static/kanban/js/kanban.js").read_text(encoding="utf-8")

    forbidden = [
        "Check-in",
        "agent check-in",
        "whatsapp_checkin_enabled",
        "kb-board-modal-agent-enabled",
        "kb-board-modal-whatsapp-checkin-enabled",
        "kb-gs-agent-enabled",
        "kanban_agent_",
        "/api/tickets/agent/checkin",
        "/agent-enabled",
        "kb-agent-indicator",
    ]
    for needle in forbidden:
        assert needle not in html
        assert needle not in board_js
        assert needle not in kanban_js

    assert "Default Workflow" in html
    assert "Default Project" in html
    assert "WhatsApp" in html
    assert "default_workflow_id" in board_js
    assert "default_project_id" in board_js


def test_automation_hub_page_is_registered_in_navigation():
    base = (ROOT / "distr/gui/web/templates/base.html").read_text(encoding="utf-8")
    server = (ROOT / "distr/gui/web/server.py").read_text(encoding="utf-8")

    assert 'href="/automations/"' in base
    assert "Automations" in base
    assert '"/automations/static/js"' in server
    assert "create_automation_routes" in server


def test_automation_hub_is_plain_crud_scheduler_surface():
    html = (ROOT / "distr/gui/web/templates/automations/automations.html").read_text(encoding="utf-8")
    js = (ROOT / "distr/gui/web/static/automations/js/automations.js").read_text(encoding="utf-8")

    assert "automation-view-list" in html
    assert "automation-view-calendar" in html
    assert 'id="automation-calendar-panel"' in html
    assert html.index('id="automation-calendar-panel"') > html.index('id="automation-detail"')
    assert "setMainView" in js
    assert "renderMainWorkspace" in js
    assert "monthGridDayCount" in js
    assert "automation-cal-weekdays" in html
    assert "automation-cal-month-shell" in html
    assert "automation-cal-week-timegrid" in html
    assert 'id="automation-cal-mode-day"' in html
    assert "renderWeekCalendar" in js
    assert "renderDayCalendar" in js
    assert "weekTimedEventsForDay" in js
    assert "buildSouthAfricanHolidayMap" in js
    assert "is-holiday" in html
    blocks_js = (ROOT / "distr/gui/web/static/automations/js/calendar_blocks.js").read_text(encoding="utf-8")
    assert "AutomationCalendarBlocks" in blocks_js
    assert "setDateTimeField" in blocks_js
    assert "DecisionsDateTime.refreshInput" in blocks_js
    assert "persistBlockTimes" in blocks_js
    assert "reloadScheduleBlocks" in blocks_js
    assert "ensureGridInteractions" in blocks_js
    assert "sched-block-modal" in html
    assert "sched-block-context-menu" in html
    assert "sched-block-options-btn" in html
    assert "sched-block-action-naturalize" in html
    assert "uniformRowHeight" in blocks_js
    assert "openBlockContextMenu" in blocks_js
    assert "calendar_blocks.js" in html
    assert "automation-search" not in html
    assert "automation-empty" in html
    assert "automation-detail" in html
    assert "w-80 flex-shrink-0 flex flex-col gap-4" in html
    assert "bg-[#1a1f3a] rounded-lg border border-white/20" in html
    assert "Add Automation" in html
    assert "Instruction" in html
    assert "Schedule" in html
    assert "Run History" in html
    assert "automation-tab-details" in html
    assert "automation-tab-history" in html
    assert "automation-fields-row" in html
    assert "automation-status-switch" in html
    assert 'id="automation-status-switch"' in html
    assert 'select id="automation-status"' not in html
    assert "setAutomationStatus" in js
    assert "toggleAutomationStatus" in js
    assert "automation-schedule-detail" in html
    assert "automation-interval-value" in html
    assert "automation-schedule-kind" in html
    assert "automation-interval-unit" in html
    assert "Every N sec/min" in html
    assert "automation-once-at" in html
    assert 'id="automation-run-at"' not in html
    assert "deleteSelected" in js
    assert "setActiveTab" in js
    assert 'method: "DELETE"' in js
    assert "automationKeyboardTargetIsEditable" in js
    assert 'document.getElementById("decisions-confirm-modal")' in js
    assert 'e.key === "Enter"' in js
    assert 'e.key === "Delete" || e.key === "Backspace"' in js
    assert 'deleteSelected(active.getAttribute("data-id"))' in js
    assert 'window.DecisionsAPI.confirm({' in js
    assert 'title: "Remove automation"' in js
    assert 'confirmLabel: "Remove"' in js

    forbidden = [
        "WhatsApp",
        "Gmail",
        "Telegram approval",
        "Playwright evidence",
        "New WhatsApp/Gmail Intake",
        "intake sources",
        "workflow handoff",
        "run evidence",
        "source_config",
        "approval_policy",
        "notification_policy",
        "ticket_creation",
        "validation",
        "Project ID",
        "Workflow ID",
        "automation-project",
        "automation-workflow",
        "linked_project_id",
        "optional_workflow_id",
    ]
    for needle in forbidden:
        assert needle not in html
        assert needle not in js


def test_automations_api_create_list_and_run_smoke(monkeypatch):
    import json

    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.gui.web.routes.automations import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)
    dispatched = []

    monkeypatch.setattr("distr.core.automation_orchestrator.resolve_current_agent_chat_id", lambda settings=None: 404)
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.emit_to_agent_chat",
        lambda *args, **kwargs: dispatched.append((*args, kwargs)),
    )

    create_resp = client.post(
        "/api/automations",
        json={
            "name": "Hourly WhatsApp Intake",
            "automation_type": "scheduled_instruction",
            "instruction": "Create a planning note.",
            "schedule": {"kind": "hourly"},
        },
    )
    assert create_resp.status_code == 200
    automation = create_resp.json()["automation"]
    assert automation["id"]
    assert automation["status"] == "active"
    assert automation["instruction"] == "Create a planning note."
    assert automation["next_run_at"]
    workflow_id = int(str(automation["id"]).replace("wf_", ""))

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        assert wf is not None
        assert wf.workflow_type == "scheduled"
        assert wf.schedule_enabled is True
        assert wf.schedule_preset == "hourly"
        assert json.loads(wf.context_rules)["decisions_surface"] == "automation"
        step = session.query(AutoWorkflowStep).filter(AutoWorkflowStep.workflow_id == workflow_id).first()
        assert step is not None
        assert step.action_type == "agent_instruction"
        assert step.instruction == "Create a planning note."

        run = AutoWorkflowRun(
            workflow_id=workflow_id,
            status="completed",
            run_data=json.dumps({"source_type": "automation", "message": "ok"}),
        )
        session.add(run)
        session.commit()

    list_resp = client.get("/api/automations")
    assert list_resp.status_code == 200
    assert any(row["name"] == "Hourly WhatsApp Intake" for row in list_resp.json()["automations"])

    runs_resp = client.get(f"/api/automations/{automation['id']}/runs")
    assert runs_resp.status_code == 200
    assert runs_resp.json()["runs"][0]["status"] == "completed"

    run_resp = client.post(f"/api/automations/{automation['id']}/run")
    assert run_resp.status_code == 200
    body = run_resp.json()
    assert body["success"] is True
    assert body["run"]["workflow_id"] == workflow_id
    assert body["run"]["automation_id"] == automation["id"]
    assert dispatched
    assert dispatched[0][0] == 404
    assert "Create a planning note." in dispatched[0][1]

    delete_resp = client.delete(f"/api/automations/{automation['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["success"] is True

    with get_session() as session:
        assert session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first() is None


def test_automations_api_accepts_interval_seconds_schedule():
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow
    from distr.gui.web.routes.automations import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    create_resp = client.post(
        "/api/automations",
        json={
            "name": "Frequent Check",
            "instruction": "Ping the board.",
            "schedule": {"kind": "interval", "interval": 15, "interval_unit": "seconds"},
        },
    )

    assert create_resp.status_code == 200
    automation = create_resp.json()["automation"]
    workflow_id = int(str(automation["id"]).replace("wf_", ""))
    assert automation["schedule"]["kind"] == "interval"
    assert automation["schedule"]["interval"] == 15
    assert automation["schedule"]["interval_unit"] == "seconds"
    assert automation["next_run_at"]

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        assert wf is not None
        assert wf.schedule_preset == "interval"
        assert wf.schedule_time == "15:seconds"
        session.delete(wf)
        session.commit()


def test_automations_api_normalizes_slash_time_on_save():
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow
    from distr.gui.web.routes.automations import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    create_resp = client.post(
        "/api/automations",
        json={
            "name": "Morning Check",
            "instruction": "Check the board.",
            "schedule": {"kind": "daily", "time": "9/20"},
        },
    )

    assert create_resp.status_code == 200
    automation = create_resp.json()["automation"]
    workflow_id = int(str(automation["id"]).replace("wf_", ""))
    assert automation["schedule"]["time"] == "09:20"
    assert automation["next_run_at"]

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        assert wf is not None
        assert wf.schedule_time == "09:20"
        assert wf.next_run_at is not None
        session.delete(wf)
        session.commit()


def test_automations_api_rejects_invalid_schedule_time():
    from distr.gui.web.routes.automations import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    resp = client.post(
        "/api/automations",
        json={
            "name": "Bad Time",
            "instruction": "This should not save.",
            "schedule": {"kind": "daily", "time": "25/99"},
        },
    )

    assert resp.status_code == 422
    assert "Schedule time out of range" in resp.text


def test_run_now_dispatches_instruction_to_orchestrator(monkeypatch):
    import distr.gui.web.routes.automations as automations_routes

    emitted_events = []
    dispatched = []

    def fail_if_workflow_agent_path_is_used(*args, **kwargs):
        raise AssertionError("automation instructions must dispatch to the live chat orchestrator")

    monkeypatch.setattr("distr.core.workflow.dispatcher.start_workflow_run", fail_if_workflow_agent_path_is_used)
    monkeypatch.setattr("distr.core.automation_orchestrator.resolve_current_agent_chat_id", lambda settings=None: 77)
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.emit_to_agent_chat",
        lambda *args, **kwargs: dispatched.append((*args, kwargs)),
    )
    monkeypatch.setattr(
        automations_routes,
        "_emit_automation_event",
        lambda **kwargs: emitted_events.append(kwargs) or len(emitted_events),
    )

    app = FastAPI()
    app.include_router(automations_routes.create_routes(), prefix="/api")
    client = TestClient(app)

    create_resp = client.post(
        "/api/automations",
        json={
            "name": "Screen Compliment",
            "instruction": (
                'say "Hey babe, you are a sexy beautiful dude!"\n'
                "the move the mouse to the center of the screen on screen 1.\n"
                "the move the mouse to the center of the screen on screen 2.\n"
                "the move the mouse to the center of the screen on screen 3."
            ),
            "schedule": {"kind": "daily", "time": "09:00"},
        },
    )
    automation_id = create_resp.json()["automation"]["id"]

    run_resp = client.post(f"/api/automations/{automation_id}/run")

    assert run_resp.status_code == 200
    run = run_resp.json()["run"]
    assert run["status"] == "dispatched"
    assert run["workflow_run_id"]
    assert run["summary"] == "Automation instruction sent to the orchestrator."
    assert dispatched
    assert dispatched[0][0] == 77
    assert dispatched[0][1].startswith("[Multi-Action Intake]")
    assert "screen 3" in dispatched[0][1]
    assert dispatched[0][2] is True
    assert dispatched[0][3].get("skip_user_persist") is True
    assert [event["event_type"] for event in emitted_events] == ["run_started", "worker_dispatched"]


def test_scheduled_automation_dispatches_to_current_chat_not_workflow_agent(monkeypatch):
    import json
    from datetime import datetime, timedelta

    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow.scheduler import run_scheduled_workflow
    from distr.gui.web.routes.automations import _automation_marker

    dispatched = []

    def fail_if_workflow_agent_path_is_used(*args, **kwargs):
        raise AssertionError("scheduled automations must dispatch to the live chat orchestrator")

    monkeypatch.setattr("distr.core.workflow.service.start_workflow_run", fail_if_workflow_agent_path_is_used)
    monkeypatch.setattr("distr.core.automation_orchestrator.resolve_current_agent_chat_id", lambda settings=None: 88)
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.emit_to_agent_chat",
        lambda *args, **kwargs: dispatched.append((*args, kwargs)),
    )

    with get_session() as session:
        workflow = AutoWorkflow(
            name="Daily Plan Automation",
            status="active",
            workflow_type="scheduled",
            context_rules=_automation_marker({"kind": "daily", "time": "09:00"}),
            schedule_enabled=True,
            schedule_preset="daily",
            schedule_time="09:00",
            next_run_at=datetime.utcnow() - timedelta(minutes=1),
        )
        session.add(workflow)
        session.flush()
        session.add(AutoWorkflowStep(
            workflow_id=workflow.id,
            position=0,
            name="Automation Instruction",
            action_type="agent_instruction",
            step_type="agent_instruction",
            instruction="Tell me my daily plan from tickets and messages.",
            config=json.dumps({"source": "automation"}),
        ))
        workflow_id = workflow.id
        session.commit()

    assert run_scheduled_workflow(workflow_id) is True
    assert dispatched
    assert dispatched[0][0] == 88
    assert "Tell me my daily plan" in dispatched[0][1]

    with get_session() as session:
        runs = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.workflow_id == workflow_id).all()
        assert len(runs) == 1
        assert runs[0].status == "dispatched"
        data = json.loads(runs[0].run_data)
        assert data["execution_mode"] == "agent_chat_orchestrator"
        assert data["phase"] == "scheduled_automation"
        workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        assert workflow is not None
        assert workflow.next_run_at is not None
        assert workflow.next_run_at > datetime.utcnow()

    dispatched.clear()
    assert run_scheduled_workflow(workflow_id) is False
    assert not dispatched


def test_automation_dispatch_records_chat_action_card(monkeypatch):
    import json

    from distr.core.db import Chat, get_session
    from distr.gui.web.routes.automations import create_routes

    with get_session() as session:
        chat = Chat(title="Agent chat")
        session.add(chat)
        session.commit()
        chat_id = chat.id

    dispatched = []
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.resolve_current_agent_chat_id",
        lambda settings=None: chat_id,
    )
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.emit_to_agent_chat",
        lambda *args, **kwargs: dispatched.append((*args, kwargs)),
    )

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)

    create_resp = client.post(
        "/api/automations",
        json={
            "name": "Inbox Sweep",
            "instruction": "Summarize unread messages.",
            "schedule": {"kind": "hourly"},
        },
    )
    automation_id = create_resp.json()["automation"]["id"]

    run_resp = client.post(f"/api/automations/{automation_id}/run")
    assert run_resp.status_code == 200
    assert dispatched
    assert dispatched[0][3].get("skip_user_persist") is True
    assert "DecisionsAI automation run" in dispatched[0][1]

    with get_session() as session:
        chat = session.get(Chat, chat_id)
        params = json.loads(chat.params) if chat.params else {}

    events = params.get("workflow_events") or []
    assert events == []
