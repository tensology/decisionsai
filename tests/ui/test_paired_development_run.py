"""Drive exact Development Stop through App View and the native HTTP boundary."""
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.workflow import development_harness
from distr.core.workflow.reply_delivery import execute_approved_workflow_reply
from distr.gui.web.routes.development.threads import register_routes
from tests.core.test_development_harness import scoped_direct_ticket  # noqa: F401
from tests.core.test_workflow_reply_review import decide, request_review, review
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
EXECUTION = "/api/workflows/studio/tasks/17/execution"
EVIDENCE = Path("/tmp/decisions-paired-development-run")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
@pytest.mark.parametrize("surface", ["desktop", "paired"])
def test_completed_workflow_shows_only_its_unresolved_queue_hold(
    page, review, fixture_base, monkeypatch, tmp_path, width, surface,
):
    from distr.core import chat_turns, orchestrator
    from distr.core.db import Chat, ChatTurnEvent
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher
    from distr.core.workflow.dispatcher import _record_ticket_queue_hold
    from distr.core.workflow.ticket_dispatch import ticket_group_scope_error
    from distr.gui.web.routes.development import workflow_execution
    from tests.ui.test_development_harness_playwright import _install_api

    app_view = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if surface == "paired" and not app_view:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    _, factory, details, _ = review
    run_id = details["workflow"]["run_id"]
    turn_id = details["workflow"]["turn_id"]
    ticket = details["ticket_id"]
    workflow_id, board_id = 44, 1
    with factory.begin() as db:
        db.add(AutoWorkflow(id=workflow_id, name="Delivery workflow"))
        db.get(Chat, 17).params = json.dumps({"development": {"ticket_id": ticket, "workflow_id": workflow_id}})
        run = db.get(AutoWorkflowRun, run_id)
        run.workflow_id, run.board_id = workflow_id, board_id
        original_lane = db.get(KanbanTicket, ticket).lane_id
        other_board = KanbanBoard(name="Another board")
        db.add(other_board)
        db.flush()
        other_lane = KanbanLane(name="Ready", board_id=other_board.id)
        db.add(other_lane)
        db.flush()
        other_board_id, other_lane_id = other_board.id, other_lane.id
        finished_ticket = KanbanTicket(id=42, title="Queued delivery", lane_id=original_lane,
                                       linked_project_id=9, workflow_status="completed")
        db.add_all([
            finished_ticket,
            KanbanTicket(id=43, title="Cross-project fixture", lane_id=original_lane, linked_project_id=10),
            KanbanTicket(id=44, title="Cross-board fixture", lane_id=other_lane.id, linked_project_id=9),
        ])
        db.flush()
        reason = ticket_group_scope_error(finished_ticket, workflow_id,
            {"ticket_id": 42, "board_id": board_id, "project_id": 9}, automatic=True)
        assert reason == "This ticket has already finished or been cancelled."
    monkeypatch.setattr(development_harness, "get_session", factory)
    monkeypatch.setattr(orchestrator, "get_session", factory)
    monkeypatch.setattr(orchestrator, "ensure_orchestrator_tables", lambda: None)
    monkeypatch.setattr(orchestrator, "is_orchestrator_enabled", lambda: True)
    monkeypatch.setattr("distr.core.initiative.outcome_flow._data_dir", lambda: tmp_path)
    _record_ticket_queue_hold(workflow_id, run_id, 42, reason, board_id=board_id, project_id=9)
    _record_ticket_queue_hold(workflow_id, run_id, 43, "Another project's reason.", board_id=board_id, project_id=10)
    _record_ticket_queue_hold(workflow_id, run_id, 44, "Another board's reason.", board_id=other_board_id, project_id=9)

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    workflow_execution.register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    state = client.get(EXECUTION).json()
    assert state["status"] == "completed"
    assert state.get("queue_handoff_hold") == {"ticket_id": 42, "reason": reason}
    assert "Another project's reason" not in json.dumps(state)
    assert "Another board's reason" not in json.dumps(state)
    with factory.begin() as db:
        db.get(KanbanTicket, ticket).lane_id = other_lane_id
    assert "queue_handoff_hold" not in client.get(EXECUTION).json()
    with factory.begin() as db:
        db.get(KanbanTicket, ticket).lane_id = original_lane
        root = db.get(Chat, 17)
        follow_up = Chat(parent_id=root.id, input="Keep the newer turn open.")
        db.add(follow_up)
        db.flush()
        newer_id = follow_up.id
        root.params = json.dumps({**json.loads(root.params), "active_turn_chat_row_id": newer_id})
        run = db.get(AutoWorkflowRun, run_id)
        run.run_data = json.dumps({
            "project_id": 9, "development_turn_id": turn_id,
            "terminal_receipt": dispatcher.build_workflow_run_receipt(
                run_id=run_id, workflow_id=workflow_id, status="completed", project_id=9, steps_summary=[]),
            "terminal_turn_pending": {"status": "completed", "chat_id": 17, "project_id": 9, "turn_id": turn_id},
        })
    monkeypatch.setattr(dispatcher, "get_session", factory)
    monkeypatch.setattr(chat_turns, "get_session", factory)
    monkeypatch.setattr(chat_turns, "_broadcast", lambda _: None)
    monkeypatch.setattr("distr.core.chat.record_chat_audit_event", lambda **_: None)
    with monkeypatch.context() as patch:
        patch.setattr("distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge._generate_report",
                      lambda _: "The first ticket is complete.")
        dispatcher._auto_resume_interrupted_runs(background=False)
        dispatcher._auto_resume_interrupted_runs(background=False)
    with factory() as db:
        recovered = db.query(Chat).filter_by(parent_id=17, response="The first ticket is complete.").one()
        recovered_id, recovered_text = recovered.id, recovered.response
        assert db.query(ChatTurnEvent).filter_by(chat_id=17, turn_id=turn_id, status="completed").count() == 1
        assert json.loads(db.get(Chat, 17).params)["active_turn_chat_row_id"] == newer_id
    chat = {"id": 17, "title": "Delivery proposal", "project_id": 9, "development": True,
            "development_workflow_id": workflow_id, "messages": [
                {"role": "assistant", "content": recovered_text, "chat_row_id": recovered_id},
            ]}
    if surface == "desktop":
        _install_api(page, [])
        page.route_web_socket("**/api/ws?*", lambda socket: socket.on_message(lambda _: None))
        page.route("**/api/chats/17?surface=development", lambda route: route.fulfill(json=chat))
        page.route(f"**{EXECUTION}", lambda route: route.fulfill(json=client.get(EXECUTION).json()))
        url = f"{fixture_base}/development/threads/17/"
    else:
        def relay(socket):
            def handle(raw):
                frame = json.loads(raw)["body"]
                if frame["type"] == "ping":
                    socket.send(json.dumps({"type": "pong"}))
                    return
                command = frame["command"]
                data = {"screens": []}
                if command == "api_relay":
                    request = frame["data"]
                    assert request["method"] == "GET"
                    path = request["path"]
                    result = {}
                    if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                        result = {"status": "thread", "chat_id": 17}
                    elif path == "/api/chats/17?surface=development":
                        result = chat
                    elif path == EXECUTION:
                        result = client.get(path).json()
                    data = {"status": 200, "response": result}
                socket.send(json.dumps({"type": "remote_control_response", "command": command,
                    "request_id": frame["request_id"], "data": data}))
            socket.on_message(handle)

        page.route_web_socket("**/ws/telegram?*", relay)
        url = f"{app_view}?channel=paired-test&proposal_key={'a' * 64}"
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(url)
    notice = page.get_by_role("status").filter(has_text="Queued ticket #42 is on hold")
    messages = page.locator("#message-list" if surface == "desktop" else ".av-messages")
    result = messages.get_by_text("The first ticket is complete.", exact=True)
    expect(notice).to_contain_text(reason)
    expect(result).to_have_count(1)
    expect(result).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    EVIDENCE.mkdir(exist_ok=True)
    notice.scroll_into_view_if_needed()
    page.screenshot(path=EVIDENCE / f"{width}-{surface}-recovered-workflow-result.png", animations="disabled")
    result.evaluate("(node) => { window.originalQueueResult = node; }")
    if surface == "desktop":
        draft = page.locator("#task-prompt")
        draft.fill("Keep this follow-up unsent.")

    monkeypatch.setattr("distr.core.workflow.ticket_dispatch.get_session", factory)
    monkeypatch.setattr("distr.gui.web.kanban_events.increment_kanban_updated", lambda **_: None)
    with factory.begin() as db:
        successor_ticket = db.get(KanbanTicket, 42)
        successor_ticket.linked_workflow_id, successor_ticket.workflow_status = workflow_id, "ready"
        predecessor_ticket = db.get(KanbanTicket, ticket)
        predecessor_ticket.workflow_status = "completed"
        predecessor = db.get(AutoWorkflowRun, run_id)
        data = json.loads(predecessor.run_data or "{}")
        data["queue_handoff_pending"] = dispatcher._ticket_queue_handoff_scope(predecessor_ticket)
        predecessor.run_data = json.dumps(data)

    def admit_successor(**kwargs):
        with factory.begin() as db:
            db.get(KanbanTicket, kwargs["ticket_id"]).workflow_status = "running"
            successor = AutoWorkflowRun(workflow_id=kwargs["workflow_id"], board_id=kwargs["board_id"],
                status="running", ticket_id=kwargs["ticket_id"], run_data=json.dumps(kwargs["run_metadata"]))
            db.add(successor)
            db.flush()
            return {"run_id": successor.id}

    admitted = Mock(side_effect=admit_successor)
    with monkeypatch.context() as patch:
        patch.setattr("distr.core.workflow.work_dispatch.dispatch_work_item", admitted)
        dispatcher._auto_resume_interrupted_runs(background=False)
    admitted.assert_called_once()
    assert admitted.call_args.kwargs["ticket_id"] == 42
    assert "queue_handoff_hold" not in client.get(EXECUTION).json()
    if surface == "paired":
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(notice).to_have_count(0, timeout=12000)
    assert result.evaluate("(node) => node === window.originalQueueResult")
    if surface == "desktop":
        expect(draft).to_have_value("Keep this follow-up unsent.")
        expect(draft).to_be_focused()
    page.screenshot(path=EVIDENCE / f"{width}-{surface}-terminal-queue-advanced.png", animations="disabled")

    # Show a failure reported through the real IDE callback route.
    monkeypatch.setattr(dispatcher, "increment_workflow_updated", lambda: None)
    monkeypatch.setattr("distr.core.workflow.router.get_session", factory)
    monkeypatch.setattr("distr.core.workflow.post_execution.get_session", factory)
    monkeypatch.setattr(dispatcher, "_finalize_terminal_run", lambda *_: None)
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_step_finished", lambda **_: None)
    monkeypatch.setattr("distr.core.workspace_memory.feedback_sync.persist_worker_feedback", lambda **_: None)
    monkeypatch.setattr("distr.core.kanban.project_execution.get_session", factory)
    monkeypatch.setattr("distr.core.kanban.project_execution.ensure_project_execution_tables", lambda: None)
    with factory.begin() as db:
        run = db.get(AutoWorkflowRun, run_id)
        step = AutoWorkflowStep(workflow_id=workflow_id, name="IDE work", status="waiting", on_fail_goto=-1)
        db.add(step)
        db.flush()
        run.status, run.current_step_id = "waiting", step.id
        attempt = ProjectExecutionSession(project_id=9, workflow_id=workflow_id, run_id=run_id,
            step_id=step.id, ticket_id=ticket, route_type="ide_bridge", route_backend="cursor", status="waiting")
        db.add(attempt)
        db.flush()
        attempt_id, step_id = attempt.id, step.id
        run.run_data = json.dumps({"source_type": "workflow_queue", "project_id": 9,
            "execution_session_id": attempt_id, "waiting_kind": "ide_handoff"})
        moved = db.get(KanbanTicket, ticket)
        moved.linked_workflow_id = workflow_id
        moved.workflow_status = "running"
    callback = client.post(f"/api/workflows/{workflow_id}/runs/{run_id}/codex-events", json={
        "event_type": "cursor_failed", "status": "failed", "output": "Cursor could not finish the work.",
        "execution_session_id": attempt_id, "step_id": step_id, "ticket_id": ticket, "project_id": 9,
    })
    assert callback.status_code == 200 and callback.json()["buffered"] is True
    execution = client.get(EXECUTION).json()
    assert execution["status"] == "failed"
    warning = execution["queue_ide_failure"]
    assert warning == "Cursor could not finish the work."
    if surface == "paired":
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    stopped = page.get_by_role("status").filter(has_text=warning)
    expect(stopped).to_be_visible(timeout=12000)
    if surface == "desktop":
        expect(draft).to_have_value("Keep this follow-up unsent.")
    else:
        expect(page.get_by_text(f"Workflow run {run_id} finished with status: failed.", exact=True)).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    stopped.scroll_into_view_if_needed()
    page.screenshot(path=EVIDENCE / f"{width}-{surface}-queued-ide-failed.png", animations="disabled")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_paired_telegram_closing_reply_tracks_the_exact_receipt(page, review, monkeypatch, tmp_path, width):
    from types import SimpleNamespace
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from distr.core.workflow import dispatcher, reply_delivery
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    _, factory, details, _ = review
    monkeypatch.setattr(receipt_writes, "_journal_path", lambda: tmp_path / "receipt-journal")
    monkeypatch.setattr(development_harness, "get_session", factory)
    monkeypatch.setattr(reply_delivery, "execute_approved_workflow_reply", execute_approved_workflow_reply)
    sender = DummyTelegramSender()
    sender._closing_reply_receipts = True
    monkeypatch.setattr("distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app", lambda: sender)
    with factory.begin() as db:
        ticket = db.get(KanbanTicket, details["ticket_id"])
        ticket.source_provider, ticket.source_thread_id = "telegram", str(sender.telegram_user_id)
        db.get(Chat, 17).params = json.dumps({"development": {"ticket_id": ticket.id}})
        envelope = reply_delivery._delivery_envelope(db, ticket, reply_delivery.resolve_reply_target(ticket), "")
    approval = request_review(review, {**details, "channel": "telegram", "delivery": envelope}).json()
    assert decide(review, approval["request_id"]).json()["status"] == "sending"
    scope = details["workflow"]
    with factory.begin() as db:
        run = db.get(AutoWorkflowRun, scope["run_id"])
        step = AutoWorkflowStep(workflow_id=run.workflow_id, name="Reply")
        db.add(step)
        db.flush()
        db.add(AutoWorkflowStepResult(run_id=run.id, step_id=step.id, status="passed",
                                     agent_response="reply_text: " + details["body"]))
        run.run_data = json.dumps({
            "project_id": 9, "development_turn_id": scope["turn_id"], "closing_reply_context": scope,
            "terminal_receipt_pending": "completed",
            "terminal_reply_scope": reply_delivery.terminal_reply_scope(db, db.get(KanbanTicket, details["ticket_id"])),
        })
    monkeypatch.setattr(dispatcher, "get_session", factory)
    dispatcher._auto_resume_interrupted_runs(background=False)
    with factory() as db:
        receipt = json.loads(db.get(AutoWorkflowRun, scope["run_id"]).run_data)["terminal_receipt"]
        assert receipt["reply_delivery"]["delivery_status"] == "sending"

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame["command"]
            data = {"screens": []}
            if command == "api_relay":
                call = frame["data"]
                assert call["method"] == "GET"
                path = call["path"]
                status, result = 200, {}
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": 17}
                elif path == "/api/chats/17?surface=development":
                    result = {"title": "Delivery proposal", "project_id": 9, "messages": []}
                elif path == EXECUTION:
                    response = client.get(path)
                    status, result = response.status_code, response.json()
                    assert str(sender.telegram_user_id) not in response.text and details["body"] not in response.text
                data = {"status": status, "response": result}
            socket.send(json.dumps({"type": "remote_control_response", "request_id": frame["request_id"],
                                    "command": command, "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(f"{url}?channel=paired-test&proposal_key={'a' * 64}")
    region = page.get_by_role("region", name="Development execution")
    EVIDENCE.mkdir(exist_ok=True)

    def check(label, state):
        expect(region.get_by_role("status").get_by_text(label, exact=True)).to_be_visible()
        expect(region.get_by_text("Completed", exact=True)).to_be_visible()
        expect(region.get_by_role("button", name="Approve")).to_have_count(0)
        page.screenshot(path=EVIDENCE / f"{width}-telegram-closing-{state}.png", animations="disabled")

    check("Client reply send in progress", "awaiting")
    sender._process_message_queue()
    [frame] = sender.websocket_attempts
    with factory.begin() as db:
        row = db.query(OrchestratorEvent).filter_by(source="reply_delivery").one()
        payload = json.loads(row.payload)
        payload["telegram_attempt"]["deadline"] = 0
        row.payload = json.dumps(payload)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    check("Client reply unconfirmed. Check the original conversation before sending again.", "unconfirmed")
    recovery_requests = []
    manager = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None, is_connected=lambda: True,
                              _closing_reply_receipt_recovery=True)
    def relay_lookup(request):
        recovery_requests.append(request)
        TelegramWebSocketManager._on_message(manager, json.dumps({
            "type": "message_sent", "closing_reply_verified": True, "client_message_id": request["token"],
            "chat_id": frame["chat_id"], "telegram_message_id": 44,
        }))
        return True
    manager._send_websocket_message = relay_lookup
    TelegramWebSocketManager._recover_closing_reply_receipts(manager)
    assert recovery_requests == [{"type": "closing_reply_receipt_lookup", "token": frame["client_message_id"]}]
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    check("Client reply sent", "sent")
    assert decide(review, approval["request_id"]).json()["status"] == "sent"
    with factory.begin() as db:
        row = db.query(OrchestratorEvent).filter_by(source="reply_delivery").one()
        payload = json.loads(row.payload)
        payload["detail"]["chat_id"] = 999
        row.payload = "{" if width in {390, 1280} else json.dumps(payload)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    check("Client reply unconfirmed. Check the original conversation before sending again.", "damaged")
    assert decide(review, approval["request_id"]).json()["status"] == "unconfirmed"
    sender._process_message_queue()
    assert sender.websocket_attempts == [frame]


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_paired_workflow_shows_scoped_client_reply_state(page, review, monkeypatch, tmp_path, width):
    from distr.core.db import Chat
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep, AutoWorkflowStepResult
    from distr.core.initiative import outcome_flow
    from distr.core.integrations.whatsapp import relay_client
    from distr.core.workflow import dispatcher, reply_delivery
    from distr.gui.web.security import INTERNAL_AUTH_HEADER

    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    _, factory, details, _ = review
    scope = details["workflow"]
    ticket = details["ticket_id"]
    monkeypatch.setattr(development_harness, "get_session", factory)
    monkeypatch.setattr("distr.gui.web.security.get_internal_api_token", lambda: "fixture-only")
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path))
    with factory.begin() as db:
        db.get(Chat, 17).params = json.dumps({"development": {"ticket_id": ticket}})
        run = db.get(AutoWorkflowRun, scope["run_id"])
        run.run_data = json.dumps({"project_id": 9, "reply_delivery": {
            "pending_approval": True, "telegram_flow_key": "approval", "body": "private reply fixture",
        }})
    outcome_flow.save_flow("approval", kind="reply", ticket_id=ticket,
                           approval_context=scope, status="awaiting_decision")
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    requests = []
    held_previews = []

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame.get("command")
            reply = {"type": "remote_control_response", "request_id": frame.get("request_id"), "command": command}
            data = {"screens": []} if command == "list_screens" else {}
            if command == "api_relay":
                method, path = frame["data"]["method"], frame["data"]["path"]
                requests.append((method, path))
                status = 200
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": 17}
                elif path == "/api/chats/17?surface=development":
                    result = {"title": "Delivery proposal", "project_id": 9, "messages": []}
                elif path == EXECUTION or path.startswith(f"{EXECUTION}/client-reply?"):
                    if path != EXECUTION and not held_previews:
                        held_previews.append((socket, reply))
                        return
                    response = client.get(path, headers={INTERNAL_AUTH_HEADER: "fixture-only"})
                    status, result = response.status_code, response.json()
                    if path == EXECUTION:
                        assert "private reply fixture" not in response.text
                else:
                    result = {}
                data = {"status": status, "response": result}
            socket.send(json.dumps({**reply, "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(f"{url}?channel=paired-test&proposal_key={'a' * 64}")
    region = page.get_by_role("region", name="Development execution")
    EVIDENCE.mkdir(exist_ok=True)
    for status, label in [
        ("awaiting_decision", "Client reply awaiting review in Telegram"),
        ("sending", "Client reply send in progress"),
        ("unconfirmed", "Client reply unconfirmed. Check the original conversation before sending again."),
        ("sent", "Client reply sent"),
        ("send_failed", "Client reply not sent"),
        ("missing", "Client reply review unavailable"),
    ]:
        if status != "awaiting_decision":
            outcome_flow.save_flow("approval", status=status)
            page.reload()
        expect(region.get_by_role("status").get_by_text("Completed", exact=True)).to_be_visible()
        expect(region.get_by_role("status").get_by_text(label, exact=True)).to_be_visible()
        expect(region.get_by_role("button", name="Approve")).to_have_count(0)
        page.screenshot(path=EVIDENCE / f"{width}-client-reply-{status}.png", animations="disabled")
    from distr.core.db.kanban import KanbanTicket
    from distr.core.workflow.reply_channel import resolve_reply_target
    from distr.core.workflow.reply_delivery import _delivery_envelope

    with factory() as db:
        ticket_row = db.get(KanbanTicket, ticket)
        envelope = _delivery_envelope(db, ticket_row, resolve_reply_target(ticket_row), "")
    details = {**details, "delivery": envelope}
    approval = request_review(review, details).json()
    assert approval["status"] == "pending"
    with factory.begin() as db:
        run = db.get(AutoWorkflowRun, scope["run_id"])
        step = AutoWorkflowStep(workflow_id=run.workflow_id, name="Reply")
        db.add(step)
        db.flush()
        db.add(AutoWorkflowStepResult(run_id=run.id, step_id=step.id, status="passed",
                                     agent_response="reply_text: " + details["body"]))
        run.run_data = json.dumps({
            "project_id": 9, "development_turn_id": scope["turn_id"], "closing_reply_context": scope,
            "terminal_receipt_pending": "completed",
            "terminal_reply_scope": reply_delivery.terminal_reply_scope(db, db.get(KanbanTicket, ticket)),
        })
    monkeypatch.setattr(dispatcher, "get_session", factory)
    sender = Mock()
    with monkeypatch.context() as patch:
        patch.setattr(reply_delivery, "deliver_ticket_reply", sender)
        dispatcher._auto_resume_interrupted_runs(background=False)
        dispatcher._auto_resume_interrupted_runs(background=False)
    sender.assert_not_called()
    with factory() as db:
        receipt = json.loads(db.get(AutoWorkflowRun, scope["run_id"]).run_data)["terminal_receipt"]
        assert receipt["reply_delivery"]["approval_request_id"] == approval["request_id"]
    page.reload()
    expect(region.get_by_role("status").get_by_text("Completed", exact=True)).to_be_visible()
    expect(region.get_by_role("status").get_by_text("Client reply awaiting review in desktop Development")).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-client-reply-review-recovered.png", animations="disabled")
    review_button = region.get_by_role("button", name="View client reply", exact=True)
    review_button.click()
    dialog = page.get_by_role("dialog", name="Client reply")
    expect(dialog.get_by_role("status")).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-client-reply-loading.png", animations="disabled")
    socket, held = held_previews[0]
    socket.send(json.dumps({**held, "data": {"status": 503, "response": {"detail": "The desktop is busy. Try again."}}}))
    expect(dialog.get_by_role("alert")).to_contain_text("The desktop is busy.")
    page.screenshot(path=EVIDENCE / f"{width}-client-reply-unavailable.png", animations="disabled")
    dialog.get_by_role("button", name="Retry").click()
    expect(dialog.get_by_text("12025550142", exact=True)).to_be_visible()
    expect(dialog.get_by_label("Reply", exact=True)).to_have_value(details["body"])
    expect(dialog.get_by_label("Reply", exact=True)).to_have_attribute("readonly", "")
    expect(dialog.get_by_role("button", name="Approve")).to_have_count(0)
    page.screenshot(path=EVIDENCE / f"{width}-client-reply-preview.png", animations="disabled")
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(review_button).to_be_focused()

    def replace_destination():
        with factory.begin() as db:
            assert db.query(OrchestratorEvent).filter_by(source="reply_delivery", status="sending").count() == 1
            db.get(KanbanTicket, ticket).source_thread_id = "12025550143"
        return {}

    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value={"success": True})))
    monkeypatch.setattr(reply_delivery, "execute_approved_workflow_reply", execute_approved_workflow_reply)
    monkeypatch.setattr(relay_client, "whatsapp_send_dry_run_enabled", lambda: False)
    monkeypatch.setattr(relay_client, "relay_request_headers", replace_destination)
    monkeypatch.setattr(relay_client.requests, "post", post)
    decision = decide(review, approval["request_id"]).json()
    assert decision["status"] == "rejected"
    post.assert_not_called()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(region.get_by_role("status").get_by_text("Client reply not sent", exact=True)).to_be_visible()
    expect(review_button).to_have_count(0)
    expect(region.get_by_role("status").get_by_text("Completed", exact=True)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-client-reply-provider-scope-changed.png", animations="disabled")
    assert all(method == "GET" for method, path in requests if path.startswith(EXECUTION) or "dev-approvals" in path)


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
@pytest.mark.parametrize("runtime", ["native", "cli"])
def test_linked_thread_shows_and_stops_only_the_exact_run(page, monkeypatch, width, runtime):
    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    page.set_default_timeout(7000)
    page.set_viewport_size({"width": width, "height": 850})
    key = "a" * 64
    run = {"id": 27, "workflow_id": 3, "status": "running", "direct": False,
           "current_step_name": "Verify delivery constraints",
           "route_receipt": {"selected": {"backend": "Pi", "model": "Fixture model"}}}
    replacement = {"id": 99, "turn_id": 42, "status": "running", "direct": True,
                   "runtime_id": "native", "model": "Native fixture model"}
    target = {"turn_id": 42}
    if runtime == "cli":
        replacement.update(runtime_id="cli_harness", model="CLI fixture model", backend_id="Pi",
                           cli_stop_available=True, execution_session_id=99, project_id=7, board_id=3)
        target = {"execution_session_id": 99}
    monkeypatch.setattr(development_harness, "development_execution_state", lambda chat_id: dict(run) if chat_id == 17 else {})
    dispatches, process_stops, requests = [], [], []
    read_unavailable = []
    lose_steering_receipt = [True]
    lose_steering_request = []
    steering_receipts = {}
    sockets, held_execution = [], []
    hold_execution = []
    lose_stop = [True]
    stopping_turns = set()

    def cancel_turn(chat_id, *, expected_turn_id):
        dispatches.append((chat_id, expected_turn_id))
        stopping_turns.add((chat_id, expected_turn_id))
        return True

    monkeypatch.setattr("distr.core.turn_runtime.cancel_active_turn", cancel_turn)
    monkeypatch.setattr("distr.core.turn_runtime.service.active_turn_stop_pending",
                        lambda chat_id, turn_id: (chat_id, turn_id) in stopping_turns)
    monkeypatch.setattr("distr.core.turn_runtime.service.native_steering_available",
                        lambda chat_id, turn_id: runtime == "native" and chat_id == 17 and turn_id == run.get("turn_id"))

    def steer_exact(chat_id, turn_id, project_id, submission_id, content):
        assert (chat_id, turn_id, project_id, content) == (17, 42, 7, "Preserve the signed receipt.")
        if submission_id not in steering_receipts:
            assert run["turn_id"] == turn_id
            steering_receipts[submission_id] = {"chat_id": chat_id, "source_ref": submission_id,
                                                "status": "delivered", "result_summary": ""}
        return steering_receipts[submission_id]

    monkeypatch.setattr("distr.core.workflow.development_control.steer_exact_turn", steer_exact)
    def get_steering_receipt(chat_id, turn_id, project_id, submission_id):
        assert (chat_id, turn_id, project_id) == (17, 42, 7)
        saved = steering_receipts.get(submission_id, {})
        return {"chat_id": chat_id, "turn_id": turn_id, "submission_id": submission_id,
                "status": saved.get("status", "missing"), "summary": ""}

    monkeypatch.setattr("distr.core.workflow.development_control.exact_steering_receipt", get_steering_receipt)
    monkeypatch.setattr("distr.core.project_cli_backends.registry.terminate_backend_process",
                        lambda project, backend, **scope: process_stops.append((project, backend, scope)) or True)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    def relay(socket):
        sockets.append(socket)
        def handle(raw):
            request = json.loads(raw)["body"]
            if request["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = request.get("command")
            reply = {"type": "remote_control_response", "command": command, "request_id": request.get("request_id")}
            status, data = 200, {}
            if command == "list_screens":
                data = {"screens": []}
            if command == "api_relay":
                method, path = request["data"]["method"], request["data"]["path"]
                body = request["data"].get("body")
                requests.append((method, path, body))
                if path == f"/api/workflows/studio/incoming/proposals/review?review_key={key}":
                    data = {"status": "thread", "chat_id": 17}
                elif path == "/api/chats/17?surface=development":
                    data = {"title": "Delivery proposal", "project_id": 7,
                            "messages": [{"role": "user", "content": "Verify the handoff."}]}
                elif path == EXECUTION and read_unavailable:
                    status, data = 503, {"detail": "Execution is temporarily unavailable."}
                elif path.startswith(EXECUTION):
                    first_stop = method == "POST" and path.endswith("/stop-exact") and bool(lose_stop)
                    if first_stop:
                        lose_stop.clear()
                        hold_execution.append(True)
                        if runtime == "native":
                            socket.send(json.dumps({**reply, "error": "Fixture Stop request lost"}))
                            return
                    if method == "POST" and path.endswith("/steer-exact") and lose_steering_request:
                        lose_steering_request.clear()
                        socket.send(json.dumps({**reply, "error": "Fixture request lost before the desktop"}))
                        return
                    response = client.request(method, path, json=body)
                    status, data = response.status_code, response.json()
                    if first_stop:
                        assert status == 200
                        socket.send(json.dumps({**reply, "error": "Fixture Stop receipt lost"}))
                        return
                    if path.endswith("/steer-exact") and lose_steering_receipt:
                        lose_steering_receipt.clear()
                        socket.send(json.dumps({**reply, "error": "Fixture steering receipt lost"}))
                        return
            message = json.dumps({**reply, "data": {"status": status, "response": data} if command == "api_relay" else data})
            if command == "api_relay" and path == EXECUTION and method == "GET" and hold_execution:
                held_execution.append((socket, message))
                return
            socket.send(message)
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.goto(f"{url}?channel=paired-test&proposal_key={key}")
    EVIDENCE.mkdir(exist_ok=True)
    section = page.get_by_role("region", name="Development execution")
    stop = section.get_by_role("button", name="Stop", exact=True)
    expect(section.get_by_text("Verify delivery constraints")).to_be_visible()
    expect(section.get_by_text("Pi · Fixture model")).to_be_visible()
    def shot(state):
        page.screenshot(path=EVIDENCE / f"{width}-{runtime}-{state}.png", animations="disabled")

    def reconnect_during_read(next_run=None):
        hold_execution.append(True)
        sockets[-1].close()
        disconnected = page.get_by_text("Your draft is kept.", exact=False)
        expect(disconnected).to_be_visible()
        if next_run is not None:
            run.clear()
            run.update(next_run)
        expect(disconnected).to_have_count(0)

    def release_execution():
        hold_execution.clear()
        assert held_execution
        for socket, message in held_execution:
            socket.send(message)
        held_execution.clear()

    shot("running")
    stop.click()
    dialog = page.get_by_role("dialog", name="Stop workflow run")
    expect(dialog).to_be_visible()
    shot("confirm")
    reconnect_during_read(replacement)
    expect(page.get_by_role("button", name="Stop run", exact=True)).to_be_disabled()
    expect(dialog.get_by_text("Checking the run before you can confirm.")).to_be_visible()
    shot("reconnecting")
    release_execution()
    expect(dialog.get_by_text("The selected run changed or is unavailable.")).to_be_visible()
    expect(page.get_by_role("button", name="Stop run", exact=True)).to_be_disabled()
    shot("replaced")
    assert not dispatches
    page.get_by_role("button", name="Cancel", exact=True).click()
    steer = section.get_by_role("button", name="Steer", exact=True)
    if runtime == "native":
        expect(steer).to_be_visible()
        steer.click()
        steer_dialog = page.get_by_role("dialog", name="Steer active turn")
        receipt_dialog = page.get_by_role("dialog", name="Steering receipt")
        draft = steer_dialog.get_by_label("Guidance for this turn")
        draft.fill("Preserve the signed receipt.")
        shot("steering-draft")
        reconnect_during_read()
        expect(steer_dialog.get_by_text("Checking the current run…")).to_be_visible()
        expect(steer_dialog.get_by_role("button", name="Send guidance")).to_be_disabled()
        expect(draft).to_have_value("Preserve the signed receipt.")
        assert not any(method == "POST" and path.endswith("/steer-exact") for method, path, _ in requests)
        shot("steering-reconnecting")
        release_execution()
        expect(steer_dialog.get_by_role("button", name="Send guidance")).to_be_enabled()
        run.update(turn_id=43, model="Replacement fixture model")
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(steer_dialog.get_by_text("The selected turn changed or is unavailable.")).to_be_visible()
        expect(steer_dialog.get_by_role("button", name="Send guidance")).to_be_disabled()
        expect(draft).to_have_value("Preserve the signed receipt.")
        shot("steering-replaced")
        steer_dialog.get_by_role("button", name="Close", exact=True).last.click()
        run.update(turn_id=42, model="Native fixture model")
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(section.get_by_text("Native fixture model")).to_be_visible()
        steer.click()
        if not draft.input_value():
            draft.fill("Preserve the signed receipt.")
        steer_dialog.get_by_role("button", name="Send guidance").click()
        expect(steer_dialog.get_by_role("alert")).to_contain_text("Delivery was not confirmed")
        shot("steering-unconfirmed")
        run.update(turn_id=43, model="Replacement fixture model")
        saved = page.evaluate("Object.entries(sessionStorage).find(([key]) => key.startsWith('decisions:steering:'))[1]")
        assert set(json.loads(saved)) == {"submission_id", "turn_id", "expected_project_id"}
        page.reload()
        expect(section.get_by_text("Replacement fixture model")).to_be_visible()
        section.get_by_role("button", name="Steering receipt").click()
        expect(receipt_dialog).to_be_visible()
        expect(receipt_dialog.get_by_role("textbox")).to_have_count(0)
        shot("steering-restored")
        receipt_dialog.get_by_role("button", name="Check receipt").click()
        expect(section.get_by_text("Guidance was sent to the previous turn.")).to_be_visible()
        shot("steering-receipt")
        assert len(steering_receipts) == 1
        sent = [body for method, path, body in requests if method == "POST" and path.endswith("/steer-exact")]
        assert len(sent) == 1
        assert any(method == "GET" and sent[0]["submission_id"] in path for method, path, _ in requests)
        assert not page.evaluate("Object.keys(sessionStorage).some(key => key.startsWith('decisions:steering:'))")
        run.update(turn_id=42, model="Native fixture model")
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(section.get_by_text("Native fixture model")).to_be_visible()
        lose_steering_request.append(True)
        steer.click()
        draft.fill("Preserve the signed receipt.")
        steer_dialog.get_by_role("button", name="Send guidance").click()
        expect(steer_dialog.get_by_role("alert")).to_contain_text("Delivery was not confirmed")
        page.reload()
        section.get_by_role("button", name="Steering receipt").click()
        receipt_dialog.get_by_role("button", name="Check receipt").click()
        expect(steer_dialog.get_by_role("alert")).to_contain_text("The desktop has no receipt yet.")
        expect(draft).to_have_value("")
        expect(steer_dialog.get_by_role("button", name="Retry same request")).to_be_disabled()
        shot("steering-missing")
        draft.fill("Preserve the signed receipt.")
        steer_dialog.get_by_role("button", name="Retry same request").click()
        expect(section.get_by_text("Guidance sent to the selected turn.")).to_be_visible()
        shot("steering-retried")
        sent = [body for method, path, body in requests if method == "POST" and path.endswith("/steer-exact")]
        assert len(sent) == 3 and sent[1] == sent[2] and len(steering_receipts) == 2
    else:
        expect(steer).to_have_count(0)
    stop.click()
    page.get_by_role("button", name="Stop run", exact=True).click()
    expect(section.get_by_text("Stop was not confirmed.", exact=False)).to_be_visible()
    expect(stop).to_be_disabled()
    assert len([path for method, path, _ in requests if method == "POST" and path.endswith("/stop-exact")]) == 1
    shot("stop-unconfirmed")
    release_execution()
    if runtime == "native":
        expect(stop).to_be_enabled()
        shot("stop-retry")
        assert not dispatches
        stop.click()
        page.get_by_role("button", name="Stop run", exact=True).click()
    else:
        page.reload()
    expect(section.get_by_text("Stopping", exact=True)).to_be_visible()
    expect(stop).to_have_count(0)
    shot("requested")
    assert dispatches == [(17, 42)]
    assert process_stops == ([(7, "Pi", {"board_id": 3, "execution_id": 99, "execution_kind": "development_session"})]
                             if runtime == "cli" else [])
    assert [(path, body) for method, path, body in requests if method == "POST" and path.endswith("/stop-exact")] == [
        (EXECUTION + "/stop-exact", target),
    ] * (2 if runtime == "native" else 1)
    run.update(status="cancelled")
    expect(section.get_by_text("Stopped", exact=True)).to_be_visible()
    expect(stop).to_have_count(0)
    shot("stopped")
    read_unavailable.append(True)
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(section.get_by_role("alert")).to_contain_text("Could not check the current run.")
    shot("unavailable")
    read_unavailable.clear()
    section.get_by_role("button", name="Retry").click()
    expect(section.get_by_role("alert")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_paired_run_reports_ticket_move_before_worker_start(page, monkeypatch, scoped_direct_ticket, width):
    from concurrent.futures import ThreadPoolExecutor

    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    scope = scoped_direct_ticket
    launched = []

    def probe(*args, **kwargs):
        scope.move("project")
        return {"status": {"ready": True}}

    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", probe)
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", lambda *a, **kw: launched.append(a))
    with ThreadPoolExecutor(max_workers=1) as executor:
        state = executor.submit(
            development_harness.dispatch_development_prompt,
            scope.chat_id, "Review this proposal", dispatch_async=False, save_prompt=True,
        ).result(timeout=20)
    assert state["status"] == "failed"
    assert not launched
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    key = "b" * 64
    execution_path = f"/api/workflows/studio/tasks/{scope.chat_id}/execution"
    execution_reads = []

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame.get("command")
            reply = {"type": "remote_control_response", "request_id": frame.get("request_id"), "command": command}
            data = {"screens": []} if command == "list_screens" else {}
            if command == "api_relay":
                method, path = frame["data"]["method"], frame["data"]["path"]
                assert method == "GET"
                result, status = {}, 200
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": scope.chat_id}
                elif path == f"/api/chats/{scope.chat_id}?surface=development":
                    result = {"title": "Review this proposal", "project_id": scope.project_id, "messages": []}
                elif path == execution_path:
                    response = client.get(path)
                    status, result = response.status_code, response.json()
                    execution_reads.append(result)
                data = {"status": status, "response": result}
            socket.send(json.dumps({**reply, "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(f"{url}?channel=paired-test&proposal_key={key}")
    region = page.get_by_role("region", name="Development execution")
    expect(region.get_by_text("Failed", exact=True)).to_be_visible()
    expect(region.get_by_text("changed before work started", exact=False)).to_be_visible()
    expect(region.get_by_role("button", name="Stop")).to_have_count(0)
    assert execution_reads[-1]["job_id"] == state["job_id"]
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / f"{width}-direct-ticket-scope-changed.png", animations="disabled")
