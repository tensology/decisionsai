from pathlib import Path
from datetime import datetime, timezone
import json
import re
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api, URL
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_approved_proactive_turn_displays_its_reply_in_development(page, fixture_base, width):
    _install_api(page, [], current_board_linked=True, direct_execution=True)
    request = "Approved proactive project work. Inspect checkout validation."
    reply = "Checkout validation is fixed. The checkout regression tests passed."
    chat = {
        "id": 17, "title": "Implement ticket DEV-42", "project_id": 7,
        "development": True, "development_source_type": "proactive_orchestrator",
        "development_ticket_id": 42, "development_board_key": "decisions:70",
        "messages": [
            {"role": "user", "content": request, "chat_row_id": 19},
            {"role": "assistant", "content": reply, "chat_row_id": 19},
        ],
    }
    page.route("**/api/chats/17?**", lambda route: route.fulfill(json=chat))
    page.route("**/api/workflows/studio/tasks/17/execution", lambda route: route.fulfill(json={"direct": True}))
    page.set_viewport_size({"width": width, "height": 920})
    page.goto(f"{fixture_base}/development/threads/17/")
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    expect(page.locator(".studio-message.user").get_by_text(request, exact=True)).to_be_visible()
    expect(page.locator(".studio-message.assistant").get_by_text(reply, exact=True)).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    folder = Path("/tmp/decisions-proactive-handoff-verify")
    folder.mkdir(exist_ok=True)
    page.screenshot(path=str(folder / f"completed-{width}.png"))


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_active_proactive_progress_and_stop(page, fixture_base, width):
    _install_api(page, [], direct_execution=True, current_board_linked=True)
    state = {"id": "proactive-6", "origin": "proactive_orchestrator", "direct": True, "turn_id": 19,
             "status": "initializing", "can_stop": False, "summary": "Starting approved work."}
    stops = []

    def stop_worker(route):
        stops.append(route.request.post_data_json)
        state["can_stop"] = False
        route.fulfill(json={"stop_requested": True, "stopped": False})

    page.route("**/api/workflows/studio/tasks/17/execution", lambda route: route.fulfill(json=state))
    page.route("**/api/workflows/studio/tasks/17/execution/stop", stop_worker)
    page.set_viewport_size({"width": width, "height": 920})
    page.goto(f"{fixture_base}/development/threads/17/")
    stop = page.get_by_role("button", name="Stop", exact=True)
    activity = page.locator(".studio-message.active-turn")
    folder = Path("/tmp/decisions-proactive-activity-verify")
    folder.mkdir(exist_ok=True)

    expect(activity).to_contain_text("Starting approved work.")
    expect(stop).to_be_hidden()
    page.screenshot(path=str(folder / f"starting-{width}.png"))

    state.update(status="running", execution_session_id=91, can_stop=True, summary="Checking project files.")
    page.reload()
    expect(activity).to_contain_text("Checking project files.")
    expect(stop).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(folder / f"running-{width}.png"))
    stop.click()
    page.get_by_role("button", name="Stop agent", exact=True).click()
    expect(page.locator("#studio-toast")).to_have_text("Stop requested.")
    expect(stop).to_be_hidden()
    expect(activity).to_contain_text("Checking project files.")
    assert stops == [{"execution_session_id": 91}]
    page.screenshot(path=str(folder / f"stop-requested-{width}.png"))

    state.update(status="blocked", summary="Review the previous approval to recover its outcome.")
    page.reload()
    expect(activity).to_contain_text("Review the previous approval to recover its outcome.")
    expect(stop).to_be_hidden()
    expect(page.locator("#send-button")).to_be_disabled()
    expect(page.locator("#task-prompt")).to_be_editable()
    page.screenshot(path=str(folder / f"unconfirmed-{width}.png"))


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_proactive_handoff_refreshes_transcript_for_active_followup(page, fixture_base, width):
    requests = []
    _install_api(page, requests, current_board_linked=True, direct_execution=True)
    page.add_init_script("""
        const originalInterval = window.setInterval.bind(window);
        window.setInterval = (callback, delay, ...args) =>
            originalInterval(callback, delay === 6000 ? 200 : delay, ...args);
    """)
    prompt = {"role": "user", "content": "Approved checkout validation.", "chat_row_id": 19}
    reply = {"role": "assistant", "content": "Checkout validation passed.", "chat_row_id": 19}
    followup = {"role": "user", "content": "Check the configuration too.", "chat_row_id": 20}
    chat = {"id": 17, "title": "Implement ticket DEV-42", "project_id": 7,
            "development": True, "development_ticket_id": 42, "development_board_key": "decisions:70",
            "messages": [prompt]}
    execution = {"id": 91, "direct": True, "status": "running", "turn_id": 19,
                 "origin": "proactive_orchestrator", "summary": "Checking the checkout.", "can_stop": False}
    commands = [{"id": 62, "chat_id": 17, "status": "queued", "command_type": "followup", "content": followup["content"]}]
    page.route("**/api/chats/17?**", lambda route: route.fulfill(json=chat))
    page.route("**/api/workflows/studio/tasks/17/execution", lambda route: route.fulfill(json=execution))
    page.route(re.compile(r"/api/workflows/studio/control-state(?:\?|$)"), lambda route: route.fulfill(
        json={"commands": commands, "interactions": [], "channels": {}, "controls": {}},
    ))
    page.set_viewport_size({"width": width, "height": 920})
    page.goto(f"{fixture_base}/development/threads/17/")
    draft = page.locator("#task-prompt")
    expect(page.locator('[data-queued-id="62"]')).to_contain_text(followup["content"])
    expect(page.locator("#thread-command-queue")).to_contain_text("Follow-ups wait for the approved run.")
    expect(page.get_by_role("button", name="Steer current thread")).to_have_count(0)
    expect(page.get_by_role("button", name="Start in a new thread")).to_be_enabled()
    draft.fill("My next unsent draft")
    folder = Path("/tmp/decisions-proactive-queue-verify")
    folder.mkdir(exist_ok=True)
    page.screenshot(path=str(folder / f"queued-{width}.png"))

    chat["messages"] = [prompt, reply, followup]
    execution.update(id=92, turn_id=20, origin="development", summary="")
    execution.pop("can_stop")
    commands.clear()
    expect(page.locator(".studio-message.assistant").get_by_text(reply["content"], exact=True)).to_be_visible()
    expect(page.locator(".studio-message.user").get_by_text(followup["content"], exact=True)).to_have_count(1)
    expect(page.locator('[data-queued-id="62"]')).to_have_count(0)
    expect(draft).to_have_value("My next unsent draft")
    if width > 680:
        expect(page.locator("#stop-run-button")).to_be_visible()
    assert not any(method == "POST" and path.endswith("/messages") for method, path in requests)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(folder / f"started-{width}.png"))


