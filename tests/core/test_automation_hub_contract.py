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
        "/api/kanban/agent/checkin",
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

    assert "automation-search" in html
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
    assert "md:grid-cols-4" in html
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

    def fake_start_workflow_run(workflow_id, context="", run_metadata=None, **kwargs):
        dispatched.append((workflow_id, context, run_metadata or {}))
        return {"run_id": 4040, "workflow_id": workflow_id, "status": "running"}

    monkeypatch.setattr("distr.core.workflow.dispatcher.start_workflow_run", fake_start_workflow_run)

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

    delete_resp = client.delete(f"/api/automations/{automation['id']}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["success"] is True

    with get_session() as session:
        assert session.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first() is None


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

    def fake_start_workflow_run(workflow_id, context="", run_metadata=None, **kwargs):
        dispatched.append((workflow_id, context, run_metadata or {}))
        return {"run_id": 909, "workflow_id": workflow_id, "status": "running"}

    monkeypatch.setattr("distr.core.workflow.dispatcher.start_workflow_run", fake_start_workflow_run)
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
    assert run["workflow_run_id"] == 909
    assert run["summary"] == "Automation instruction sent to the orchestrator."
    assert dispatched
    assert dispatched[0][1].startswith("[Multi-Action Intake]")
    assert "screen 3" in dispatched[0][1]
    assert dispatched[0][2]["source_type"] == "automation"
    assert dispatched[0][2]["is_workflow_attached"] is True
    assert [event["event_type"] for event in emitted_events] == ["run_started", "worker_dispatched"]
