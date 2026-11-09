"""Canonical task completion and honest cross-app synchronization feedback."""
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-proposal-sync')


def _open(page, fixture_base, *, sync=None, fail_save=False, local=False):
    page.set_default_timeout(10_000)
    SHOTS.mkdir(parents=True, exist_ok=True)
    proposal = {
        'id': 'tensology:94', 'origin': 'tensology', 'source': 'gmail',
        'title': 'Check linked project synchronization', 'description': 'Fixture description.',
        'project_id': 7, 'project_name': 'DecisionsAI', 'message_ids': ['fixture-message'],
        'tasks': ['Review source project', 'Verify the shared proposal'],
        'task_completed_indexes': [1], 'updated_at': '2026-11-09T09:00:00Z',
    }
    if local:
        proposal = {**proposal, 'id': 'local:gmail:fixture', 'origin': 'local'}
    state = {'proposal': proposal, 'writes': [], 'list_requests': [], 'sync': sync or {'state': 'complete'}, 'fail_save': fail_save}
    _install_api(page, [])

    def handler(route):
        request = route.request
        path = urlparse(request.url).path
        if path.endswith('/proposals/details'):
            body = request.post_data_json
            state['writes'].append(body)
            if state['fail_save']:
                return route.fulfill(status=409, json={'error': 'This proposal changed. Refresh Incoming and retry.'})
            state['proposal'] = {**state['proposal'], 'id': 'tensology:94', 'origin': 'tensology', 'task_completed_indexes': body['task_completed_indexes'],
                                 'updated_at': '2026-11-09T10:00:00Z'}
            return route.fulfill(json={'success': True, 'proposal': state['proposal']})
        if path.endswith('/incoming/proposals'):
            state['list_requests'].append(request.url)
            return route.fulfill(json={'proposals': [state['proposal']], 'sync': state['sync']})
        if path.endswith('/proposals/files/ensure'):
            return route.fulfill(json={'attachments': [], 'failures': []})
        route.fallback()

    page.route('**/api/workflows/studio/incoming/proposals**', handler)
    page.add_init_script("""localStorage.setItem('incoming.proposal.tasks.done:tensology:94', '[0]');
        const originalFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            if (String(args[0]).includes('/proposals/details') || String(args[0]).includes('refresh=1')) await new Promise(resolve => setTimeout(resolve, 500));
            return originalFetch(...args);
        };""")
    page.goto(f'{fixture_base}/development/incoming/', wait_until='domcontentloaded')
    expect(page.locator('.proposal-card')).to_have_count(1)
    return state


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_task_completion_waits_for_server_and_survives_reopen(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 900})
    state = _open(page, fixture_base)
    expect(page.locator('#incoming-proposal-sync-status')).to_have_text('Synchronized with Tensology.')
    page.locator('.proposal-title').click()
    page.locator('[data-proposal-tab="tasks"]').click()
    toggles = page.locator('[data-proposal-task-toggle]')
    expect(toggles.nth(0)).to_have_attribute('aria-checked', 'false')
    expect(toggles.nth(1)).to_have_attribute('aria-checked', 'true')
    toggles.nth(0).click()
    expect(toggles.nth(0)).to_be_disabled()
    expect(toggles.nth(0)).to_have_attribute('aria-checked', 'false')
    page.wait_for_timeout(200)  # Inspect settled tab styling while the mocked write is pending.
    page.screenshot(path=str(SHOTS / f'tasks-saving-{width}.png'))
    expect(page.locator('[data-proposal-task-save-status]')).to_have_text('Saved')
    expect(toggles.nth(0)).to_have_attribute('aria-checked', 'true')
    assert state['writes'] == [{'proposal_id': 'tensology:94', 'message_ids': ['fixture-message'],
                                'expected_updated_at': '2026-11-09T09:00:00Z', 'task_completed_indexes': [0, 1]}]
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'tasks-saved-{width}.png'))
    page.keyboard.press('Escape')
    page.locator('.proposal-title').click()
    page.locator('[data-proposal-tab="tasks"]').click()
    expect(page.locator('[data-proposal-task-toggle]').nth(0)).to_have_attribute('aria-checked', 'true')
    page.locator('[data-proposal-task-toggle]').nth(1).click()
    expect(page.locator('[data-proposal-task-save-status]')).to_have_text('Saved')
    assert state['writes'][-1]['expected_updated_at'] == '2026-11-09T10:00:00Z'


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_task_save_conflict_preserves_server_state_and_can_retry(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 900})
    state = _open(page, fixture_base, fail_save=True)
    page.locator('.proposal-title').click()
    page.locator('[data-proposal-tab="tasks"]').click()
    toggle = page.locator('[data-proposal-task-toggle]').nth(0)
    toggle.click()
    expect(page.locator('[data-proposal-task-save-status]')).to_contain_text('This proposal changed.')
    expect(toggle).to_have_attribute('aria-checked', 'false')
    expect(toggle).to_be_enabled()
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'tasks-error-{width}.png'))
    state['fail_save'] = False
    toggle.focus()
    page.keyboard.press('Space')
    expect(toggle).to_have_attribute('aria-checked', 'true')
    expect(toggle).to_be_focused()
    assert len(state['writes']) == 2


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_resync_displays_incomplete_without_hiding_proposals(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 900})
    state = _open(page, fixture_base, sync={'state': 'partial', 'error': 'Tensology is unavailable.',
                                          'conflicts': [{'proposal_id': 'local:fixture', 'reason': 'Project not linked.'}]})
    status = page.locator('#incoming-proposal-sync-status')
    expect(status).to_contain_text('Synchronization incomplete.')
    expect(status).to_contain_text('Tensology is unavailable.')
    expect(status).to_contain_text('1 proposal could not be synchronized.')
    expect(page.locator('.proposal-card')).to_have_count(1)
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'sync-incomplete-{width}.png'))
    state['sync'] = {'state': 'complete', 'uploaded': 1, 'updated': 1, 'conflicts': [], 'error': ''}
    page.locator('#incoming-resync').click()
    expect(status).to_have_text('Checking proposal synchronization...')
    page.wait_for_timeout(200)  # Allow the existing error-color transition to settle.
    page.screenshot(path=str(SHOTS / f'sync-pending-{width}.png'))
    expect(status).to_have_text('Synchronized with Tensology.')
    page.screenshot(path=str(SHOTS / f'sync-complete-{width}.png'))


