"""Legacy embedded Board responsive checks, with all Board API requests isolated."""
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e_playwright
OUTPUT = Path('/tmp/decisions-development-responsive/legacy-board')


def install_board_api(page):
    ticket = {'id': 'ticket-1', 'title': 'Review the responsive implementation with a descriptive ticket title',
              'description': 'Verify the project board, metadata, and ticket form on phone and desktop widths.',
              'assignee': 'Project reviewer', 'due_date': '2026-10-29', 'priority': 'high',
              'tags': ['review', 'responsive'], 'time_estimate': '2h', 'column_id': 'todo'}
    board = {'provider': 'local', 'project_name': 'Responsive implementation review', 'columns': [
        {'id': 'todo', 'name': 'To do', 'tickets': [ticket]},
        {'id': 'done', 'name': 'Done', 'tickets': []},
    ]}
    requests = []
    def handler(route):
        requests.append((route.request.method, route.request.url))
        if route.request.method != 'GET':
            route.fulfill(status=405, json={'detail': 'Fixture is read only'})
        elif '/api/tickets/ticket-1' in route.request.url:
            route.fulfill(json=ticket)
        elif '/api/board?' in route.request.url:
            route.fulfill(json=board)
        else:
            route.fulfill(status=404, json={'detail': 'Fixture endpoint unavailable'})
    page.route('**/board/api/**', handler)
    return requests


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_legacy_board_and_ticket_form_fit(page: Page, width):
    page.set_default_timeout(5000)
    requests = install_board_api(page)
    page.set_viewport_size({'width': width, 'height': 900})
    response = page.goto('http://127.0.0.1:8765/board/?project_id=7')
    assert response.status == 200
    expect(page.locator('.ticket-card')).to_be_visible()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / f'board-{width}.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='Edit ticket', exact=True).click()
    expect(page.locator('#ticketModal')).to_have_class('modal active')
    expect(page.locator('#ticketTitle')).to_have_value('Review the responsive implementation with a descriptive ticket title')
    expect(page.locator('#ticketTitle')).to_be_focused()
    expect(page.locator('.decisions-datetime-value')).to_contain_text('2026')
    page.get_by_role('button', name='Close ticket', exact=True).focus()
    page.keyboard.press('Shift+Tab')
    expect(page.get_by_role('button', name='Save', exact=True)).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.get_by_role('button', name='Close ticket', exact=True)).to_be_focused()
    page.screenshot(path=str(OUTPUT / f'ticket-{width}.png'), full_page=True)
    assert page.locator('.modal-content').evaluate('node => node.getBoundingClientRect().right <= innerWidth && node.getBoundingClientRect().left >= 0')
    page.locator('.cancel-btn').click()
    expect(page.locator('#ticketModal')).not_to_have_class('modal active')
    expect(page.get_by_role('button', name='Edit ticket', exact=True)).to_be_focused()
    page.get_by_role('button', name='Edit ticket', exact=True).click()
    expect(page.locator('#ticketTitle')).to_be_focused()
    page.keyboard.press('Escape')
    expect(page.locator('#ticketModal')).not_to_have_class('modal active')
    assert all(method == 'GET' for method, _ in requests)