def install_shared_workflow(page, *, waiting=False, workflow_message=True):
    _install_api(page, [], assistant_markdown=True, pending_interaction=waiting)
    status = 'waiting' if waiting else 'completed'
    chat = {
        'id': 17, 'title': 'Accepted current proposal', 'project_id': 7,
        'development': True, 'development_workflow_id': 44,
        'messages': [
            {'role': 'user', 'content': 'Execute the current proposal.', 'chat_row_id': 19},
            {'role': 'workflow', 'workflow_event': {'workflow_id': 44, 'run_id': 99, 'status': status, 'step_id': 1}},
            {'role': 'assistant', 'content': 'Current proposal result: TELEGRAM_PROPOSAL_E2E_OK.', 'chat_row_id': 20},
        ],
    }
    if not workflow_message:
        chat['messages'] = [message for message in chat['messages'] if message['role'] != 'workflow']
    execution = {'id': 99, 'direct': False, 'workflow_id': 44, 'status': status, 'current_step_id': 1,
                 'summary': 'Read-only check: pwd returned the registered project. printf printed TELEGRAM_PROPOSAL_E2E_OK.\n\n```\n/Users/example/development/WORK/CRYSTALLOGIC/www.registered-project.example\n```'}
    other = {'id': 98, 'workflow_id': 44, 'chat_id': 18, 'status': 'waiting', 'current_step_id': 1, 'waiting_kind': 'verification_review'}
    own = {'id': 99, 'workflow_id': 44, 'chat_id': 17, 'status': status, 'current_step_id': 1}
    page.route('**/api/chats/17?**', lambda route: route.fulfill(json=chat))
    page.route('**/api/workflows/studio/tasks/17/execution', lambda route: route.fulfill(json=execution))
    page.route('**/api/workflows/active-runs**', lambda route: route.fulfill(json=[other, own] if waiting else [other]))


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_workflow_completion_stays_above_a_newer_prompt(page, fixture_base, width):
    from distr.gui.web.routes.chat import _merge_thread_rows_with_tool_and_workflow_events

    install_shared_workflow(page)
    root = SimpleNamespace(params=json.dumps({'workflow_events': [{
        'id': 'late-completion', 'run_id': 99, 'workflow_id': 44, 'workflow_name': 'Checkout workflow',
        'type': 'completed', 'status': 'completed', 'turn_id': 19,
        'summary': 'Checkout workflow finished.', 'timestamp': '2026-11-11T12:05:00+00:00',
    }]}))
    original = 'Run the approved checkout workflow.'
    newer = 'Check the pricing screen next.'
    rows = [SimpleNamespace(id=turn_id, input=text, response=None, created_date=datetime(2026, 11, 11, 12, minute, tzinfo=timezone.utc))
            for turn_id, text, minute in [(19, original, 0), (20, newer, 3)]]
    chat = {
        'id': 17, 'title': 'Checkout follow-up', 'project_id': 7,
        'development': True, 'development_workflow_id': 44,
        'messages': _merge_thread_rows_with_tool_and_workflow_events(rows, root),
    }
    page.route('**/api/chats/17?**', lambda route: route.fulfill(json=chat))
    page.set_viewport_size({'width': width, 'height': 920})
    page.goto(f'{fixture_base}/development/threads/17/')
    disclosure = page.locator('details.turn-activity-disclosure > summary').filter(has_text='View workflow')
    expect(disclosure).to_have_count(1)
    expect(page.locator('.studio-message.user').get_by_text(newer, exact=True)).to_be_visible()
    ordering = page.evaluate("""() => {
        const prompts = document.querySelectorAll('.studio-message.user');
        const workflow = document.querySelector('details.turn-activity-disclosure');
        return Boolean(prompts[0].compareDocumentPosition(workflow) & Node.DOCUMENT_POSITION_FOLLOWING)
            && Boolean(workflow.compareDocumentPosition(prompts[1]) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    assert ordering
    folder = Path('/tmp/decisions-workflow-turn-trace')
    folder.mkdir(exist_ok=True)
    page.screenshot(path=folder / f'{width}-collapsed.png', animations='disabled')
    disclosure.click()
    expect(page.locator('[data-workflow-runner="done"]')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=folder / f'{width}.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_completed_chat_shows_its_actual_reply_with_other_chat_waiting_on_same_workflow(page, width):
    install_shared_workflow(page)
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(URL)
    page.locator('#studio-sidebar').evaluate("node => node.classList.remove('open')")
    reply = page.locator('.studio-message.assistant').get_by_text('Current proposal result: TELEGRAM_PROPOSAL_E2E_OK.', exact=True)
    expect(reply).to_be_visible()
    expect(reply).to_be_in_viewport()
    expect(page.locator('[data-workflow-runner="live"]')).to_have_count(0)
    expect(page.locator('[data-run-id="98"]')).to_have_count(0)
    expect(page.locator('#task-state')).to_contain_text('Ready')
    historical = page.locator('[data-workflow-runner="done"]')
    expect(historical).to_be_hidden()
    folder = Path('/tmp/decisions-shared-workflow-regression')
    folder.mkdir(exist_ok=True)
    page.screenshot(path=str(folder / f'completed-{width}.png'))
    page.locator('details.turn-activity-disclosure > summary').filter(has_text='View workflow').click()
    expect(historical).to_be_visible()


def test_matching_chat_human_review_remains_visible_and_uses_its_own_run(page):
    install_shared_workflow(page, waiting=True)
    page.goto(URL)
    own = page.locator('[data-workflow-runner="live"][data-run-id="99"]')
    expect(own).to_be_visible()
    expect(own.locator('.thread-workflow-checkpoint')).to_be_visible()
    expect(page.locator('[data-run-id="98"]')).to_have_count(0)
    expect(page.locator('#task-state')).to_contain_text('Waiting')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_completed_chat_without_workflow_messages_retains_keyboard_inspectable_own_history(page, width):
    install_shared_workflow(page, workflow_message=False)
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(URL)
    page.locator('#studio-sidebar').evaluate("node => node.classList.remove('open')")
    reply = page.locator('.studio-message.assistant').get_by_text('Current proposal result: TELEGRAM_PROPOSAL_E2E_OK.', exact=True)
    expect(reply).to_be_visible()
    expect(reply).to_be_in_viewport()
    summary = page.locator('details.turn-activity-disclosure > summary').filter(has_text='View workflow')
    expect(summary).to_have_count(1)
    own = page.locator('[data-workflow-runner="done"][data-run-id="99"]')
    expect(own).to_be_hidden()
    summary.focus()
    page.keyboard.press('Enter')
    expect(own).to_be_visible()
    expect(page.locator('[data-run-id="98"]')).to_have_count(0)
    own.locator('.thread-workflow-ring [data-wf-step]').first.click()
    dialog = page.locator('#thread-workflow-step-dialog')
    expect(dialog).to_be_visible()
    expect(dialog.locator('#thread-workflow-step-feedback')).not_to_contain_text('TELEGRAM_PROPOSAL_E2E_OK')
    page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    result = page.locator('[data-workflow-run-result="99"]')
    expect(result).to_contain_text('Run result')
    expect(result).to_contain_text('TELEGRAM_PROPOSAL_E2E_OK')
    expect(result).to_contain_text('pwd')
    assert result.evaluate('node => node.scrollWidth <= node.clientWidth'), 'Run result must contain long command output on mobile'
    summary.focus()
    page.keyboard.press('Enter')
    expect(own).to_be_hidden()
    expect(reply).to_be_visible()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_stopped_approval_explains_why_in_desktop_workflow_history(page, fixture_base, width):
    from distr.core.workflow.draft_launch import WORKFLOW_APPROVAL_SCOPE_CHANGED, approved_workflow_stop_reason

    install_shared_workflow(page, workflow_message=False)
    reason = approved_workflow_stop_reason('failed', {'approval_scope_changed': True})
    execution = {'id': 99, 'direct': False, 'workflow_id': 44, 'status': 'failed',
                 'current_step_id': 1, 'summary': reason}
    page.route('**/api/workflows/studio/tasks/17/execution', lambda route: route.fulfill(json=execution))
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(f'{fixture_base}/development/threads/17/')
    disclosure = page.locator('details.turn-activity-disclosure > summary').filter(has_text='View workflow')
    expect(disclosure).to_have_count(1)
    disclosure.click()
    result = page.locator('[data-workflow-run-result="99"]')
    expect(result.get_by_text(WORKFLOW_APPROVAL_SCOPE_CHANGED, exact=True)).to_be_visible()
    expect(page.locator('[data-run-id="98"]')).to_have_count(0)
    assert result.evaluate('node => node.scrollWidth <= node.clientWidth')
    folder = Path('/tmp/decisions-shared-workflow-regression')
    folder.mkdir(exist_ok=True)
    page.screenshot(path=folder / f'approval-stopped-{width}.png', animations='disabled')
