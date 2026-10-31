from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api, URL

pytestmark = pytest.mark.e2e_playwright


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
