"""Exercise paired workflow review through the real desktop approval endpoints."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core import orchestrator_proactive as proactive
from distr.core import db as core_db
from distr.core.db.kanban import KanbanLane, KanbanTicket, KanbanTicketTodo
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
from distr.core.workflow import builtin, development_harness, dispatcher, draft_launch, post_execution, router as step_router
from distr.core.workflow.development_threads import bind_ticket_development_thread
from distr.gui.web.routes.development.threads import register_routes as register_thread_routes
from distr.gui.web.routes.development.workflows import register_routes
from tests.core.test_orchestrator_proactive import _factory, _seed_work_signal, _session_ctx

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path("/tmp/decisions-paired-workflow-review")


@pytest.mark.parametrize("selection", ["ticket", "builtin", "hidden"])
@pytest.mark.parametrize(("width", "delivered"), [(390, True), (768, False), (1280, True), (1440, False)])
def test_paired_approval_recovers_the_exact_workflow_without_automatic_retry(
    page, tmp_path, monkeypatch, width, delivered, selection,
):
    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    factory = _factory(tmp_path)
    ids = _seed_work_signal(factory, tmp_path)

    def provider():
        return _session_ctx(factory)

    for module in (proactive, development_harness, dispatcher, draft_launch, post_execution, step_router):
        monkeypatch.setattr(module, "get_session", provider)
    if selection == "builtin":
        monkeypatch.setattr(core_db, "get_session", provider)
    monkeypatch.setattr("distr.core.orchestrator.get_session", provider)
    monkeypatch.setattr(proactive, "_external_context", lambda: {})
    monkeypatch.setattr(proactive, "_resolve_route", lambda _: {"backend": "pi", "model": "fixture-model"})
    monkeypatch.setattr(dispatcher, "record_workflow_chat_event", lambda *a, **k: None)
    monkeypatch.setattr("distr.core.orchestration_events.emit_orchestration_event", lambda **_: None)
    workers = []

    class HeldTimer:
        def __init__(self, _delay, callback):
            self.callback = callback

        def start(self):
            workers.append(self.callback)

    monkeypatch.setattr(dispatcher, "threading", SimpleNamespace(
        Timer=HeldTimer, get_ident=dispatcher.threading.get_ident, Lock=dispatcher.threading.Lock,
    ))
    steps = ["Inspect the original proposal", "Repair the delivery labels", "Verify the result"]
    workflow_id = None
    with provider() as db:
        ticket = db.get(KanbanTicket, ids.ticket_id)
        if selection == "ticket":
            Path(db.get(Project, ids.project_id).folder_location).mkdir(exist_ok=True)
        ticket.title = "Delivery proposal"
        ticket.complexity = "complex"
        lane_id = ticket.lane_id
        db.get(KanbanLane, lane_id).name = "Backlog"
        if delivered or selection == "builtin":
            workflow = AutoWorkflow(name="Delivery review", status="active")
            db.add(workflow)
            db.flush()
            workflow_id = workflow.id
            if selection == "builtin":
                ticket.description = "Scan the project and map the current pages."
                workflow.workflow_input = json.dumps({"builtin_slug": builtin.EXISTING_PROJECT})
            else:
                ticket.linked_workflow_id = workflow_id
            for position, instruction in enumerate(steps):
                workflow.steps.append(AutoWorkflowStep(
                    position=position, name=instruction, instruction=instruction,
                    action_type="send_to_project_cli", step_type="send_to_project_cli",
                    linked_project_id=ids.project_id,
                ))
        else:
            for position, instruction in enumerate(steps):
                db.add(KanbanTicketTodo(ticket_id=ids.ticket_id, text=instruction, position=position))

    def scan_approval():
        candidate = proactive.run_proactive_check(board_id=ids.board_id)["candidates"][0]
        return {"approved": True, "candidate_id": candidate["candidate_id"],
                "revision": candidate["workflow_draft"]["checklist_revision"]}

    old_approval = scan_approval()
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    register_thread_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    review_path = f"/api/workflows/studio/boards/{ids.board_id}/tickets/{ids.ticket_id}/workflow-draft"
    assert client.get(review_path).json()["status"] == "awaiting_review"
    board_path = f"/api/tickets/boards/{ids.board_id}"
    ticket_path = f"/api/tickets/tickets/{ids.ticket_id}"
    ticket_row = {"id": ids.ticket_id, "title": "Delivery proposal", "lane_id": lane_id, "complexity": "complex",
                  "board_id": ids.board_id, "plan_sources": {"status": "changed", "changes": [
                      {"item_id": 12, "title": "Delivery requirements", "built_revision": 1, "current_revision": 2},
                  ]}}
    board = {"id": ids.board_id, "name": "Delivery board", "default_project_id": ids.project_id}
    requests, launches = [], []
    approval_conflict = True

    def relay(socket):
        def handle(raw):
            request = json.loads(raw)["body"]
            if request["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = request.get("command")
            reply = {"type": "remote_control_response", "command": command, "request_id": request.get("request_id")}
            if command != "api_relay":
                socket.send(json.dumps({**reply, "data": {"screens": []}}))
                return
            call = request["data"]
            method, path, body = call["method"], call["path"], call.get("body")
            requests.append((method, path, body))
            status, data = 200, {}
            if path == review_path or path == review_path + "/approve":
                first_approval = method == "POST" and not approval_conflict and launches == [old_approval]
                if method == "POST":
                    launches.append(body)
                if first_approval and not delivered:
                    socket.send(json.dumps({**reply, "error": "Approval request dropped"}))
                    return
                response = client.request(method, path, json=body)
                status, data = response.status_code, response.json()
                if first_approval:
                    assert status == 200
                    if width == 390:
                        socket.close()
                    else:
                        socket.send(json.dumps({**reply, "error": "Approval response dropped"}))
                    return
            elif path == "/api/tickets/boards":
                data = [board]
            elif path == board_path:
                data = {**board, "lanes": [{"id": lane_id, "name": "Backlog", "tickets": [ticket_row]}]}
            elif path == ticket_path:
                data = ticket_row
            elif path in ("/api/projects", "/api/workflows", "/api/chats"):
                data = []
            elif path.startswith("/api/chats/") and "surface=development" in path:
                receipt = client.get(review_path).json()
                assert path == f"/api/chats/{receipt['chat_id']}?surface=development"
                data = {"title": "Delivery proposal", "project_id": ids.project_id,
                        "messages": [{"role": "user", "content": "Approved delivery workflow."}]}
            elif path.startswith("/api/workflows/studio/tasks/") and path.endswith("/execution"):
                receipt = client.get(review_path).json()
                assert path == f"/api/workflows/studio/tasks/{receipt['chat_id']}/execution"
                response = client.get(path)
                status, data = response.status_code, response.json()
            socket.send(json.dumps({**reply, "data": {"status": status, "response": data}}))

        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_default_timeout(8000)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{url}?channel=paired-workflow-test")
    page.get_by_title("App View", exact=True).click()
    page.get_by_role("button", name="Ticket Boards", exact=True).click()
    page.get_by_text("Delivery board", exact=True).click()
    sources = page.get_by_label("Plan sources", exact=True)
    changed = ticket_row["plan_sources"]
    EVIDENCE.mkdir(exist_ok=True)
    for state, message in (
        ("current", "This ticket uses the current saved Plan revisions."),
        ("unavailable", "The saved Plan revisions could not be verified."),
    ):
        ticket_row["plan_sources"] = {"status": state, "changes": []}
        page.get_by_text("Delivery proposal", exact=True).click()
        expect(sources.get_by_role("status")).to_have_text(message)
        expect(sources.get_by_role("listitem")).to_have_count(0)
        page.screenshot(path=EVIDENCE / f"{width}-plan-sources-{state}.png", animations="disabled")
        page.get_by_role("button", name="Back", exact=True).click()
    ticket_row["plan_sources"] = changed
    page.get_by_text("Delivery proposal", exact=True).click()
    expect(sources.get_by_role("status")).to_have_text("Plan sources changed since this ticket was built.")
    expect(sources.get_by_role("listitem")).to_have_text("Delivery requirements: revision 1 to 2")
    sources.evaluate("element => element.scrollIntoView({block: 'center'})")
    page.screenshot(path=EVIDENCE / f"{width}-plan-sources.png", animations="disabled")
    page.get_by_text("Workflow review", exact=True).click()
    review = page.locator(".av-ticket-workflow-review")
    approve = review.get_by_role("button", name="Approve and launch", exact=True)
    expect(review.get_by_text("Awaiting review for", exact=False)).to_be_visible(timeout=2500)
    expect(review.get_by_role("listitem")).to_have_text(steps)
    if selection == "builtin":
        expect(review.get_by_text("The request asks to map an existing project into the plan.", exact=True)).to_be_visible()
        expect(review.get_by_text("Assigned as the board default.", exact=True)).to_have_count(0)
        review.evaluate("element => element.scrollIntoView({block: 'center'})")
        page.screenshot(path=EVIDENCE / f"{width}-builtin-recommendation.png", animations="disabled")
    expect(page.get_by_role("button", name="complex", exact=True)).to_have_attribute("aria-pressed", "true")
    assert not launches and not workers
    title = page.get_by_role("textbox").first
    title.fill("Unsaved proposal title")
    expect(review.get_by_text("Save ticket changes before approving the workflow.")).to_be_visible()
    expect(approve).to_have_count(0)
    title.fill(ticket_row["title"])
    EVIDENCE.mkdir(exist_ok=True)
    review.evaluate("element => element.scrollIntoView({block: 'center'})")
    page.screenshot(path=EVIDENCE / f"{width}-review.png", animations="disabled")

    revised_steps = steps
    linked_chat = None
    with provider() as db:
        if selection == "ticket":
            ticket = db.get(KanbanTicket, ids.ticket_id)
            linked_chat = bind_ticket_development_thread(
                db, ticket, project_id=ids.project_id, board_id=ids.board_id,
                lane_name="Backlog", approved_workflow_id=ticket.linked_workflow_id,
            ).id
        elif selection == "hidden":
            db.get(Project, ids.project_id).agent_ignored = True
        else:
            revised_steps = [*steps[:-1], "Verify the updated delivery receipt"]
            step = db.get(AutoWorkflow, workflow_id).steps[-1]
            step.name = step.instruction = revised_steps[-1]
    evidence_name = f"{width}-thread-link" if linked_chat is not None else str(width)
    if selection == "hidden":
        evidence_name = f"{width}-hidden-project"
    conflict = (proactive.PROJECT_HIDDEN_FROM_AGENT if selection == "hidden"
                else "This draft is out of date. Refresh it before approving.")
    approve.click()
    expect(review.get_by_role("alert")).to_have_text(conflict)
    expect(review.get_by_role("listitem")).to_have_count(0)
    expect(approve).to_have_count(0)
    assert launches == [old_approval] and not workers
    with provider() as db:
        assert db.query(AutoWorkflowRun).count() == 0
    review.evaluate("element => element.scrollIntoView({block: 'center'})")
    page.screenshot(path=EVIDENCE / f"{evidence_name}-approval-conflict.png", animations="disabled")
    refresh = review.get_by_role("button", name="Refresh workflow review", exact=True)
    refresh.click()
    expect(review.get_by_role("status")).to_have_text(
        proactive.PROJECT_HIDDEN_FROM_AGENT if selection == "hidden"
        else "This draft is out of date. Ask the orchestrator to scan this board again."
    )
    expect(approve).to_have_count(0)
    page.screenshot(path=EVIDENCE / f"{evidence_name}-awaiting-rescan.png", animations="disabled")
    if selection == "hidden":
        with provider() as db:
            db.get(Project, ids.project_id).agent_ignored = False
    approval = scan_approval()
    assert (approval["revision"] == old_approval["revision"]) == (selection == "hidden")
    if selection == "builtin":
        with provider() as db:
            event = db.get(OrchestratorEvent, approval["candidate_id"])
            payload = json.loads(event.payload)
            payload["candidate"]["workflow_draft"]["reason"] = "Assigned as the board default."
            event.payload = json.dumps(payload)
    approval_conflict = False
    if selection == "hidden":
        review.get_by_role("button", name="Refresh workflow status", exact=True).click()
    else:
        refresh.click()
    expect(review.get_by_role("listitem")).to_have_text(revised_steps)
    if selection == "builtin":
        expect(review.get_by_text("The request asks to map an existing project into the plan.", exact=True)).to_be_visible()
    expect(approve).to_be_visible()
    assert launches == [old_approval] and not workers
    review.evaluate("element => element.scrollIntoView({block: 'center'})")
    page.screenshot(path=EVIDENCE / f"{evidence_name}-replacement-review.png", animations="disabled")

    approve.click()
    expect(review.get_by_text("Launch could not be confirmed.", exact=False)).to_be_visible()
    expect(approve).to_have_count(0)
    assert launches == [old_approval, approval]
    page.screenshot(path=EVIDENCE / f"{width}-unconfirmed.png", animations="disabled")
    review.get_by_role("button", name="Check launch status").click()
    if not delivered:
        expect(approve).to_be_visible()
        assert launches == [old_approval, approval] and not workers
        approve.click()
    open_thread = review.get_by_role("button", name="Open Development thread")
    expect(open_thread).to_be_visible()
    assert launches == ([old_approval, approval] if delivered else [old_approval, approval, approval])
    assert len(workers) == 1
    with provider() as db:
        run = db.query(AutoWorkflowRun).one()
        assert (run.board_id, run.ticket_id) == (ids.board_id, ids.ticket_id)
        if linked_chat is not None:
            assert run.chat_id == linked_chat
    page.screenshot(path=EVIDENCE / f"{evidence_name}-launched.png", animations="disabled")
    with provider() as db:
        db.get(KanbanTicket, ids.ticket_id).source_chat_id = None
    review.get_by_role("button", name="Refresh workflow status").click()
    expect(review.get_by_role("status")).to_have_text("A workflow was approved, but its run could not be confirmed.")
    expect(open_thread).to_have_count(0)
    expect(approve).to_have_count(0)
    page.screenshot(path=EVIDENCE / f"{width}-receipt-scope-unconfirmed.png", animations="disabled")
    with provider() as db:
        db.get(KanbanTicket, ids.ticket_id).source_chat_id = run.chat_id
        saved_run = db.get(AutoWorkflowRun, run.id)
        run_data = json.loads(saved_run.run_data)
        run_data.pop("result_packet", None)
        saved_run.run_data = json.dumps(run_data)

    folder_changed = selection == "ticket"
    warning = (draft_launch.WORKFLOW_APPROVAL_FOLDER_CHANGED if folder_changed
               else draft_launch.WORKFLOW_APPROVAL_SCOPE_CHANGED)

    def change_scope_during_notification(*_args):
        with provider() as db:
            if folder_changed:
                checkout = Path(db.get(Project, ids.project_id).folder_location)
                checkout.rename(tmp_path / "original-checkout")
                checkout.mkdir()
            else:
                db.get(KanbanTicket, ids.ticket_id).source_chat_id = None

    monkeypatch.setattr(dispatcher, "build_step_preflight", lambda *_: {"ok": True, "summary": "Preflight passed"})
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_started",
                        change_scope_during_notification)
    execute = Mock(side_effect=AssertionError("Worker must not start after approved scope changes"))
    monkeypatch.setattr(dispatcher.StepDispatcher, "_execute", execute)
    result = dispatcher.StepDispatcher().run_in_workflow(run.current_step_id, run.id)
    assert result["error"] == warning
    execute.assert_not_called()
    late_output = "Status: needs_input\nSummary: A late request\nBlockers: Stopped worker"
    result_worker = dispatcher.StepDispatcher()
    result_worker._record_result_and_route(run.current_step_id, run.id, late_output, False)
    with provider() as db:
        assert db.query(AutoWorkflowStepResult).filter_by(run_id=run.id).count() == 0
        db.get(KanbanTicket, ids.ticket_id).source_chat_id = run.chat_id
    review.get_by_role("button", name="Check launch status").click()
    expect(open_thread).to_be_visible()
    expect(review.get_by_role("status")).to_have_text(warning)
    suffix = "folder" if folder_changed else "scope"
    page.screenshot(path=EVIDENCE / f"{width}-approval-{suffix}-stopped.png", animations="disabled")
    assert len(workers) == 1
    open_thread.click()
    expect(page.get_by_text("Approved delivery workflow.", exact=True)).to_be_visible()
    execution = page.get_by_role("region", name="Development execution")
    expect(execution.get_by_text("Failed", exact=True)).to_be_visible()
    expect(execution.get_by_text(warning, exact=True)).to_be_visible()
    assert any(method == "GET" and path == f"/api/chats/{run.chat_id}?surface=development" for method, path, _ in requests)
    page.screenshot(path=EVIDENCE / f"{width}-{'folder-' if folder_changed else ''}thread.png", animations="disabled")
    page.get_by_role("button", name="Back", exact=True).click()
    page.get_by_role("textbox").first.fill("Updated delivery title")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Delivery board", exact=True)).to_be_visible()
    updates = [body for method, path, body in requests if method == "PUT" and path == ticket_path]
    assert len(updates) == 1 and updates[0]["title"] == "Updated delivery title" and "lane_id" not in updates[0]
    assert not any(path == ticket_path + "/move" for _, path, _ in requests)
