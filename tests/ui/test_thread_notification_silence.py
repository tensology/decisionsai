"""Background Development WebSocket bursts must not play proposal sounds."""
import pytest
from playwright.sync_api import expect

from tests.ui.test_incoming_dismiss_undo import fixture_base
from tests.ui.test_development_harness_playwright import _install_api

pytestmark = pytest.mark.e2e_playwright


def test_background_thread_updates_remain_silent_and_refresh(page, fixture_base):
    requests, sockets = [], []
    _install_api(page, requests, direct_execution=True, run_status='running')
    page.add_init_script('''
        Object.defineProperty(document, 'hidden', { get: () => true });
        window.__soundStarts = [];
        window.Audio = class {
            constructor(url) { window.__soundStarts.push(url); }
            play() { return Promise.resolve(); }
        };
    ''')
    page.route_web_socket('**/api/ws?*', lambda socket: sockets.append(socket) if 'chat_id=17' in socket.url else None)
    page.goto(f'{fixture_base}/development/threads/17/')
    expect(page.locator('#task-prompt')).to_be_enabled()
    page.wait_for_function('() => !!window.DecisionsNotificationSound')
    page.evaluate('''() => {
        window.__soundRequests = 0;
        const original = DecisionsNotificationSound.playCurrent;
        DecisionsNotificationSound.playCurrent = (...args) => { window.__soundRequests++; return original(...args); };
    }''')
    assert len(sockets) == 1
    before = requests.count(('GET', '/api/workflows/studio/tasks/17/execution'))
    import json
    for event in ['tool_started', 'tool_completed'] * 100 + ['turn_completed', 'turn_failed']:
        sockets[0].send(json.dumps({'type': 'turn_event', 'chat_id': 17, 'event_type': event}))
    page.wait_for_timeout(500)
    assert requests.count(('GET', '/api/workflows/studio/tasks/17/execution')) > before
    assert page.evaluate('window.__soundRequests') == 0
    assert page.evaluate('window.__soundStarts') == []