def test_first_local_task_save_tracks_canonical_id_for_following_edits(page, fixture_base):
    state = _open(page, fixture_base, local=True)
    page.locator('.proposal-title').click()
    page.locator('[data-proposal-tab="tasks"]').click()
    page.locator('[data-proposal-task-toggle]').nth(0).click()
    expect(page.locator('[data-proposal-task-save-status]')).to_have_text('Saved')
    page.locator('[data-proposal-task-toggle]').nth(1).click()
    expect(page.locator('[data-proposal-task-save-status]')).to_have_text('Saved')
    assert len(state['writes']) == 2
    assert state['writes'][0]['proposal_id'] == 'local:gmail:fixture'
    assert state['writes'][1]['proposal_id'] == 'tensology:94'
    assert state['writes'][1]['expected_updated_at'] == '2026-11-09T10:00:00Z'


@pytest.mark.parametrize('status', ['proposal_sync', 'reconciled', 'proposal_updated'])
def test_sync_completion_push_reads_cache_without_restarting_triage(page, fixture_base, status):
    page.add_init_script("""window.proposalSockets = [];
        class TestSocket {
            static OPEN = 1;
            constructor(url) { this.url = url; this.readyState = 0; this.sent = [];
                window.proposalSockets.push(this);
                setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0); }
            send(value) { this.sent.push(JSON.parse(value)); }
            close() { this.readyState = 3; }
        }
        window.WebSocket = TestSocket;""")
    page.set_viewport_size({'width': 1280, 'height': 900})
    state = _open(page, fixture_base, sync={'state': 'partial', 'error': 'Synchronization is pending.'})
    page.wait_for_function("window.proposalSockets.some(socket => socket.sent.some(message => message.subscribe_topic === 'incoming'))")
    state['list_requests'].clear()
    state['sync'] = {'state': 'complete', 'conflicts': [], 'error': ''}
    page.evaluate("""status => {
        const socket = window.proposalSockets.find(item => item.sent.some(message => message.subscribe_topic === 'incoming'));
        socket.onmessage({data: JSON.stringify({type: 'proposal_changed', status})});
    }""", status)
    expect(page.locator('#incoming-proposal-sync-status')).to_have_text('Synchronized with Tensology.')
    assert state['list_requests'], 'The completion event must refresh the displayed cached result.'
    assert not any('refresh=1' in url for url in state['list_requests']), 'Completion must not restart triage.'
    expect(page.locator('#incoming-resync')).to_be_enabled()
    expect(page.locator('.proposal-card')).to_have_count(1)
    page.screenshot(path=str(SHOTS / f'sync-push-{status}.png'))
