"""Audit fixes: stale inline drafts, ERD 409 vs 422, unsaved guards (mocked APIs on the fixture server, no DB).

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_save_conflicts_playwright.py
"""
import json

import pytest

from tests.ui.test_development_harness_playwright import _install_api, TEST_BASE_URL
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL, _open_requirements, _caret_after

pytestmark = pytest.mark.e2e_playwright
CONFLICT = "This plan item has a newer revision. Your draft has been kept. Review the newer version before saving."
ERD = 'erDiagram\n    A {\n        int id PK\n    }\n    A ||--o{ B : "has"\n'


class RevisionBackend(Backend):
    """Like the real route: 409 only for a stale expected_revision, 422 for invalid content."""
    def __init__(self):
        super().__init__()
        self.items[1]["content"] = ERD
        self.invalid = False
        self.patches = []

    def bump(self, item_id, content):
        item = next(i for i in self.items if i["id"] == item_id)
        item["content"] = content
        item["revision_count"] += 1

    def route(self, route):
        req = route.request
        path = req.url.split('/api', 1)[1].split('?')[0]
        if path.startswith('/workflows/studio/plan-items/') and req.method == 'PATCH':
            body = json.loads(req.post_data or '{}')
            self.patches.append(body)
            item = next(i for i in self.items if i["id"] == int(path.rsplit('/', 1)[1]))
            if body.get("expected_revision") is not None and body["expected_revision"] != item["revision_count"]:
                return route.fulfill(status=409, json={"detail": CONFLICT})
            if self.invalid:
                return route.fulfill(status=422, json={"detail": "Invalid Mermaid diagram. No changes were saved: Parse error (mock)"})
        return super().route(route)


@pytest.fixture
def backend(page):
    b = RevisionBackend()
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": 1440, "height": 900})
    return b


def _unload_blocked(page):
    return page.evaluate("() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; }")


def _spa_go(page, url):
    page.evaluate("(u) => { history.pushState({}, '', u); dispatchEvent(new PopStateEvent('popstate')); }", url)


def test_stale_inline_draft_opens_in_conflict_and_never_overwrites(page, backend):
    editor = _open_requirements(page)
    _caret_after(page, 'Customers can sign in.')
    page.keyboard.type(' My edit.')
    assert _unload_blocked(page)                                   # unsaved inline edit guards unload
    # leave the plan in-app, someone else saves a newer revision, come back (refreshWorkspace path)
    _spa_go(page, TEST_BASE_URL + '/development/boards/decisions/70/')
    page.wait_for_timeout(300)
    backend.bump(11, "# Requirements\n\nFR-001: Newer server text.\n")
    _spa_go(page, PLAN_URL)
    page.get_by_role("button", name="Documents", exact=True).click()
    banner = page.locator('.plan-inline-conflict')
    banner.wait_for(state='visible', timeout=8000)
    assert 'My edit.' in page.locator('.plan-inline-text').inner_text()      # the user's draft is still there
    assert page.locator('.plan-inline-save').is_disabled()                   # ...but can't overwrite revision 2
    assert backend.patches == []
    page.locator('.plan-inline-reload').click()
    page.locator('.plan-inline-conflict').wait_for(state='hidden')
    assert 'Newer server text.' in page.locator('.plan-inline-text').inner_text()
    assert page.evaluate("() => document.querySelector('.plan-inline-text').innerText.includes('My edit.')") is False
    assert backend.patches == []                                             # nothing was written


def test_inline_save_sends_the_draft_base_revision(page, backend):
    _open_requirements(page)
    _caret_after(page, 'Customers can sign in.')
    page.keyboard.type(' x')
    backend.bump(11, "# Requirements\n\nFR-001: Newer.\n")       # server moves on while the page still shows rev 1
    page.locator('.plan-inline-save').click()
    page.locator('.plan-inline-conflict').wait_for(state='visible', timeout=6000)
    assert backend.patches[-1]["expected_revision"] == 1
    assert page.locator('.plan-inline-save').is_disabled()


def test_erd_409_locks_with_reload_latest_and_422_stays_visible_then_retries(page, backend):
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for(state='visible', timeout=10000)
    # 422: error stays visible; the next edit retries and saves
    backend.invalid = True
    page.locator('[data-erd="add-table"]').click()
    assert _unload_blocked(page)                                             # dirty ERD guards unload
    status = page.locator('.erd-status')
    page.locator('.erd-editor[data-save-state="error"]').wait_for(timeout=6000)
    assert 'Invalid Mermaid' in status.inner_text()
    assert page.locator('.erd-reload').is_hidden()
    backend.invalid = False
    page.locator('[data-erd="add-table"]').click()
    page.locator('.erd-editor[data-save-state="saved"]').wait_for(timeout=6000)
    assert not _unload_blocked(page)
    assert backend.patches[-1]["expected_revision"] == 1                    # ctx revision, not a fake 0
    # 409: a newer revision elsewhere locks autosave until "Reload latest"
    backend.bump(12, ERD.replace('"has"', '"owns"') + '    SERVER_ONLY {\n    }\n')
    page.locator('[data-erd="add-table"]').click()
    page.locator('.erd-editor[data-save-state="conflict"]').wait_for(timeout=6000)
    reload_button = page.locator('.erd-reload')
    assert reload_button.is_visible() and 'your version is copied' in reload_button.inner_text()
    count = len(backend.patches)
    page.locator('[data-erd="add-table"]').click()                           # more edits never retry the stale save
    page.wait_for_timeout(1800)
    assert len(backend.patches) == count
    assert page.locator('.erd-editor').get_attribute('data-save-state') == 'conflict'
    assert _unload_blocked(page)
    reload_button.click()
    page.locator('.erd-table[data-table="SERVER_ONLY"]').wait_for(timeout=6000)
    assert page.locator('.erd-editor').get_attribute('data-save-state') == 'saved'
    assert len(backend.patches) == count                                     # reload wrote nothing
    assert not _unload_blocked(page)
