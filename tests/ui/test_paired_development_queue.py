"""Follow-ups use the same durable queue from a paired proposal transcript."""
import json
import os
import re
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.db import Chat
from distr.core.workflow import development_control, development_harness
from distr.core.workflow.development_threads import ensure_development_thread, rebind_development_thread
from distr.gui.web.routes import chat as chat_routes
from distr.gui.web.routes.development.thread_controls import register_routes as register_controls
from distr.gui.web.routes.development.threads import register_routes as register_threads
from tests.core.test_development_control import development_db  # noqa: F401
from tests.core.test_queued_submissions import _finish_simulated_direct_worker
from tests.core.test_development_harness import scoped_direct_ticket  # noqa: F401
from tests.core.test_proposal_continuity_integration import intake  # noqa: F401
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base  # noqa: F401

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path("/tmp/decisions-paired-development-queue")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_desktop_queue_scope_is_visible_in_both_apps(page, development_db, fixture_base, width):
    from distr.core.db.projects import Project
    from distr.core.db.workflow import DevelopmentCommand

    remote = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not remote:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    chat_id = development_db["chat_id"]
    old = development_control.enqueue_command(chat_id, "Instruction from the original project.", command_type="followup")
    legacy = development_control.enqueue_command(chat_id, "Older instruction without a saved project.", command_type="followup")
    with development_db["session"]() as db:
        db.get(DevelopmentCommand, legacy["id"]).metadata_json = "{}"
        project = Project(name="New project")
        db.add(project)
        db.flush()
        project_id = project.id
        db.commit()
    development_control.update_thread_controls(chat_id, project_id=project_id)
    current = development_control.enqueue_command(chat_id, "Instruction for the current project.", command_type="followup")
    app, router = FastAPI(), APIRouter()
    register_controls(router, None)
    app.include_router(router, prefix="/api")
    api = TestClient(app)
    task = f"/api/workflows/studio/tasks/{chat_id}"
    rows = api.get(f"{task}/commands?include_terminal=false").json()["items"]
    assert [(row["id"], row["scope_status"]) for row in rows] == [
        (current["id"], "current"), (old["id"], "earlier"), (legacy["id"], "unknown"),
    ]
    notices = ["Queued for an earlier project, board, or ticket.",
               "Original project could not be verified. Queue it again from the intended thread."]
    page.set_viewport_size({"width": width, "height": 900})
    _install_api(page, [], current_board_linked=True, direct_execution=True, terminal_session=False)
    page.route("**/api/workflows/studio/control-state?*", lambda route: route.fulfill(json={
        "commands": [{**row, "chat_id": 17} for row in rows], "controls": {}, "interactions": [],
    }))
    page.goto(f"{fixture_base}/development/threads/17/")
    desktop = page.locator("#thread-command-queue")
    for command, notice in zip((old, legacy), notices):
        item = desktop.locator(f"[data-queued-id='{command['id']}']")
        expect(item.get_by_text(notice, exact=True)).to_be_visible()
        expect(item.get_by_role("button", name="Steer current thread")).to_have_count(0)
        expect(item.get_by_role("button", name="Start in a new thread")).to_have_count(0)
        expect(item.get_by_role("button", name="Delete queued item")).to_be_enabled()
    expect(desktop.locator(f"[data-queued-id='{current['id']}']").get_by_role("button", name="Start in a new thread")).to_be_enabled()
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / f"{width}-desktop-held-queue-scope.png", animations="disabled")
    last = desktop.locator(f"[data-queued-id='{legacy['id']}']")
    last.get_by_role("button", name="Delete queued item").scroll_into_view_if_needed()
    expect(last.get_by_text(notices[1], exact=True)).to_be_in_viewport(ratio=1)
    page.screenshot(path=EVIDENCE / f"{width}-desktop-unknown-queue-scope.png", animations="disabled")

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame["command"]
            data = {"screens": []}
            if command == "api_relay":
                path = frame["data"]["path"]
                assert frame["data"]["method"] == "GET"
                result = {}
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": chat_id}
                elif path.startswith(f"/api/chats/{chat_id}"):
                    result = {"id": chat_id, "title": "Review queued instructions", "project_id": project_id,
                              "development_scope": [project_id, "", "", None, ""], "messages": []}
                elif path == f"{task}/execution":
                    result = {"status": "completed", "direct": True}
                elif path.startswith(f"{task}/commands"):
                    result = api.get(path).json()
                data = {"status": 200, "response": result}
            socket.send(json.dumps({"type": "remote_control_response", "command": command,
                                    "request_id": frame.get("request_id"), "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.goto(f"{remote}?channel=paired-test&proposal_key={'f' * 64}")
    paired = page.locator(".av-development-queue")
    paired.get_by_text("Follow-ups (3)", exact=True).click()
    for command, notice in zip((old, legacy), notices):
        item = paired.get_by_role("listitem").filter(has_text=command["content"])
        expect(item.get_by_text(notice, exact=True)).to_be_visible()
        expect(item.get_by_role("button", name="Run next")).to_have_count(0)
        expect(item.get_by_role("button", name="Remove")).to_be_enabled()
    expect(paired.get_by_role("listitem").filter(has_text=current["content"])
           .get_by_role("button", name="Run next")).to_be_enabled()
    page.screenshot(path=EVIDENCE / f"{width}-paired-held-queue-scope.png", animations="disabled")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_moved_ticket_result_is_held_on_desktop_and_paired_view(page, monkeypatch, scoped_direct_ticket, fixture_base, width):
    from concurrent.futures import ThreadPoolExecutor

    from distr.core.turn_runtime import TurnResult

    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    scope = scoped_direct_ticket
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
                        lambda *a, **kw: {"status": {"ready": True}})
    monkeypatch.setattr("distr.core.turn_runtime.build_development_context", lambda *a, **kw: [])

    async def worker(request, **kwargs):
        scope.move("project")
        return TurnResult(success=True, runtime_id="cli_harness", backend_id="pi",
                          model="fixture", output="Old worker result.")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", worker)
    with ThreadPoolExecutor(max_workers=1) as executor:
        state = executor.submit(
            development_harness.dispatch_development_prompt, scope.chat_id,
            "Review the original project.", save_prompt=True, dispatch_async=False,
        ).result(timeout=12)
    app, router = FastAPI(), APIRouter()
    register_threads(router, None)
    app.include_router(router, prefix="/api")
    api = TestClient(app)
    run_path = f"/api/workflows/studio/tasks/{scope.chat_id}/execution"
    run_response = api.get(run_path)
    assert run_response.status_code == 200
    notice = "The Development project, board or ticket changed while the worker was running."
    with scope.session() as db:
        turn = db.get(Chat, state["turn_id"])
        transcript = [{"role": "user", "content": "Review the original project.", "chat_row_id": turn.id},
                      {"role": "assistant", "content": turn.response, "chat_row_id": turn.id}]
    chat = {"id": 17, "title": "Review the original project", "project_id": scope.project_id,
            "development": True, "development_board_key": "decisions:70", "messages": transcript}
    page.set_viewport_size({"width": width, "height": 900})
    _install_api(page, [], current_board_linked=True, direct_execution=True, run_status="failed", terminal_session=False)
    page.route("**/api/chats/17**", lambda route: route.fulfill(json=chat))
    page.route("**/api/workflows/studio/tasks/17/execution", lambda route: route.fulfill(json=run_response.json()))
    page.goto(f"{fixture_base}/development/threads/17/")
    expect(page.get_by_text(notice, exact=False).first).to_be_visible()
    expect(page.get_by_text("Old worker result.", exact=True)).to_have_count(0)
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / f"{width}-desktop-result-held.png", animations="disabled")

    def relay(socket):
        def handle(raw):
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame["command"]
            data = {"screens": []}
            if command == "api_relay":
                path = frame["data"]["path"]
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": scope.chat_id}
                elif path.startswith(f"/api/chats/{scope.chat_id}"):
                    result = {**chat, "id": scope.chat_id}
                elif path == run_path:
                    result = run_response.json()
                else:
                    result = {}
                data = {"status": 200, "response": result}
            socket.send(json.dumps({"type": "remote_control_response", "command": command,
                                    "request_id": frame.get("request_id"), "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.goto(f"{url}?channel=paired-test&proposal_key={'c' * 64}")
    region = page.get_by_role("region", name="Development execution")
    expect(region.get_by_role("status")).to_have_text("Failed")
    expect(region.get_by_text("Result held for scope review.", exact=True)).to_be_visible()
    expect(page.get_by_text(notice, exact=False)).to_be_visible()
    expect(page.get_by_text("Old worker result.", exact=True)).to_have_count(0)
    page.screenshot(path=EVIDENCE / f"{width}-paired-result-held.png", animations="disabled")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_followup_queue_recovers_an_unknown_receipt_and_does_not_interrupt(page, monkeypatch, intake, width):
    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    page.set_default_timeout(7000)
    page.set_viewport_size({"width": width, "height": 850})
    session, _, projects = intake
    project_id = projects[0]
    chat_id = ensure_development_thread(title="Delivery follow-ups", project_id=project_id)
    with session() as db:
        db.add(Chat(parent_id=chat_id, response="The handoff is ready for review."))
        db.commit()
    key = "a" * 64
    task = f"/api/workflows/studio/tasks/{chat_id}"
    monkeypatch.setattr(development_control, "get_session", session)
    monkeypatch.setattr(chat_routes, "get_session", session)
    monkeypatch.setattr("distr.core.laya_runtime.get_mode", lambda: "off")
    monkeypatch.setattr("distr.core.kanban.ticket_policy._global_complexity_route", lambda tier, _settings: {
        "backend": "pi", "model": f"test-{tier}", "model_provider": "ollama",
    })

    from distr.core.turn_runtime import TurnResult

    async def simulated_execution(request, **kwargs):
        return TurnResult(success=False, error="Simulated worker outcome", runtime_id="cli_harness",
                          backend_id=request.route.backend_id, model=request.route.model)

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", simulated_execution)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend",
                        lambda *a, **kw: {"status": {"ready": True}})
    app, router = FastAPI(), APIRouter()
    register_threads(router, None)
    register_controls(router, None)
    app.include_router(router, prefix="/api")
    app.include_router(chat_routes.create_routes(Path.cwd()), prefix="/api")
    client = TestClient(app)
    requests = []
    lost_receipt = [True]
    drop_submission = []
    refuse_retry = []
    cancel_before_receipt = []
    hide_saved_receipt = [True]
    reveal_on_next_connection = []
    make_busy_on_start = [True]
    sockets, held = [], {}
    queue_read = f"{task}/commands?include_terminal=false"
    execution_read = f"{task}/execution"
    hold_reads = set()
    development_harness._update_execution(chat_id, job_id="fixture", status="running")

    def relay(socket):
        sockets.append(socket)
        if reveal_on_next_connection:
            reveal_on_next_connection.clear()
            hide_saved_receipt.clear()
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
                    data = {"status": "thread", "chat_id": chat_id}
                elif path.startswith(f"/api/chats/{chat_id}"):
                    response = client.get(path)
                    status, data = response.status_code, response.json()
                elif path.startswith(f"{task}/"):
                    if path == f"{task}/followups" and method == "POST" and refuse_retry:
                        refuse_retry.clear()
                        socket.send(json.dumps({**reply, "error": "Too many requests. Try again shortly.", "reason": "relay_rate_limited"}))
                        return
                    if path == f"{task}/followups" and method == "POST" and drop_submission:
                        drop_submission.clear()
                        socket.send(json.dumps({**reply, "error": "Fixture request lost before desktop receipt"}))
                        return
                    if path.endswith("/start") and make_busy_on_start:
                        development_harness._update_execution(chat_id, job_id="fixture", status="running")
                        make_busy_on_start.clear()
                    response = client.request(method, path, json=body)
                    status, data = response.status_code, response.json()
                    if path == f"{task}/followups" and method == "POST" and lost_receipt:
                        assert status == 200
                        lost_receipt.clear()
                        if cancel_before_receipt:
                            cancel_before_receipt.clear()
                            assert development_control.update_command(data["id"], cancel=True)["status"] == "cancelled"
                        socket.send(json.dumps({**reply, "error": "Fixture receipt lost"}))
                        return
                    if path == f"{task}/followups" and method == "POST":
                        hide_saved_receipt.clear()
                    if path == f"{task}/commands?include_terminal=false" and hide_saved_receipt:
                        data = {**data, "items": []}
                    if path.endswith("/receipt") and hide_saved_receipt:
                        status, data = 404, {"detail": "Fixture receipt not yet visible"}
            message = json.dumps({**reply, "data": {"status": status, "response": data} if command == "api_relay" else data})
            if command == "api_relay" and method == "GET" and path in hold_reads:
                held.setdefault(path, []).append((socket, message))
                return
            socket.send(message)
        socket.on_message(handle)

    def release(path):
        hold_reads.remove(path)
        for socket, message in held.pop(path):
            socket.send(message)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.goto(f"{url}?channel=paired-test&proposal_key={key}")
    EVIDENCE.mkdir(exist_ok=True)
    queue = page.locator(".av-development-queue")
    section = page.get_by_role("region", name="Development execution")
    expect(section.get_by_text("Running", exact=True)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-collapsed.png", animations="disabled")
    queue.get_by_text("Follow-ups", exact=True).click()
    draft = queue.get_by_label("Follow-up instruction")
    instruction = "Fix the failing production migration regression."
    draft.fill(instruction)
    page.screenshot(path=EVIDENCE / f"{width}-draft.png", animations="disabled")
    development_harness._update_execution(chat_id, job_id="fixture", status="completed")
    expect(section.get_by_text("Completed", exact=True)).to_be_visible()
    expect(draft).to_have_value(instruction)
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_role("alert")).to_contain_text("Queueing was not confirmed")
    expect(draft).to_have_value(instruction)
    page.screenshot(path=EVIDENCE / f"{width}-unconfirmed.png", animations="disabled")
    queue.get_by_role("button", name="Retry queueing").click()
    expect(queue.get_by_text("Follow-up queued.", exact=True)).to_be_visible()
    expect(draft).to_have_value("")
    assert len(development_control.list_commands(chat_id)) == 1
    submissions = [body for method, path, body in requests if method == "POST" and path == f"{task}/followups"]
    assert len(submissions) == 2 and submissions[0] == submissions[1]
    start = queue.get_by_role("button", name="Run next")
    expect(start).to_be_enabled()
    page.screenshot(path=EVIDENCE / f"{width}-queued.png", animations="disabled")
    draft.fill("Preserve this draft across reconnect.")
    queue.get_by_role("button", name="Remove", exact=True).click()
    reconnect_dialog = page.get_by_role("dialog", name="Remove queued follow-up")
    hold_reads.update([queue_read, execution_read])
    writes_before = sum(method == "POST" for method, _, _ in requests)
    sockets[-1].close()
    disconnected = page.get_by_text("Your draft is kept.", exact=False)
    expect(disconnected).to_be_visible()
    expect(disconnected).to_have_count(0)
    expect(reconnect_dialog.get_by_text("Checking the follow-up before you can confirm.")).to_be_visible()
    expect(reconnect_dialog.get_by_role("button", name="Remove follow-up")).to_be_disabled()
    page.screenshot(path=EVIDENCE / f"{width}-remove-reconnecting.png", animations="disabled")
    reconnect_dialog.get_by_role("button", name="Keep follow-up").click()
    expect(queue.get_by_text("Checking the queue…")).to_be_visible()
    expect(section.get_by_text("Checking the current run…")).to_be_visible()
    expect(start).to_be_disabled()
    expect(queue.get_by_role("button", name="Remove", exact=True)).to_be_disabled()
    expect(queue.get_by_role("button", name="Queue follow-up")).to_be_disabled()
    expect(draft).to_have_value("Preserve this draft across reconnect.")
    page.screenshot(path=EVIDENCE / f"{width}-reconnecting.png", animations="disabled")
    release(queue_read)
    expect(queue.get_by_role("button", name="Queue follow-up")).to_be_enabled()
    expect(start).to_be_disabled()
    release(execution_read)
    expect(start).to_be_enabled()
    assert sum(method == "POST" for method, _, _ in requests) == writes_before
    draft.fill("")
    page.screenshot(path=EVIDENCE / f"{width}-reconnected.png", animations="disabled")
    start.click()
    expect(queue.get_by_role("alert")).to_contain_text("already running")
    expect(section.get_by_text("Running", exact=True)).to_be_visible()
    expect(start).to_be_disabled()
    assert development_control.list_commands(chat_id)[0]["status"] == "queued"
    page.screenshot(path=EVIDENCE / f"{width}-run-conflict.png", animations="disabled")
    development_harness._update_execution(chat_id, job_id="fixture", status="completed")
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(start).to_be_enabled()
    start.click()
    expect(queue.get_by_text(re.compile(r"^Follow-up (started|submitted for startup)\.$"))).to_be_visible()
    expect(section.get_by_text("pi · test-high", exact=True)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-high-route-started.png", animations="disabled")
    with session() as db:
        turn, = db.query(Chat).filter_by(parent_id=chat_id, input=instruction).all()
        assert turn.input == instruction
    expect(queue.get_by_role("list", name="Queued instructions")).to_have_count(0)
    lost_receipt.append(True)
    draft.fill("A follow-up to remove.")
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_text("Follow-up queued.", exact=True)).to_be_visible()
    expect(draft).to_have_value("")
    expect(queue.get_by_role("button", name="Retry queueing")).to_have_count(0)
    assert len([body for method, path, body in requests if method == "POST" and path == f"{task}/followups"]) == 3
    page.screenshot(path=EVIDENCE / f"{width}-recovered.png", animations="disabled")
    queue.get_by_role("button", name="Remove", exact=True).click()
    dialog = page.get_by_role("dialog", name="Remove queued follow-up")
    expect(dialog).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-remove.png", animations="disabled")
    dialog.get_by_role("button", name="Remove follow-up").click()
    expect(queue.get_by_text("Follow-up cancelled.", exact=True)).to_be_visible()
    assert [row["status"] for row in development_control.list_commands(chat_id)] == ["delivered", "cancelled"]
    assert all(not path.endswith("/execution/stop") and not path.endswith("/execution/stop-exact")
               for method, path, _ in requests if method == "POST")

    development_harness._update_execution(chat_id, status="completed")
    draft.fill("Review the original project evidence.")
    rebind_development_thread(
        chat_id, project_id=projects[1], board_key=None, board_provider=None, title="Reassigned follow-ups",
    )
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.get_by_text("Reassigned follow-ups", exact=True)).to_be_visible()
    expect(draft).to_have_value("Review the original project evidence.")
    expect(queue.get_by_role("button", name="Queue follow-up")).to_be_disabled()
    expect(queue.get_by_text("This draft was written for the previous project, board, or ticket.")).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-scope-changed.png", animations="disabled")
    assert len([body for method, path, body in requests if method == "POST" and path == f"{task}/followups"]) == 3
    queue.get_by_role("button", name="Use draft here").click()
    expect(queue.get_by_role("button", name="Queue follow-up")).to_be_enabled()
    expect(draft).to_have_value("Review the original project evidence.")
    page.screenshot(path=EVIDENCE / f"{width}-scope-reviewed.png", animations="disabled")
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_text("Follow-up queued.", exact=True)).to_be_visible()
    queued = development_control.list_commands(chat_id)[-1]
    assert queued["metadata"]["app_view_scope"][0] == projects[1]
    expect(draft).to_have_value("")

    drop_submission.append(True)
    draft.fill("This instruction belongs to the reassigned project.")
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_role("button", name="Retry queueing")).to_be_visible()
    rebind_development_thread(
        chat_id, project_id=project_id, board_key=None, board_provider=None, title="Original project follow-ups",
    )
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.get_by_text("Original project follow-ups", exact=True)).to_be_visible()
    expect(queue.get_by_text("Queued for an earlier project, board, or ticket.")).to_be_visible()
    expect(start).to_have_count(0)
    retry_previous = queue.get_by_role("button", name="Retry original submission")
    expect(retry_previous).to_be_enabled()
    expect(queue.get_by_role("button", name="Use draft here")).to_have_count(0)
    retry_previous.scroll_into_view_if_needed()
    page.screenshot(path=EVIDENCE / f"{width}-scope-retry.png", animations="disabled")
    retry_previous.click()
    expect(queue.get_by_role("alert")).to_contain_text("The Development thread changed.")
    expect(draft).to_have_value("This instruction belongs to the reassigned project.")
    expect(queue.get_by_role("button", name="Use draft here")).to_be_enabled()
    expect(queue.get_by_role("button", name="Queue follow-up")).to_be_disabled()
    submissions = [body for method, path, body in requests if method == "POST" and path == f"{task}/followups"]
    assert submissions[-2] == submissions[-1]
    assert submissions[-1]["expected_scope"][0] == projects[1]
    assert len(development_control.list_commands(chat_id)) == 3

    queue.get_by_role("button", name="Use draft here").click()
    small_edit = "Fix a typo in the button text."
    draft.fill(small_edit)
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_text("Follow-up queued.", exact=True)).to_be_visible()
    eligible = queue.get_by_role("listitem").filter(has_text=small_edit)
    held = queue.get_by_role("listitem").filter(has_text="Review the original project evidence.")
    expect(held.get_by_text("Queued for an earlier project, board, or ticket.")).to_be_visible()
    expect(held.get_by_role("button", name="Remove", exact=True)).to_be_enabled()
    run_eligible = eligible.get_by_role("button", name="Run next")
    expect(run_eligible).to_be_enabled()
    run_eligible.scroll_into_view_if_needed()
    page.screenshot(path=EVIDENCE / f"{width}-scope-eligible.png", animations="disabled")
    run_eligible.click()
    expect(queue.get_by_text(re.compile(r"^Follow-up (started|submitted for startup)\.$"))).to_be_visible()
    expect(page.locator(".av-messages").get_by_text(small_edit, exact=True)).to_be_visible()
    expect(section.get_by_text("pi · test-low", exact=True)).to_be_visible()
    expect(held).to_be_visible()
    assert development_control.list_commands_for_id(queued["id"])["status"] == "queued"
    with session() as db:
        assert db.query(Chat).filter_by(parent_id=chat_id, input=small_edit).count() == 1
        assert db.query(Chat).filter_by(parent_id=chat_id, input="Review the original project evidence.").count() == 0
    page.screenshot(path=EVIDENCE / f"{width}-scope-eligible-started.png", animations="disabled")

    lost_receipt.append(True)
    cancel_before_receipt.append(True)
    final_instruction = "Discard this instruction before it starts."
    submissions_before = sum(method == "POST" and path == f"{task}/followups" for method, path, _ in requests)
    draft.fill(final_instruction)
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_role("status").get_by_text("Follow-up was cancelled.", exact=True)).to_be_visible()
    expect(draft).to_have_value("")
    expect(queue.get_by_role("button", name="Retry queueing")).to_have_count(0)
    assert sum(method == "POST" and path == f"{task}/followups" for method, path, _ in requests) == submissions_before + 1
    assert any(method == "GET" and path.endswith("/receipt") for method, path, _ in requests)
    assert [row["status"] for row in development_control.list_commands(chat_id) if row["content"] == final_instruction] == ["cancelled"]
    page.screenshot(path=EVIDENCE / f"{width}-cancelled-receipt-recovered.png", animations="disabled")

    drop_submission.append(True)
    reload_instruction = "Verify the customer billing example."
    draft.fill(reload_instruction)
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_role("button", name="Retry queueing")).to_be_visible()
    writes_before_reload = [body for method, path, body in requests if method == "POST" and path == f"{task}/followups"]
    page.reload()
    expect(draft).to_be_visible()
    expect(draft).to_have_value(reload_instruction)
    retry_queueing = queue.get_by_role("button", name="Retry queueing")
    expect(retry_queueing).to_be_enabled()
    assert [body for method, path, body in requests if method == "POST" and path == f"{task}/followups"] == writes_before_reload
    page.screenshot(path=EVIDENCE / f"{width}-reload-retry-restored.png", animations="disabled")
    refuse_retry.append(True)
    retry_queueing.click()
    expect(queue.get_by_role("alert")).to_contain_text("The original submission is still unconfirmed.")
    expect(draft).to_have_value(reload_instruction)
    expect(retry_queueing).to_be_enabled()
    page.screenshot(path=EVIDENCE / f"{width}-reload-retry-rate-limited.png", animations="disabled")

    from datetime import timedelta
    with session() as db:
        root = db.get(Chat, chat_id)
        original_created_date = root.created_date
        root.created_date += timedelta(seconds=1)
        db.commit()
    retry_queueing.click()
    expect(draft).to_have_value("")
    expect(retry_queueing).to_have_count(0)
    assert not any(row["content"] == reload_instruction for row in development_control.list_commands(chat_id))
    with session() as db:
        db.get(Chat, chat_id).created_date = original_created_date
        db.commit()
    page.reload()
    expect(draft).to_have_value(reload_instruction)
    expect(retry_queueing).to_be_enabled()
    retry_queueing.click()
    expect(queue.get_by_text("Follow-up queued.", exact=True)).to_be_visible()
    expect(draft).to_have_value("")
    submissions = [body for method, path, body in requests if method == "POST" and path == f"{task}/followups"]
    assert submissions == [*writes_before_reload, *([writes_before_reload[-1]] * 3)]
    assert [row["status"] for row in development_control.list_commands(chat_id) if row["content"] == reload_instruction] == ["queued"]
    assert len(submissions[-1]["expected_thread_identity"]) == 64

    lost_receipt.append(True)
    cancel_before_receipt.append(True)
    hide_saved_receipt.append(True)
    reveal_on_next_connection.append(True)
    draft.fill("Cancel this before the next connection.")
    queue.get_by_role("button", name="Queue follow-up").click()
    expect(queue.get_by_role("button", name="Retry queueing")).to_be_visible()
    writes_before_reload = sum(method == "POST" and path == f"{task}/followups" for method, path, _ in requests)
    page.reload()
    expect(queue.get_by_text("Follow-up was cancelled.", exact=True)).to_be_in_viewport(ratio=1)
    expect(draft).to_have_value("")
    assert sum(method == "POST" and path == f"{task}/followups" for method, path, _ in requests) == writes_before_reload
    assert page.evaluate("Object.keys(sessionStorage).filter(key => key.startsWith('decisions:followup:')).length") == 0
    page.screenshot(path=EVIDENCE / f"{width}-reload-receipt-recovered.png", animations="disabled")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_followup_startup_rejection_restores_the_reviewed_item(page, monkeypatch, scoped_direct_ticket, fixture_base, width):
    from threading import Event

    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    scope = scoped_direct_ticket
    monkeypatch.setattr(development_control, "get_session", scope.session)
    instruction = "Review the queued proposal evidence."
    item = development_control.enqueue_command(
        scope.chat_id, instruction, source="app_view", source_ref="d" * 32,
        command_type="followup", idempotent=True, expected_project_id=scope.project_id,
    )
    probing, release, finished = Event(), Event(), Event()
    worker_calls = []
    run = development_harness._run_execution

    def probe(*args, **kwargs):
        probing.set()
        assert release.wait(15)
        return {"status": {"ready": True}}

    async def worker(**kwargs):
        try:
            await run(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(development_harness, "_run_execution", worker)
    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", probe)
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", lambda *args, **kwargs: worker_calls.append(args))
    app, router = FastAPI(), APIRouter()
    register_threads(router, None)
    register_controls(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    writes = []
    key = "d" * 64
    task = f"/api/workflows/studio/tasks/{scope.chat_id}"

    def relay(socket):
        def handle(raw):
            request = json.loads(raw)["body"]
            if request["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = request.get("command")
            reply = {"type": "remote_control_response", "command": command, "request_id": request.get("request_id")}
            data = {"screens": []} if command == "list_screens" else {}
            if command == "api_relay":
                method, path = request["data"]["method"], request["data"]["path"]
                status, result = 200, {}
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    result = {"status": "thread", "chat_id": scope.chat_id}
                elif path.startswith(f"/api/chats/{scope.chat_id}"):
                    result = {"title": "Queued proposal evidence", "project_id": scope.project_id, "messages": []}
                elif path.startswith(f"{task}/"):
                    response = client.request(method, path, json=request["data"].get("body"))
                    status, result = response.status_code, response.json()
                    if method == "POST":
                        writes.append((path, result))
                data = {"status": status, "response": result}
            socket.send(json.dumps({**reply, "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(f"{url}?channel=paired-test&proposal_key={key}")
    queue = page.locator(".av-development-queue")
    queue.get_by_text("Follow-ups (1)", exact=True).click()
    start = queue.get_by_role("button", name="Run next")
    expect(start).to_be_enabled()
    start.click()
    EVIDENCE.mkdir(exist_ok=True)
    try:
        expect(queue.get_by_text("Starting", exact=True)).to_be_visible()
        assert probing.wait(5)
        expect(queue.get_by_text("Follow-up submitted for startup.")).to_be_visible()
        expect(start).to_be_disabled()
        page.screenshot(path=EVIDENCE / f"{width}-followup-starting.png", animations="disabled")
        scope.move("board")
    finally:
        release.set()
        assert finished.wait(5)

    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(queue.get_by_text("Startup stopped. The follow-up remains queued for review.")).to_be_visible()
    expect(queue.get_by_text(instruction)).to_be_visible()
    expect(queue.get_by_text("Starting", exact=True)).to_have_count(0)
    expect(queue.get_by_text("Follow-up submitted for startup.")).to_have_count(0)
    expect(queue.get_by_role("button", name="Retry follow-up")).to_be_enabled()
    expect(page.get_by_role("region", name="Development execution").get_by_text("Failed", exact=True)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-followup-restored.png", animations="disabled")
    assert not worker_calls
    assert len(writes) == 1 and writes[0][1]["starting"] is True

    _install_api(page, [], current_board_linked=True, direct_execution=True, run_status="completed", terminal_session=False)

    def desktop_controls(route):
        saved = development_control.list_commands_for_id(item["id"])
        route.fulfill(json={"commands": [{**saved, "chat_id": 17}], "controls": {}, "interactions": []})

    page.route("**/api/workflows/studio/control-state?*", desktop_controls)

    def desktop_retry(route):
        response = client.post(f"{task}/followups/{item['id']}/start", json=route.request.post_data_json)
        assert response.status_code == 409
        route.fulfill(status=response.status_code, json=response.json())

    page.route(f"**/api/workflows/studio/tasks/17/followups/{item['id']}/start", desktop_retry)
    page.goto(f"{fixture_base}/development/threads/17/")
    desktop_queue = page.locator("#thread-command-queue")
    expect(desktop_queue.get_by_text(instruction)).to_be_visible()
    expect(desktop_queue.get_by_text("Startup stopped. The follow-up remains queued for review.")).to_be_visible()
    expect(desktop_queue.get_by_role("button", name="Retry follow-up")).to_be_enabled()
    page.screenshot(path=EVIDENCE / f"{width}-desktop-followup-restored.png", animations="disabled")
    desktop_queue.get_by_role("button", name="Retry follow-up").click()
    expect(desktop_queue.get_by_role("alert")).to_contain_text("changed before work started")
    expect(desktop_queue.get_by_text(instruction)).to_be_visible()
    page.screenshot(path=EVIDENCE / f"{width}-desktop-followup-retry-blocked.png", animations="disabled")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
@pytest.mark.parametrize("new_thread", [False, True], ids=["steer", "fork"])
def test_desktop_manual_queue_retries_same_destination(page, development_db, fixture_base, monkeypatch, width, new_thread):
    from threading import Event

    chat_id = development_db["chat_id"]
    finalized = _finish_simulated_direct_worker(development_db, monkeypatch)
    instruction = "Review the manual queue evidence."
    item = development_control.enqueue_command(chat_id, instruction, command_type="followup")
    probing, release = Event(), Event()
    ready = False

    def probe(*args, **kwargs):
        if not ready:
            probing.set()
            assert release.wait(15)
        return {"status": {"ready": ready}, "next_step": "Fixture worker unavailable"}

    monkeypatch.setattr("distr.core.project_cli_backends.catalog_probe.probe_cli_backend", probe)
    app, router = FastAPI(), APIRouter()
    register_controls(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    receipts = []
    page.set_viewport_size({"width": width, "height": 850})
    _install_api(page, [], current_board_linked=True, direct_execution=True, run_status="completed", terminal_session=False)
    source_url = f"{fixture_base}/development/threads/17/"

    def controls(route):
        saved = development_control.list_commands_for_id(item["id"])
        rows = [{**saved, "chat_id": 17}] if "chat_id=17" in route.request.url else []
        route.fulfill(json={"commands": rows, "controls": {}, "interactions": []})

    def launch(route):
        payload = route.request.post_data_json
        response = client.post(f"/api/workflows/studio/commands/{item['id']}/run", json=payload)
        result = response.json()
        assert response.status_code == 200, result
        assert payload["new_thread"] is new_thread and payload["expected_modified_at"]
        receipts.append(result)
        route.fulfill(json={**result, "chat_id": 88 if new_thread else 17})

    def fork(route):
        with development_db["session"]() as db:
            turns = db.query(Chat).filter_by(parent_id=receipts[-1]["chat_id"]).order_by(Chat.id).all()
            messages = [{"role": "user", "content": turn.input} for turn in turns]
        route.fulfill(json={
            "id": 88, "title": "Manual queue destination", "development": True, "project_id": 7,
            "development_board_key": "decisions:70", "messages": messages,
        })

    page.route("**/api/workflows/studio/control-state?*", controls)
    page.route(f"**/api/workflows/studio/commands/{item['id']}/run", launch)
    page.route("**/api/chats/88?*", fork)
    page.route("**/api/workflows/studio/tasks/88/execution", lambda route: route.fulfill(json={
        **development_harness.development_execution_state(receipts[-1]["chat_id"]), "chat_id": 88,
    }))
    for suffix in ("artifacts", "plans"):
        page.route(f"**/api/workflows/studio/tasks/88/{suffix}", lambda route: route.fulfill(json={"items": []}))
    page.goto(source_url)
    queue = page.locator("#thread-command-queue")
    EVIDENCE.mkdir(exist_ok=True)
    mode = "fork" if new_thread else "steer"
    try:
        queue.get_by_role("button", name="Start in a new thread" if new_thread else "Steer current thread").click()
        if new_thread:
            expect(page).to_have_url(f"{fixture_base}/development/threads/88/")
            expect(page.locator("#header-task")).to_have_text("Manual queue destination")
            page.screenshot(path=EVIDENCE / f"{width}-manual-fork-destination.png", animations="disabled")
            page.goto(source_url)
        expect(queue.get_by_text("Starting...", exact=True)).to_be_visible()
        assert probing.wait(5)
        expect(queue.get_by_role("button", name="Delete queued item")).to_be_disabled()
        page.mouse.move(1, 250)
        page.screenshot(path=EVIDENCE / f"{width}-manual-{mode}-starting.png", animations="disabled")
    finally:
        release.set()
        assert finalized.wait(5)

    page.reload()
    expect(queue.get_by_text(instruction)).to_be_visible()
    expect(queue.get_by_text("Startup stopped. The follow-up remains queued for review.")).to_be_visible()
    retry = queue.get_by_role("button", name="Retry follow-up")
    expect(retry).to_be_enabled()
    if new_thread:
        expect(queue.get_by_role("button", name="Start in a new thread")).to_have_count(0)
    page.mouse.move(1, 250)
    page.screenshot(path=EVIDENCE / f"{width}-manual-{mode}-restored.png", animations="disabled")
    ready = True
    finalized.clear()
    retry.click()
    if new_thread:
        expect(page).to_have_url(f"{fixture_base}/development/threads/88/")
    else:
        expect(queue).to_be_hidden()
    assert finalized.wait(5)
    assert len(receipts) == 2 and receipts[0]["chat_id"] == receipts[1]["chat_id"]
    assert development_control.list_commands_for_id(item["id"])["status"] == "delivered"


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_desktop_steer_rejects_a_stale_worker_identity(page, development_db, fixture_base, monkeypatch, width):
    from unittest.mock import Mock

    chat_id = development_db["chat_id"]
    item = development_control.enqueue_command(chat_id, "Keep this reviewed follow-up.", command_type="followup")
    replacement = {"direct": True, "status": "running", "runtime_id": "native", "turn_id": 202}
    stop = Mock()
    launch = Mock()
    monkeypatch.setattr(development_harness, "development_execution_state", lambda _: replacement)
    monkeypatch.setattr(development_harness, "stop_development_execution", stop)
    monkeypatch.setattr(development_harness, "dispatch_development_prompt", launch)
    app, router = FastAPI(), APIRouter()
    register_controls(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    posted = []
    page.set_viewport_size({"width": width, "height": 850})
    _install_api(page, [], current_board_linked=True, direct_execution=True, run_status="running", terminal_session=False)

    def controls(route):
        saved = development_control.list_commands_for_id(item["id"])
        route.fulfill(json={"commands": [{**saved, "chat_id": 17}], "controls": {}, "interactions": []})

    def execution(route):
        turn = 202 if posted else 101
        route.fulfill(json={**replacement, "turn_id": turn, "exact_stop": {"turn_id": turn}})

    def steer(route):
        payload = route.request.post_data_json
        posted.append(payload)
        response = client.post(f"/api/workflows/studio/commands/{item['id']}/run", json=payload)
        assert response.status_code == 409
        route.fulfill(status=response.status_code, json=response.json())

    page.route("**/api/workflows/studio/control-state?*", controls)
    page.route("**/api/workflows/studio/tasks/17/execution", execution)
    page.route(f"**/api/workflows/studio/commands/{item['id']}/run", steer)
    page.goto(f"{fixture_base}/development/threads/17/")
    queue = page.locator("#thread-command-queue")
    queue.get_by_role("button", name="Steer current thread").click()
    expect(queue.get_by_role("alert")).to_contain_text("The run changed.")
    expect(queue.get_by_text(item["content"], exact=True)).to_be_visible()
    expect(queue.get_by_role("button", name="Retry follow-up")).to_be_enabled()
    assert posted == [{"new_thread": False, "expected_modified_at": item["modified_at"],
                       "expected_stop": {"turn_id": 101}}]
    stop.assert_not_called()
    launch.assert_not_called()
    assert development_control.list_commands_for_id(item["id"])["status"] == "queued"
    EVIDENCE.mkdir(exist_ok=True)
    page.mouse.move(1, 250)
    page.screenshot(path=EVIDENCE / f"{width}-manual-steer-worker-changed.png", animations="disabled")
