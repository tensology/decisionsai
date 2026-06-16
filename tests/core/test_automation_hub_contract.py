from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]


def _automation_record_id(automation: dict) -> int:
    record_id = automation.get("record_id")
    if record_id is not None:
        return int(record_id)
    raw = str(automation.get("id") or "").replace("auto_", "").replace("wf_", "")
    return int(raw)


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
    assert "DecisionsListKeyboard" in js
    assert "deleteSelected" in js
    assert 'window.DecisionsAPI.confirm({' in js
    assert 'title: "Remove automation"' in js
    assert 'confirmLabel: "Remove"' in js
    assert "automation-presets-btn" in html
    assert "automation-preset-menu" in html
    assert "createAutomationFromPreset" in js
    assert "automation-preset-id" in html
    assert 'id="automation-preset"' not in html
    assert "Custom instruction" not in html
    assert "loadAutomationPresets" in js
    assert "/api/automations/presets" in js

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
    from distr.core.db.automation import Automation, AutomationRun
    from distr.gui.web.routes.automations import create_routes

    app = FastAPI()
    app.include_router(create_routes(), prefix="/api")
    client = TestClient(app)
    dispatched = []

    monkeypatch.setattr("distr.core.automation_orchestrator.resolve_current_agent_chat_id", lambda settings=None: 404)
    monkeypatch.setattr(
        "distr.core.automation_subagent.start_automation_subagent",
        lambda **kwargs: None,
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
    record_id = _automation_record_id(automation)
    assert str(automation["id"]).startswith("auto_")

    with get_session() as session:
        row = session.query(Automation).filter(Automation.id == record_id).first()
        assert row is not None
        assert row.schedule_enabled is True
        assert row.schedule_preset == "hourly"
        assert row.instruction == "Create a planning note."

        run = AutomationRun(
            automation_id=record_id,
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
    assert body["run"]["automation_id"] == automation["id"]
    assert body["run"]["status"] == "running"
    assert body["run"]["workflow_run_id"] or body["run"].get("automation_run_id")

    delete_resp = client.delete(f"/api/automations/{automation['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["success"] is True

    with get_session() as session:
        assert session.query(Automation).filter(Automation.id == record_id).first() is None


def test_automations_api_accepts_interval_seconds_schedule():
    from distr.core.db import get_session
    from distr.core.db.automation import Automation
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
    record_id = _automation_record_id(automation)
    assert automation["schedule"]["kind"] == "interval"
    assert automation["schedule"]["interval"] == 15
    assert automation["schedule"]["interval_unit"] == "seconds"
    assert automation["next_run_at"]

    with get_session() as session:
        row = session.query(Automation).filter(Automation.id == record_id).first()
        assert row is not None
        assert row.schedule_preset == "interval"
        assert row.schedule_time == "15:seconds"
        session.delete(row)
        session.commit()


def test_automations_api_normalizes_slash_time_on_save():
    from distr.core.db import get_session
    from distr.core.db.automation import Automation
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
    record_id = _automation_record_id(automation)
    assert automation["schedule"]["time"] == "09:20"
    assert automation["next_run_at"]

    with get_session() as session:
        row = session.query(Automation).filter(Automation.id == record_id).first()
        assert row is not None
        assert row.schedule_time == "09:20"
        assert row.next_run_at is not None
        session.delete(row)
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
    started = []

    def fail_if_workflow_agent_path_is_used(*args, **kwargs):
        raise AssertionError("automation instructions must dispatch to the live chat orchestrator")

    def capture_start(**kwargs):
        started.append(kwargs)
        return None

    monkeypatch.setattr("distr.core.workflow.dispatcher.start_workflow_run", fail_if_workflow_agent_path_is_used)
    monkeypatch.setattr("distr.core.automation_orchestrator.resolve_current_agent_chat_id", lambda settings=None: 77)
    monkeypatch.setattr(
        "distr.core.automation_subagent.start_automation_subagent",
        capture_start,
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
    assert run["status"] == "running"
    assert run["workflow_run_id"]
    assert run["summary"] == "Automation subagent started."
    assert started
    assert started[0]["chat_id"] == 77
    assert started[0]["automation"]["instruction"]
    assert [event["event_type"] for event in emitted_events] == ["run_started", "worker_dispatched"]


def test_scheduled_automation_dispatches_to_current_chat_not_workflow_agent(monkeypatch):
    import json
    from datetime import datetime, timedelta

    from distr.core.automation.scheduler import run_scheduled_automation
    from distr.core.automation.store import create_automation, get_automation
    from distr.core.db import get_session
    from distr.core.db.automation import Automation, AutomationRun

    dispatched = []

    def fail_if_workflow_agent_path_is_used(*args, **kwargs):
        raise AssertionError("scheduled automations must dispatch to the live chat orchestrator")

    monkeypatch.setattr("distr.core.workflow.service.start_workflow_run", fail_if_workflow_agent_path_is_used)
    monkeypatch.setattr("distr.core.automation_orchestrator.resolve_current_agent_chat_id", lambda settings=None: 88)
    monkeypatch.setattr(
        "distr.core.automation_subagent.start_automation_subagent",
        lambda **kwargs: dispatched.append(kwargs),
    )

    automation = create_automation(
        name="Daily Plan Automation",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Tell me my daily plan from tickets and messages.",
        preset_id="",
        schedule={"kind": "daily", "time": "09:00"},
        action_config={},
    )
    record_id = int(automation["record_id"])

    with get_session() as session:
        row = session.query(Automation).filter(Automation.id == record_id).first()
        assert row is not None
        row.next_run_at = datetime.utcnow() - timedelta(minutes=1)
        session.commit()

    due_automation = get_automation(automation["id"])
    assert due_automation is not None
    assert run_scheduled_automation(due_automation) is True
    assert dispatched
    assert dispatched[0]["chat_id"] == 88
    assert "Tell me my daily plan" in dispatched[0]["automation"]["instruction"]

    with get_session() as session:
        runs = session.query(AutomationRun).filter(AutomationRun.automation_id == record_id).all()
        assert len(runs) == 1
        assert runs[0].status == "running"
        data = json.loads(runs[0].run_data)
        assert data["execution_mode"] == "automation_subagent_instruction"
        assert data["phase"] == "scheduled_automation"
        row = session.query(Automation).filter(Automation.id == record_id).first()
        assert row is not None
        assert row.next_run_at is not None
        assert row.next_run_at > datetime.utcnow()

    dispatched.clear()
    assert run_scheduled_automation(due_automation) is False
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
        "distr.core.automation_subagent.start_automation_subagent",
        lambda **kwargs: dispatched.append(kwargs),
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
    assert run_resp.json()["run"]["status"] == "running"
