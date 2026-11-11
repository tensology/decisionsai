"""Generated tasks open their existing ticket without discarding planning work."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_chat_header_playwright import ChatBackend
from tests.ui.test_plan_inline_edit_playwright import PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/decisions-plan-task-links')


def install(page):
    requests = []
    _install_api(page, requests)
    backend = ChatBackend()
    backend.assets = []
    backend.messages[3] = [{'id': 31, 'role': 'assistant', 'content': 'Review the generated work before starting it.',
        'build_result': {'tasks': [{'id': 42, 'title': 'Implement navigation', 'depends_on': [999]},
                                   {'id': 999, 'title': 'Moved prerequisite', 'parent_id': 42}]}}]
    page.route('**/api/**', backend.route)
    return requests


def shot(page, width, state):
    OUT.mkdir(exist_ok=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(OUT / f'{state}-{width}.png'))


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_existing_generated_ticket_opens_without_leaving_plan_and_failures_keep_draft(page, width):
    requests = install(page)
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(PLAN_URL)
    draft = page.get_by_role('textbox', name='Message', exact=True)
    draft.fill('Keep this planning instruction and its exact scope.')
    task = page.get_by_role('link', name='#42 Implement navigation', exact=True)
    expect(task).to_have_attribute('href', '/development/boards/decisions/70/kanban/?ticket_id=42')
    shot(page, width, 'links')
    def reject(route):
        route.fulfill(status=503, json={'detail': 'Ticket board unavailable'})
    page.route('**/api/tickets/boards/70', reject)
    task.click()
    expect(page.locator('.plan-task-link-status[role="alert"]')).to_contain_text('Ticket board unavailable')
    expect(draft).to_have_value('Keep this planning instruction and its exact scope.')
    expect(page.locator('#kanban-ticket-dialog')).not_to_be_visible()
    shot(page, width, 'unavailable')
    page.unroute('**/api/tickets/boards/70', reject)
    task.click()
    dialog = page.locator('#kanban-ticket-dialog')
    expect(dialog).to_be_visible()
    expect(page.locator('#kanban-ticket-id')).to_have_value('42')
    expect(page).to_have_url(PLAN_URL)
    shot(page, width, 'ticket')
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    expect(draft).to_have_value('Keep this planning instruction and its exact scope.')
    page.get_by_role('link', name='#999 Moved prerequisite', exact=True).click()
    expect(page.locator('.plan-task-link-status[role="alert"]')).to_contain_text('no longer on this board')
    assert page.locator('.plan-task-link-status[role="alert"]').evaluate('''n => {
      const box=n.getBoundingClientRect(), log=n.closest('[role="log"]').getBoundingClientRect();
      return box.top >= log.top - 1 && box.bottom <= log.bottom + 1;
    }''')
    expect(dialog).not_to_be_visible()
    shot(page, width, 'missing')
    assert not any(method != 'GET' for method, path in requests if '/tickets/' in path), 'Opening work must not start or modify it'


def test_late_ticket_lookup_does_not_open_over_a_different_workspace(page):
    install(page)
    page.goto(PLAN_URL)
    expect(page.get_by_role('link', name='#42 Implement navigation', exact=True)).to_be_visible()
    held = []
    page.route('**/api/tickets/boards/70', lambda route: held.append(route))
    page.get_by_role('link', name='#42 Implement navigation', exact=True).click()
    expect(page.locator('.plan-task-link-status')).to_have_text('Opening task...')
    page.locator('#sidebar-workflows-toggle').click()
    expect(page.locator('#workflow-workspace')).to_be_visible()
    assert len(held) == 1
    held[0].fulfill(json={'id': 70, 'lanes': [{'id': 1, 'name': 'Backlog', 'tickets': [{'id': 42, 'title': 'Do not open over workflows'}]}]})
    expect(page.locator('#kanban-ticket-dialog')).not_to_be_visible()
    expect(page).to_have_url(PLAN_URL.split('/boards/')[0] + '/workflows/')


def test_latest_task_selection_wins_over_an_earlier_lookup(page):
    install(page)
    page.goto(PLAN_URL)
    expect(page.get_by_role('link', name='#42 Implement navigation', exact=True)).to_be_visible()
    held = []
    page.route('**/api/tickets/boards/70', lambda route: held.append(route))
    page.get_by_role('link', name='#999 Moved prerequisite', exact=True).click()
    expect(page.locator('.plan-task-link-status')).to_have_text('Opening task...')
    page.get_by_role('link', name='#42 Implement navigation', exact=True).click()
    assert len(held) == 1
    with page.expect_request('**/api/tickets/boards/70'):
        held[0].fulfill(json={'id': 70, 'lanes': [{'id': 1, 'name': 'Backlog', 'tickets': [{'id': 999, 'title': 'Earlier selection'}]}]})
    expect(page.locator('#kanban-ticket-dialog')).not_to_be_visible()
    assert len(held) == 2
    held[1].fulfill(json={'id': 70, 'lanes': [{'id': 1, 'name': 'Backlog', 'tickets': [{'id': 42, 'title': 'Latest selection'}]}]})
    expect(page.locator('#kanban-ticket-dialog')).to_be_visible()
    expect(page.locator('#kanban-ticket-id')).to_have_value('42')
    expect(page.locator('#kanban-ticket-title')).to_have_value('Latest selection')


@pytest.mark.parametrize('ticket_id,exists', [(42, True), (999, False)])
def test_copied_task_link_opens_exact_ticket_or_explains_missing_scope(page, ticket_id, exists):
    install(page)
    target = PLAN_URL.replace('/plan/', f'/kanban/?ticket_id={ticket_id}')
    page.goto(target)
    dialog = page.locator('#kanban-ticket-dialog')
    if exists:
        expect(dialog).to_be_visible()
        expect(page.locator('#kanban-ticket-id')).to_have_value(str(ticket_id))
    else:
        expect(page.locator('#studio-toast')).to_contain_text('no longer on this board')
        expect(dialog).not_to_be_visible()
    expect(page.locator('#kanban-workspace')).to_be_visible()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_plan_ticket_run_errors_are_visible_and_retry_does_not_duplicate(page, width):
    install(page)
    page.set_viewport_size({'width': width, 'height': 1000})
    held = []
    page.route('**/api/tickets/tickets/42/send-to-cli', lambda route: held.append(route))
    page.goto(PLAN_URL)
    draft = page.get_by_role('textbox', name='Message', exact=True)
    draft.fill('Preserve the planning scope.')
    page.get_by_role('link', name='#42 Implement navigation', exact=True).click()
    button = page.locator('#kanban-ticket-run-agent')
    status = page.locator('#kanban-ticket-action-status')
    button.click()
    expect(button).to_be_disabled()
    expect(status).to_have_text('Sending ticket to Codex...')
    button.dispatch_event('click')
    assert len(held) == 1
    held.pop().fulfill(json={'success': False, 'error': 'Prerequisite #999 must be accepted as Complete before this task can run.', 'message': 'Ticket sent to Codex.'})
    expect(status).to_have_attribute('role', 'alert')
    expect(status).to_contain_text('Prerequisite #999')
    expect(button).to_be_enabled()
    expect(page.locator('#studio-toast')).not_to_contain_text('Ticket sent to Codex.')
    assert status.evaluate('n => { const b=n.getBoundingClientRect(); return b.top >= 0 && b.bottom <= innerHeight; }')
    shot(page, width, 'blocked-run')
    button.click()
    assert len(held) == 1
    held.pop().fulfill(status=409, json={'detail': 'Prerequisite #999 is still running.'})
    expect(status).to_have_text('Prerequisite #999 is still running.')
    expect(button).to_be_enabled()
    shot(page, width, 'blocked-retry')
    page.keyboard.press('Escape')
    expect(draft).to_have_value('Preserve the planning scope.')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_copied_task_link_preserves_board_error_when_lookup_fails(page, width):
    requests = install(page)
    page.set_viewport_size({'width': width, 'height': 1000})
    def unavailable(route):
        route.fulfill(status=503, json={'detail': 'Ticket board unavailable'})

    page.route('**/api/tickets/boards/70', unavailable)
    target = PLAN_URL.replace('/plan/', '/kanban/?ticket_id=42')
    page.goto(target)
    expect(page.locator('#studio-toast')).to_contain_text('Could not load')
    expect(page.locator('#studio-toast')).not_to_contain_text('no longer on this board')
    expect(page.locator('#kanban-ticket-dialog')).not_to_be_visible()
    expect(page.locator('#kanban-workspace')).to_be_visible()
    expect(page.locator('#development-kanban-lanes')).to_contain_text('Could not load this board')
    expect(page.locator('#development-kanban-lanes')).not_to_contain_text('Loading board...')
    shot(page, width, 'copied-unavailable')
    page.unroute('**/api/tickets/boards/70', unavailable)
    page.reload()
    expect(page).to_have_url(target)
    expect(page.locator('#kanban-ticket-dialog')).to_be_visible()
    expect(page.locator('#kanban-ticket-id')).to_have_value('42')
    expect(page.locator('#development-kanban-lanes')).not_to_contain_text('Could not load this board')
    shot(page, width, 'copied-recovered')
    assert not any(method != 'GET' for method, path in requests if '/tickets/' in path)
