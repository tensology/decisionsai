"""Visible Incoming operations against real UI with isolated APIs."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-incoming-progress')


def install(page, base, *, failure=False):
    _install_api(page, [], current_board_linked=True, direct_execution=True, terminal_session=False)
    page.route('**/assets/img/bolt.png', lambda route: route.fulfill(
        content_type='image/png', body=(Path(__file__).resolve().parents[2] / 'assets/img/bolt.png').read_bytes()))
    rows = [{'id': f'local:whatsapp:request-{i}', 'origin': 'local', 'source': 'whatsapp',
             'project_id': 7, 'project_name': 'DecisionsAI', 'title': f'Review request {i}',
             'description': 'Keep the source files and transcripts.', 'message_ids': [f'm{i}']}
            for i in (1, 2)]
    state = {'calls': [], 'rows': rows, 'checks': {'whatsapp': {'source_state': 'queued', 'recalibration': {'state': 'queued'}}}, 'check_calls': [], 'check_failure': False}

    def check(route):
        state['check_calls'].append(route.request.method)
        if state['check_failure']:
            return route.fulfill(status=503, json={'error': 'Source check unavailable. Please retry.'})
        return route.fulfill(json={'success': True, 'checks': state['checks']})

    page.route('**/api/workflows/studio/incoming/check', check)

    def handler(route):
        path = urlsplit(route.request.url).path
        if path.endswith(('/proposals/merge', '/proposals/thread')):
            state['calls'].append((path, route.request.post_data_json))
            if failure:
                return route.fulfill(status=503, json={'error': 'Preparation failed. Try again.'})
            state['rows'] = []
            return route.fulfill(json={'chat_id': 17, 'success': True})
        if path.endswith('/incoming/proposals'):
            return route.fulfill(json={'proposals': state['rows'], 'sync': {'state': 'complete'}})
        return route.fallback()

    page.route('**/api/workflows/studio/incoming/proposals**', handler)
    page.add_init_script("""const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            if (/proposals\\/(merge|thread)$/.test(String(args[0]))) await new Promise(resolve => setTimeout(resolve, 1200));
            return originalFetch(...args);
        };""")
    page.goto(base + '/development/incoming/')
    expect(page.locator('.proposal-card')).to_have_count(2)
    return state


def drop(page):
    page.locator('.proposal-card').first.evaluate("""source => {
        const transfer = new DataTransfer();
        source.dispatchEvent(new DragEvent('dragstart', {bubbles:true,dataTransfer:transfer}));
        document.querySelectorAll('.proposal-card')[1].dispatchEvent(new DragEvent('drop', {bubbles:true,dataTransfer:transfer}));
    }""")


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('operation', ['merge', 'thread'])
def test_progress_precedes_navigation_and_check_action_stays_visible(page, fixture_base, width, operation):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install(page, fixture_base)
    check = page.get_by_role('button', name='Check incoming', exact=True)
    expect(check).to_be_visible()
    assert check.locator('..').locator('#incoming-dismissed-toggle').count() == 1
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'incoming-{width}.png'))
    if operation == 'merge':
        drop(page)
    else:
        page.locator('[data-incoming-proposal-thread]').first.click()
    modal = page.get_by_role('dialog').filter(has=page.locator('progress'))
    expect(modal).to_be_visible()
    expect(modal).to_contain_text('Merging proposals' if operation == 'merge' else 'Preparing thread')
    expect(modal.locator('progress')).not_to_have_attribute('value', '100')
    expect(page.get_by_role('button', name='Close progress')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.get_by_role('button', name='Close progress')).to_be_focused()
    bounds = modal.bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'{operation}-{width}.png'))
    expect(page).to_have_url(fixture_base + '/development/threads/17/')
    expect(modal).to_have_count(0)
    assert len(state['calls']) == 1
    assert page.evaluate('document.body.style.overflow') != 'hidden'


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_failed_merge_keeps_both_sources_and_retry_enabled(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install(page, fixture_base, failure=True)
    drop(page)
    expect(page.get_by_role('dialog')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(page.locator('#incoming-operation-status')).to_contain_text('Merging')
    expect(page.locator('#studio-toast')).to_contain_text('Preparation failed')
    expect(page.locator('.proposal-card')).to_have_count(2)
    expect(page.locator('[data-incoming-proposal-thread]').first).to_be_enabled()
    expect(page.locator('#incoming-operation-status')).to_be_hidden()
    page.screenshot(path=str(SHOTS / f'merge-error-{width}.png'))
    drop(page)
    expect(page.get_by_role('dialog')).to_be_visible()
    expect(page.locator('#studio-toast')).to_contain_text('Preparation failed')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['calls']) == 2


def test_opening_whatsapp_acknowledges_only_current_messages(page, fixture_base):
    install(page, fixture_base)
    now = datetime.now(timezone.utc).isoformat()
    items = [{'key': f'whatsapp:{i}', 'source': 'whatsapp', 'source_thread_id': f'contact-{i}',
              'source_message_id': f'message-{i}', 'created_at': now, 'sender': f'Contact {i}',
              'conversation_label': f'Contact {i}', 'text': 'Review this request', 'processed': False}
             for i in (1, 2)]
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={'items': items, 'links': [], 'channels': {}}))
    page.reload()
    page.locator('[data-incoming-filter="whatsapp"]').click()
    second = page.locator('[data-incoming-conversation="whatsapp:contact-2"]')
    expect(second.locator('.incoming-new-dot')).to_have_count(1)
    second.click()
    expect(second.locator('.incoming-new-dot')).to_have_count(0)
    page.reload()
    page.locator('[data-incoming-filter="whatsapp"]').click()
    expect(second.locator('.incoming-new-dot')).to_have_count(0)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_explicit_check_queues_once_and_reports_ai_progress_separately(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    state = install(page, fixture_base)
    assert state['check_calls'] == []
    button = page.get_by_role('button', name='Check incoming', exact=True)
    button.click()
    status = page.locator('#incoming-check-status')
    expect(status).to_contain_text('Checking sources and proposal project assignments')
    page.screenshot(path=str(SHOTS / f'checking-{width}.png'))
    state['checks'] = {'whatsapp': {'source_state': 'complete', 'recalibration': {'state': 'complete', 'updated': 2}}}
    expect(status).to_have_text('Incoming checked. 2 proposals updated.', timeout=10_000)
    page.screenshot(path=str(SHOTS / f'checked-{width}.png'))
    assert state['check_calls'] == ['POST', 'GET']
    state['check_failure'] = True
    button.click()
    expect(status).to_contain_text('Source check unavailable')
    page.screenshot(path=str(SHOTS / f'check-error-{width}.png'))
    expect(page.locator('.proposal-card')).to_have_count(2)
