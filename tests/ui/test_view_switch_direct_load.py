"""Direct (hard) loads of plan / Kanban / thread URLs show the board view switcher.

Run against the fixture server (mocked APIs):
    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_view_switch_direct_load.py
"""
import pytest

from tests.ui.test_development_harness_playwright import _install_api, TEST_BASE_URL

pytestmark = pytest.mark.e2e_playwright

WS = {"id": 5, "board_key": "decisions:70", "board_name": "Merrypak", "project_id": 9, "root_path": "/tmp/x",
      "items": [], "item_count": 0, "scan": {"state": "idle", "error": ""}}


def _plan_routes(route):
    path = route.request.url.split('/api', 1)[1].split('?')[0]
    if path == '/workflows/studio/plan-workspaces' and route.request.method == 'POST':
        return route.fulfill(json=WS)
    if path.startswith('/workflows/studio/plan-workspaces/5'):
        if path.endswith('/conversation'):
            return route.fulfill(json={"messages": [], "attachments": [], "provider": "", "model_name": ""})
        if path.endswith('/scan-status'):
            return route.fulfill(json={"state": "idle", "error": ""})
        return route.fulfill(json=WS)
    if path.startswith('/llms/'):
        return route.fulfill(json={"providers": [], "models": []})
    return route.fallback()


@pytest.mark.parametrize('path', ['/development/boards/decisions/70/plan/', '/development/boards/decisions/70/kanban/', '/development/threads/17/'])
def test_view_switcher_visible_on_direct_load(page, path):
    _install_api(page, [])
    page.route("**/api/**", _plan_routes)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + path)
    page.wait_for_function("""() => [...document.querySelectorAll('[data-board-view-switch]')]
        .some(n => !n.classList.contains('hidden') && n.offsetParent !== null && n.querySelectorAll('[data-board-view]').length === 3)""", timeout=8000)


def test_plan_menu_present_on_direct_load_and_thread_menu_unaffected(page):
    _install_api(page, [])
    page.route("**/api/**", _plan_routes)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + '/development/boards/decisions/70/plan/')
    rebuild = page.locator('#plan-rebuild-sitemap')
    rebuild.wait_for(state='visible', timeout=8000)
    assert rebuild.inner_text().strip() == 'Rebuild'
    assert page.locator('#plan-more-button').count() == 0
    assert page.locator('#plan-header-menu').count() == 0
    assert page.get_by_role('button', name='Build tickets', exact=True).is_visible()
    for ident in ('thread-more-button', 'thread-header-menu'):
        assert page.locator(f'[id="{ident}"]').count() == 1
    page.goto(TEST_BASE_URL + '/development/threads/17/')
    page.locator('#thread-more-button').wait_for(state='visible', timeout=8000)
    page.locator('#thread-more-button').click()
    assert page.locator('#thread-header-menu').is_visible()
    assert page.locator('#plan-header-menu').count() == 0 or not page.locator('#plan-header-menu').is_visible()
