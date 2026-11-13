"""Render the exact Development checkpoint after another project fills the global window."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from tests.core.test_workflow_interactions import (
    _bind_moved_restart_ticket, _clear_interactions, crowded_development_checkpoints, paired_checkpoint, restart_checkpoint,
)
from tests.ui.test_development_harness_playwright import URL, _install_api, _open_details
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path("/tmp/decisions-checkpoint-scope")


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('surface', ['desktop', 'paired'])
@pytest.mark.parametrize('scenario', [
    'ready', 'blocked', 'moved', 'launch_failure', 'startup_failure', 'auto_startup_failure', 'damaged_recovery',
    'unreadable_scope',
])
def test_development_thread_reviews_interrupted_workflow(page, fixture_base, restart_checkpoint, surface, width, scenario,
                                                         monkeypatch):
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow import dispatcher, interactions

    scope = restart_checkpoint
    execution = "/api/workflows/studio/tasks/17/execution"
    title = "Review interrupted workflow"
    if scenario in {'moved', 'unreadable_scope'}:
        _bind_moved_restart_ticket(scope)
    if scenario == 'unreadable_scope':
        with scope.factory.begin() as db:
            run = db.get(AutoWorkflowRun, 20)
            run.status = 'running'
            run.run_data = '{"project_id":9,"execution_route":'
        dispatcher._cleanup_orphaned_runs_on_startup()
        dispatcher._auto_resume_interrupted_runs(background=False)
    if scenario == 'blocked':
        with scope.factory.begin() as db:
            worker = ProjectExecutionSession(project_id=9, workflow_id=10, run_id=20, step_id=30)
            db.add(worker)
            db.flush()
            db.add(ProjectExecutionEvent(session_id=worker.id, event_type="executor_start"))
    if scenario in {'launch_failure', 'startup_failure'}:
        save_worker = interactions._start_checkpoint_worker

        def fail_launch(**_):
            monkeypatch.setattr(interactions, '_start_checkpoint_worker', save_worker)
            raise RuntimeError("can't start new thread")

        if scenario == 'startup_failure':
            token = scope.client.get(execution).json()['checkpoint']['token']
            response = scope.client.post(execution + '/checkpoint', headers=scope.headers,
                                         json={'run_id': 20, 'token': token, 'action': 'continue'})
            assert response.json()['queued']
            scope.workers.clear()
        monkeypatch.setattr(interactions, '_start_checkpoint_worker', fail_launch)
        if scenario == 'startup_failure':
            unconfirmed, checkpoint_steps = interactions.recover_interrupted_checkpoint_decisions()
            dispatcher._auto_resume_interrupted_runs(background=False, unconfirmed_checkpoint_steps=unconfirmed,
                                                     checkpoint_steps=checkpoint_steps)
    if scenario == 'auto_startup_failure':
        start = Mock(side_effect=RuntimeError("can't start new thread"))
        with monkeypatch.context() as patch:
            patch.setattr(dispatcher, 'threading', SimpleNamespace(Thread=Mock(return_value=SimpleNamespace(start=start))))
            dispatcher._auto_resume_interrupted_runs()
        start.assert_called_once()
    if scenario == 'damaged_recovery':
        with scope.factory.begin() as db:
            run = db.get(AutoWorkflowRun, 20)
            data = json.loads(run.run_data)
            data['restart_recovery'] = {'auto_resume_counts': {'30': 'broken'}}
            run.run_data = json.dumps(data)
        dispatcher._auto_resume_interrupted_runs(background=False)
    page.set_viewport_size({"width": width, "height": 900})

    if surface == "desktop":
        _install_api(page, [], pending_interaction=True)
        page.route_web_socket("**/api/ws?*", lambda socket: socket.on_message(lambda _: None))
        page.route(f"**{execution}", lambda route: route.fulfill(json={
            **scope.client.get(execution).json(), "workflow_id": 44,
        }))

        def api(route):
            url = urlsplit(route.request.url)
            response = scope.client.request(
                route.request.method, url.path, params=parse_qs(url.query), json=route.request.post_data_json,
            )
            route.fulfill(status=response.status_code, json=response.json())

        page.route("**/api/workflows/studio/control-state?*", api)
        page.route("**/api/workflows/studio/interactions/*/resolve", api)
        page.goto(f"{fixture_base}/development/threads/17/")
        _open_details(page)
        panel = page.locator(".waiting-card")
        expect(panel.get_by_text(title, exact=True)).to_be_visible()
    else:
        remote = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
        if not remote:
            pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
        project = {"id": 9, "name": "Workflow test project"}
        chat = {"id": 17, "title": "Workflow test thread", "project_id": 9, "development": True, "messages": [],
                "development_board_key": "decisions:70"}
        reads = {
            "/api/tickets/boards": [{"id": 70, "name": project['name'], "default_project_id": 9}],
            "/api/projects": [project],
            "/api/projects/9": project,
            "/api/chats?surface=development": [chat],
            "/api/chats/17?surface=development": chat,
            "/api/workflows/studio/tasks/17/commands?include_terminal=false": {"items": []},
        }

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
                    path = call["path"]
                    status, result = 200, reads.get(path, {})
                    if path in {execution, execution + "/checkpoint"}:
                        response = scope.client.request(
                            call["method"], path, headers=scope.headers, json=call.get("body"),
                        )
                        status, result = response.status_code, response.json()
                    data = {"status": status, "response": result}
                socket.send(json.dumps({"type": "remote_control_response", "command": command,
                                        "request_id": frame["request_id"], "data": data}))
            socket.on_message(handle)

        page.route_web_socket("**/ws/telegram?*", relay)
        page.goto(f"{remote}?channel=paired-test")
        page.get_by_title("App View", exact=True).click()
        page.get_by_role("button", name="Boards", exact=True).click()
        page.get_by_role("button", name=project["name"], exact=True).click()
        page.get_by_role("button", name=chat["title"]).click()
        if scenario in {'moved', 'unreadable_scope'}:
            panel = page.get_by_role('region', name='Development execution')
            expect(panel.get_by_role('button', name=title, exact=True)).to_have_count(0)
        else:
            page.get_by_role("button", name=title, exact=True).click()
            panel = page.get_by_role("dialog", name=title)

    resume = panel.get_by_role("button", name="Resume saved step", exact=True)
    stop = panel.get_by_role("button", name="Stop workflow", exact=True)
    if scenario in {'moved', 'unreadable_scope'}:
        expected = ('no longer belongs to the saved board or project' if scenario == 'moved'
                    else 'The saved workflow scope could not be read.')
        expect(panel).to_contain_text(expected)
        expect(resume).to_have_count(0)
        expect(stop).to_have_count(0)
        expect(page.get_by_role('button', name='Stop', exact=True)).to_have_count(0)
    elif scenario in {'blocked', 'damaged_recovery'}:
        expect(stop).to_be_enabled()
        expected = 'already started' if scenario == 'blocked' else 'The saved recovery context is invalid.'
        expect(panel).to_contain_text(expected)
        expect(resume).to_have_count(0)
    else:
        expect(stop).to_be_enabled()
        expect(panel).to_contain_text(scope.prompt)
        expect(resume).to_be_enabled()
    if surface == 'desktop':
        expect(page.locator('#task-state b')).to_have_text('Interrupted')
        expect(page.locator('#stop-run-button')).to_be_hidden()
        expect(page.locator('#development-task-header .task-breadcrumb')).to_have_css('overflow-x', 'hidden')
        assert page.locator('#header-task').evaluate('''node => {
            const title = node.getBoundingClientRect();
            const breadcrumb = node.parentElement.getBoundingClientRect();
            return Math.min(title.right, breadcrumb.right) - title.left >= 24;
        }''')
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / f"{width}-{surface}-restart-{scenario}.png", animations="disabled")
    assert not scope.workers
    if scenario == 'launch_failure':
        resume.click()
        if surface == 'desktop':
            expect(page.get_by_text('Could not start the workflow decision. Please try again.', exact=True)).to_be_visible()
        else:
            expect(panel.get_by_text('Decision could not be confirmed.', exact=False)).to_be_visible()
        assert not scope.workers
        page.screenshot(path=EVIDENCE / f"{width}-{surface}-restart-retry.png", animations="disabled")
        if surface == 'paired':
            panel.get_by_role('button', name='Check checkpoint status').click()
        expect(resume).to_be_enabled()
    if scenario in {'ready', 'launch_failure', 'startup_failure', 'auto_startup_failure'}:
        resume.click()
        expect(page.get_by_text("Applying checkpoint decision", exact=False).first).to_be_visible()
        assert [worker["action"] for worker in scope.workers] == ["continue"]


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_development_inspector_keeps_its_checkpoint(page, crowded_development_checkpoints, width):
    scope = crowded_development_checkpoints
    page.set_viewport_size({"width": width, "height": 900})
    _install_api(page, [], pending_interaction=True)
    queries = []

    def read_control_state(route):
        url = urlsplit(route.request.url)
        query = parse_qs(url.query)
        response = scope.client.get(url.path, params=query)
        queries.append(query)
        route.fulfill(status=response.status_code, json=response.json())

    page.route("**/api/workflows/studio/control-state?*", read_control_state)
    page.goto(URL)
    _open_details(page)
    card = page.locator(".waiting-card")
    approve = card.get_by_role("button", name="Approve", exact=True)
    expect(approve).to_have_attribute("data-interaction-token", scope.own_token)
    assert {"chat_id": [str(scope.own_id)], "run_id": [str(scope.own_run)]} in queries
    EVIDENCE.mkdir(exist_ok=True)
    card.screenshot(path=EVIDENCE / f"{width}-own-checkpoint.png", animations="disabled")
    page.route("**/api/workflows/studio/interactions/*/resolve", lambda route: route.fulfill(json={"success": True}))
    with page.expect_request("**/api/workflows/studio/interactions/*/resolve") as submitted:
        approve.click()
    request = submitted.value
    assert request.method == "POST"
    assert urlsplit(request.url).path == f"/api/workflows/studio/interactions/{scope.own_token}/resolve"
    assert request.post_data_json["action"] == "approve"


@pytest.mark.parametrize(("width", "delivered"), [(390, True), (768, False), (1280, True), (1440, False)])
@pytest.mark.parametrize("scenario", ["response_loss", "restart", "revision_conflict"])
def test_paired_checkpoint_review_recovers_lost_decisions(page, paired_checkpoint, width, delivered, scenario, monkeypatch):
    url = os.environ.get("DECISIONS_REMOTE_APP_TEST_URL")
    if not url:
        pytest.skip("Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.")
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow import interactions

    scope = paired_checkpoint
    restart = scenario == "restart"
    conflict = scenario == "revision_conflict"
    advanced = conflict and delivered
    delivered = not conflict and (delivered or restart)
    execution = "/api/workflows/studio/tasks/17/execution"
    decision_path = execution + "/checkpoint"
    posts = []
    wait_for_check = False
    if conflict:
        assert interactions.authorize_telegram_checkpoint_delivery(token=scope.token, telegram_chat_id=123)
    frozen = scope.client.get(execution).json()

    def relay(socket):
        def handle(raw):
            nonlocal wait_for_check
            frame = json.loads(raw)["body"]
            if frame["type"] == "ping":
                socket.send(json.dumps({"type": "pong"}))
                return
            command = frame.get("command")
            reply = {"type": "remote_control_response", "command": command, "request_id": frame.get("request_id")}
            data = {"screens": []} if command == "list_screens" else {}
            status = 200
            if command == "api_relay":
                call = frame["data"]
                path = call["path"]
                if path.startswith("/api/workflows/studio/incoming/proposals/review?"):
                    data = {"status": "thread", "chat_id": 17}
                elif path == "/api/chats/17?surface=development":
                    data = {"title": "Delivery proposal", "project_id": 9, "messages": []}
                elif path == execution:
                    data = frozen if wait_for_check else scope.client.get(path).json()
                elif path == decision_path:
                    posts.append(call["body"])
                    if conflict:
                        winner = interactions.handle_telegram_workflow_reply(
                            "Run #20: Preserve the original receipt.", chat_id=123, background=True,
                        )
                        assert winner["queued"] and winner["run_id"] == 20
                        if advanced:
                            with scope.factory.begin() as db:
                                db.get(AutoWorkflowRun, 20).status = "running"
                    elif len(posts) == 1:
                        if delivered:
                            response = scope.client.post(path, json=call["body"], headers=scope.headers)
                            assert response.status_code == 200
                            wait_for_check = True
                        socket.send(json.dumps({**reply, "error": "Simulated lost checkpoint response"}))
                        return
                    response = scope.client.post(path, json=call["body"], headers=scope.headers)
                    status, data = response.status_code, response.json()
                data = {"status": status, "response": data}
            socket.send(json.dumps({**reply, "data": data}))
        socket.on_message(handle)

    page.route_web_socket("**/ws/telegram?*", relay)
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(f"{url}?channel=paired-checkpoint&proposal_key={'a' * 64}")
    region = page.get_by_role("region", name="Development execution")
    opener = region.get_by_role("button", name="Review checkpoint", exact=True)
    expect(opener).to_be_visible()
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / f"{width}-paired-waiting.png", animations="disabled")
    opener.click()
    dialog = page.get_by_role("dialog", name="Workflow checkpoint")
    expect(dialog.get_by_text(scope.prompt, exact=True)).to_be_visible()
    assert not posts and not scope.workers
    page.keyboard.press("Escape")
    expect(opener).to_be_focused()
    opener.click()
    action = "approve" if delivered else "feedback"
    guidance = "Keep the signed receipt with the result."
    submit = dialog.get_by_role("button", name="Approve" if delivered else "Submit revision", exact=True)
    if not delivered:
        expect(submit).to_be_disabled()
        dialog.get_by_label("Revision guidance").fill(guidance)
    page.screenshot(path=EVIDENCE / f"{width}-paired-review.png", animations="disabled")
    submit.click()
    body = {"run_id": 20, "token": scope.token, "action": action, "response_text": "" if delivered else guidance}
    if conflict:
        notice = region.get_by_text("A different revision was already accepted for this checkpoint.", exact=True)
        expect(notice).to_be_visible()
        expect(region.get_by_text("Running" if advanced else "Applying checkpoint decision", exact=True)).to_be_visible()
        expect(dialog).to_be_hidden()
        page.screenshot(path=EVIDENCE / f"{width}-revision-conflict.png", animations="disabled")
        view_revision = region.get_by_role("button", name="View your unsent revision", exact=True)
        expect(view_revision).to_be_focused()
        view_revision.click()
        unsent = page.get_by_role("dialog", name="Unsent revision")
        expect(unsent).to_be_visible()
        text = unsent.get_by_label("Revision guidance", exact=True)
        expect(text).to_have_value(guidance)
        expect(text).to_have_attribute("readonly", "")
        expect(unsent.get_by_text("Preserve the original receipt.", exact=True)).to_have_count(0)
        page.screenshot(path=EVIDENCE / f"{width}-unsent-revision.png", animations="disabled")
        page.keyboard.press("Escape")
        expect(view_revision).to_be_focused()
        view_revision.click()
        unsent.get_by_role("button", name="Dismiss notice").click()
        expect(notice).to_have_count(0)
        expect(region).to_be_focused()
        assert posts == [body]
        assert [worker["response_text"] for worker in scope.workers] == ["Preserve the original receipt."]
        return
    check = dialog.get_by_role("button", name="Check checkpoint status", exact=True)
    expect(check).to_be_visible()
    expect(check).to_be_focused()
    expect(submit).to_have_count(0)
    assert posts == [body]
    page.screenshot(path=EVIDENCE / f"{width}-paired-unconfirmed.png", animations="disabled")
    wait_for_check = False
    if restart:
        def interrupt_worker(**_):
            raise SystemExit("desktop restarted")

        monkeypatch.setattr(interactions, "_apply_standard_workflow_interaction", interrupt_worker)
        with pytest.raises(SystemExit, match="desktop restarted"):
            interactions._apply_claimed_interaction(**scope.workers[0])
        monkeypatch.setattr(interactions, "_APPLY_WORKER_ID", "restarted-desktop")
        assert interactions.recover_interrupted_checkpoint_decisions() == ({(20, 30)}, set())
    elif delivered:
        with scope.factory.begin() as db:
            db.get(AutoWorkflowRun, 20).status = "running"
    if check.is_visible():
        check.click()
    if restart:
        expect(region.get_by_text("Checkpoint decision unconfirmed", exact=True)).to_be_visible()
        expect(opener).to_have_count(0)
        assert posts == [body]
    elif delivered:
        expect(region.get_by_text("Running", exact=True)).to_be_visible()
        assert posts == [body]
    else:
        expect(submit).to_be_enabled()
        expect(dialog.get_by_role("button", name="Approve", exact=True)).to_be_focused()
        expect(dialog.get_by_label("Revision guidance")).to_have_value(guidance)
        assert posts == [body] and not scope.workers
        submit.click()
        expect(opener).to_have_count(0)
        expect(region.get_by_text("Applying checkpoint decision", exact=True)).to_be_visible()
        assert posts == [body, body]
    assert len(scope.workers) == 1 and scope.workers[0]["token"] == scope.token
    assert scope.workers[0]["action"] == action
    assert scope.client.get(execution.replace("/17/", "/18/")).json()["checkpoint"]["token"] == scope.other
    expect(dialog).to_be_hidden()
    ending = "restart-unconfirmed" if restart else "resolved"
    page.screenshot(path=EVIDENCE / f"{width}-paired-{ending}.png", animations="disabled")


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_desktop_shows_an_unconfirmed_checkpoint_without_a_retry_action(page, width):
    page.set_viewport_size({"width": width, "height": 900})
    _install_api(page, [], pending_interaction=True, checkpoint_decision_status="unconfirmed")
    page.goto(URL)
    card = page.locator('[data-workflow-runner="live"] .thread-workflow-checkpoint')
    expect(card.get_by_text("Checkpoint decision unconfirmed", exact=True)).to_be_visible()
    expect(card.get_by_role("button")).to_have_count(0)
    EVIDENCE.mkdir(exist_ok=True)
    card.screenshot(path=EVIDENCE / f"{width}-desktop-restart-card.png", animations="disabled")
    _open_details(page)
    inspector = page.locator(".waiting-card")
    expect(inspector.get_by_text("Checkpoint decision unconfirmed", exact=True)).to_be_visible()
    expect(inspector.get_by_role("status")).to_contain_text("Review this run before continuing.")
    expect(inspector.get_by_role("button")).to_have_count(0)
    inspector.screenshot(path=EVIDENCE / f"{width}-desktop-restart-inspector.png", animations="disabled")
