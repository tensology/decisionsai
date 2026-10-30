"""Editing entry points use the existing editor without changing execution config."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_modules_playwright import BASE, install_plan

pytestmark = pytest.mark.e2e_playwright
OUTPUT = Path('/tmp/decisions-automation-edit')


def install(page):
    install_plan(page)
    page.add_init_script("""sessionStorage.setItem('decisions_automations_view', 'list');
        sessionStorage.setItem('decisions_automations_status_filter', 'all');
        window.WebSocket = class { static OPEN = 1; readyState = 1; };""")
    rows = [{
        'id': 'auto_2', 'name': 'Another automation', 'status': 'paused',
        'automation_type': 'scheduled_instruction', 'instruction': 'Another task',
        'schedule': {'kind': 'daily', 'time': '09:00'},
    }, {
        'id': 'auto_13', 'name': 'Availability monitor', 'status': 'paused',
        'automation_type': 'scheduled_instruction', 'instruction': 'Keep the availability alert rules.',
        'action_config': {'tool': 'hetzner_availability', 'args': {'automation_id': 'auto_13'},
                          'notification_policy': {'mode': 'on_new', 'channels': ['in_app']}},
        'schedule': {'kind': 'daily', 'time': '10:00', 'timezone': 'Africa/Johannesburg',
                     'ignored_occurrences': ['2026-10-30T10:00']},
    }]
    writes = []
    reject = [False]

    def api(route):
        req = route.request
        path = urlparse(req.url).path
        if req.method == 'PUT':
            writes.append(req.post_data_json)
            if reject[0]:
                route.fulfill(status=503, json={'detail': 'Save failed. Try again.'})
            else:
                rows[1].update(req.post_data_json)
                route.fulfill(json={'success': True, 'automation': rows[1]})
        elif req.method != 'GET':
            raise AssertionError('Unexpected mutation: ' + req.method + ' ' + path)
        elif path == '/api/automations':
            route.fulfill(json={'automations': rows})
        elif path.endswith('/presets'):
            route.fulfill(json={'presets': []})
        elif path.endswith('/runs'):
            route.fulfill(json={'runs': []})
        elif path == '/api/automations/auto_13':
            route.fulfill(json={'automation': rows[1], 'runs': []})
        else:
            route.fulfill(json={})
    page.route('**/api/automations**', api)
    return rows, writes, reject


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('entry', ['double_click', 'edit_button', 'context_menu'])
def test_edit_entry_selects_correct_automation_and_preserves_configuration(page: Page, width, entry):
    rows, writes, reject = install(page)
    original = deepcopy(rows[1])
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/development/automations/')
    row = page.locator('[data-scheduled-row="auto_13"]')
    expect(row).to_be_visible()
    OUTPUT.mkdir(exist_ok=True)
    if entry == 'double_click':
        page.screenshot(path=str(OUTPUT / f'list-{width}.png'))
        row.locator('.scheduled-row-title').dblclick()
    elif entry == 'edit_button':
        row.get_by_role('button', name='Edit automation Availability monitor', exact=True).click()
    else:
        row.locator('.scheduled-row-title').click(button='right')
        expect(page.get_by_role('menuitem', name='Edit automation')).to_be_focused()
        if width in [390, 1440]:
            page.screenshot(path=str(OUTPUT / f'menu-{width}.png'))
        page.keyboard.press('Escape')
        expect(page.locator('#scheduled-row-menu')).to_have_count(0)
        expect(row.locator('[data-row-action="edit"]')).to_be_focused()
        row.locator('.scheduled-row-title').click(button='right')
        page.get_by_role('menuitem', name='Edit automation').click()
    expect(page).to_have_url(BASE + '/automations/?automation=auto_13')
    expect(page.locator('#automation-name')).to_be_visible()
    expect(page.locator('#automation-name')).to_have_value(original['name'])
    expect(page.locator('#automation-instruction')).to_have_value(original['instruction'])
    if entry == 'double_click':
        page.screenshot(path=str(OUTPUT / f'editor-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#automation-name').fill('Edited availability monitor')
    page.locator('#automation-instruction').fill('Updated instruction, preserving alert settings.')
    reject[0] = True
    with page.expect_response(lambda response: response.request.method == 'PUT' and '/api/automations/auto_13' in response.url):
        page.locator('#automation-save').click()
    expect(page.locator('#automation-name')).to_have_value('Edited availability monitor')
    expect(page.locator('#automation-instruction')).to_have_value('Updated instruction, preserving alert settings.')
    expect(page.locator('#automation-snackbar')).to_contain_text('Save failed')
    if entry == 'double_click' and width in [390, 1440]:
        page.screenshot(path=str(OUTPUT / f'failed-save-{width}.png'))
    reject[0] = False
    with page.expect_response(lambda response: response.request.method == 'PUT' and '/api/automations/auto_13' in response.url):
        page.locator('#automation-save').click()
    expect(page.locator('#automation-detail-title')).to_have_text('Edited availability monitor')
    if entry == 'double_click' and width in [390, 1440]:
        page.screenshot(path=str(OUTPUT / f'saved-{width}.png'))
    assert rows[1]['action_config'] == original['action_config']
    assert 'action_config' not in writes[-1] or writes[-1]['action_config'] == original['action_config']
    assert writes[-1]['schedule'] == original['schedule']
    assert writes[-1]['automation_type'] == original['automation_type']
    assert not errors


def test_calendar_double_click_opens_editor_without_history_overlay(page: Page):
    install(page)
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.goto(BASE + '/development/automations/')
    page.locator('[data-scheduled-view="calendar"]').click()
    event = page.locator('[data-scheduled-calendar-event="auto_13"]').first
    expect(event).to_be_visible()
    event.dblclick()
    expect(page).to_have_url(BASE + '/automations/?automation=auto_13')
    expect(page.locator('#automation-name')).to_have_value('Availability monitor')
    expect(page.locator('#scheduled-history-modal')).to_have_count(0)


def test_tool_bound_rule_opens_shared_editor_and_retains_tool_type(page: Page):
    rows, writes, _ = install(page)
    rows[1]['automation_type'] = 'tool_action'
    original = deepcopy(rows[1])
    page.goto(BASE + '/development/automation-rules/')
    page.locator('[data-scheduled-row="auto_13"] [data-row-action="edit"]').click()
    expect(page.locator('#automation-name')).to_have_value(original['name'])
    page.locator('#automation-name').fill('Edited rule')
    with page.expect_response(lambda response: response.request.method == 'PUT' and '/api/automations/auto_13' in response.url):
        page.locator('#automation-save').click()
    assert writes[-1]['automation_type'] == 'tool_action'
    assert rows[1]['action_config'] == original['action_config']


def test_calendar_single_click_keeps_history_and_menu_can_edit(page: Page):
    install(page)
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.goto(BASE + '/development/automations/')
    page.locator('[data-scheduled-view="calendar"]').click()
    event = page.locator('[data-scheduled-calendar-event="auto_13"]').first
    event.click()
    expect(page.locator('#scheduled-history-modal')).to_be_visible()
    page.keyboard.press('Escape')
    event.click(button='right')
    page.locator('#scheduled-cal-menu').get_by_role('menuitem', name='Edit automation').click()
    expect(page.locator('#automation-name')).to_have_value('Availability monitor')
