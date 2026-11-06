"""Inline plan editing: edit -> unsaved -> Save -> tick + "Saved" snackbar; failure keeps edits;
caret placement mid-text; floating toolbar formatting persists through save + reload.

Run against the fixture server (mocked APIs, no real DB):
    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_inline_edit_playwright.py
"""
import json

import pytest

from tests.ui.test_development_harness_playwright import _install_api, TEST_BASE_URL

pytestmark = pytest.mark.e2e_playwright
PLAN_URL = TEST_BASE_URL + '/development/boards/decisions/70/plan/'
DOC = "# Requirements\n\nFR-001: Customers can sign in.\n- keep `code` and **bold**\n\n```\nraw block\n```"


class Backend:
    def __init__(self):
        self.items = [
            {"id": 11, "item_type": "prd", "title": "Product requirements", "content": DOC, "content_format": "markdown", "status": "draft", "revision_count": 1},
            {"id": 12, "item_type": "architecture", "title": "Architecture and ERD", "content": "erDiagram\n    A ||--o{ B : has\n", "content_format": "mermaid", "status": "draft", "revision_count": 1},
        ]
        self.fail = False
        self.saves = []
        self.provider = ""
        self.model = ""

    def workspace(self):
        return {"id": 5, "board_key": "decisions:70", "board_name": "Merrypak", "project_id": 9, "root_path": "/tmp/x",
                "items": self.items, "item_count": len(self.items), "scan": {"state": "idle", "error": ""}}

    def route(self, route):
        req = route.request
        path = req.url.split('/api', 1)[1].split('?')[0]
        if path.startswith('/workflows/studio/plan-items/') and req.method == 'PATCH':
            if self.fail:
                return route.fulfill(status=500, json={"detail": "disk full (mock)"})
            body = json.loads(req.post_data or '{}')
            item = next(i for i in self.items if i["id"] == int(path.rsplit('/', 1)[1]))
            item["content"] = body["content"]
            item["revision_count"] += 1
            self.saves.append(body)
            return route.fulfill(json=item)
        if path == '/workflows/studio/plan-workspaces' and req.method == 'POST':
            return route.fulfill(json=self.workspace())
        if path.startswith('/workflows/studio/plan-workspaces/5'):
            if path.endswith('/conversation'):
                return route.fulfill(json={"messages": [], "attachments": [], "provider": self.provider, "model_name": self.model})
            if path.endswith('/scan-status'):
                return route.fulfill(json={"state": "idle", "error": ""})
            return route.fulfill(json=self.workspace())
        if path.startswith('/llms/available-providers'):
            providers = [{"id": self.provider, "name": self.provider}] if self.provider else []
            return route.fulfill(json={"providers": providers})
        if path.startswith('/llms/'):
            return route.fulfill(json={"providers": [], "models": [self.model] if self.model else []})
        return route.fallback()


@pytest.fixture
def backend(page):
    b = Backend()
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": 1440, "height": 900})
    return b


def _open_requirements(page):
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Documents", exact=True).click()
    editor = page.locator('.plan-inline-text[aria-label="Product requirements"]')
    editor.wait_for(state='visible', timeout=8000)
    return editor


def _caret_after(page, text):
    page.evaluate("""(needle) => {
        const root = document.querySelector('.plan-inline-text');
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let n; while ((n = walker.nextNode())) { const i = n.nodeValue.indexOf(needle); if (i >= 0) {
            const r = document.createRange(); r.setStart(n, i + needle.length); r.collapse(true);
            root.focus(); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return; } }
        throw new Error('needle not found: ' + needle);
    }""", text)


def _select(page, text):
    page.evaluate("""(needle) => {
        const root = document.querySelector('.plan-inline-text');
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let n; while ((n = walker.nextNode())) { const i = n.nodeValue.indexOf(needle); if (i >= 0) {
            const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + needle.length);
            root.focus(); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return; } }
        throw new Error('needle not found: ' + needle);
    }""", text)


def test_no_brief_buttons_and_edit_save_tick_snackbar(page, backend):
    editor = _open_requirements(page)
    assert page.get_by_role("button", name="Edit brief").count() == 0
    assert page.locator('.plan-brief-input').count() == 0
    state = page.locator('.plan-inline-state')
    save = page.locator('.plan-inline-save')
    assert state.inner_text() == 'Saved' and save.is_disabled()
    # click into the middle of a word, type there
    box = editor.locator('text=Customers can sign in.').bounding_box()
    page.mouse.click(box['x'] + 30, box['y'] + box['height'] / 2)
    _caret_after(page, 'Customers ')
    page.keyboard.type('and staff ')
    assert 'Customers and staff can sign in.' in editor.inner_text()
    assert state.inner_text() == 'Unsaved changes' and save.is_enabled()
    save.click()
    page.locator('.plan-inline-tick:not([hidden])').wait_for(timeout=4000)
    toast = page.locator('#studio-toast')
    assert toast.inner_text() == 'Saved'
    assert state.inner_text() == 'Saved' and save.is_disabled()
    saved = backend.saves[-1]["content"]
    assert saved == DOC.replace('Customers can', 'Customers and staff can')   # untouched lines byte-identical
    assert backend.saves[-1]["expected_revision"] == 1


def test_save_failure_keeps_edits_and_reenables_save(page, backend):
    editor = _open_requirements(page)
    backend.fail = True
    _caret_after(page, 'sign in')
    page.keyboard.type(' quickly')
    page.locator('.plan-inline-save').click()
    toast = page.locator('#studio-toast.error')
    toast.wait_for(timeout=4000)
    assert 'Could not save' in toast.inner_text()
    assert 'sign in quickly.' in editor.inner_text()
    assert page.locator('.plan-inline-save').is_enabled()
    assert page.locator('.plan-inline-state').inner_text() == 'Not saved: disk full (mock)'   # error shown inline; edits stay unsaved
    assert page.locator('.plan-inline-tick:not([hidden])').count() == 0
    backend.fail = False
    page.locator('.plan-inline-save').click()
    page.locator('.plan-inline-tick:not([hidden])').wait_for(timeout=4000)
    assert 'sign in quickly.' in backend.saves[-1]["content"]


def test_toolbar_formats_persist_and_dismisses(page, backend):
    editor = _open_requirements(page)
    bar = page.locator('.md-toolbar')
    _select(page, 'Customers')
    bar.wait_for(state='visible', timeout=3000)
    assert all(bar.locator(f'[data-fmt="{k}"]').get_attribute('aria-label') for k in ('bold', 'underline', 'strike', 'colour', 'highlight', 'link'))
    bar.locator('[data-fmt="bold"]').click()
    assert bar.locator('[data-fmt="bold"]').get_attribute('aria-pressed') == 'true'
    _select(page, 'sign')
    bar.locator('[data-fmt="highlight"]').click()
    _select(page, 'can')
    bar.locator('[data-fmt="colour"]').click()
    bar.locator('[data-colour="#3b82f6"]').click()
    _select(page, 'in.')
    page.keyboard.press('Control+u' if page.evaluate('navigator.platform').startswith('Linux') else 'Meta+u')
    _select(page, 'Requirements')
    page.keyboard.press('Meta+k')
    page.locator('.md-tb-link input').fill('javascript:alert(1)')
    page.locator('.md-tb-link [data-link="apply"]').click()
    assert 'http(s)' in page.locator('.md-tb-error').inner_text()
    page.locator('.md-tb-link input').fill('example.com/docs')
    page.locator('.md-tb-link [data-link="apply"]').click()
    # Escape dismisses, typing elsewhere still works
    _select(page, 'staff' if 'staff' in editor.inner_text() else 'keep')
    bar.wait_for(state='visible')
    page.keyboard.press('Escape')
    assert bar.is_hidden()
    _caret_after(page, 'keep')
    page.keyboard.type('!')
    assert bar.is_hidden()   # collapsed caret outside a link: no toolbar
    page.locator('.plan-inline-save').click()
    page.locator('.plan-inline-tick:not([hidden])').wait_for(timeout=4000)
    saved = backend.saves[-1]["content"]
    assert '**Customers**' in saved
    assert '<mark>sign</mark>' in saved
    assert '<span style="color: rgb(59, 130, 246)">can</span>' in saved or '<span style="color: #3b82f6">can</span>' in saved
    assert '<u>in.</u>' in saved
    assert '[Requirements](https://example.com/docs)' in saved
    assert 'keep!' in saved
    assert '```\nraw block\n```' in saved
    # reload: same formatting rendered from the stored markdown
    page.reload()
    page.get_by_role("button", name="Documents", exact=True).click()
    editor = page.locator('.plan-inline-text[aria-label="Product requirements"]')
    editor.wait_for(state='visible', timeout=8000)
    assert editor.locator('strong', has_text='Customers').count() == 1
    assert editor.locator('mark', has_text='sign').count() == 1
    assert editor.locator('u', has_text='in.').count() == 1
    link = editor.locator('a', has_text='Requirements')
    assert link.get_attribute('href') == 'https://example.com/docs' and link.get_attribute('rel') == 'noopener noreferrer' and link.get_attribute('target') == '_blank'
    assert 'rgb(59, 130, 246)' in editor.locator('span[style*="color"]', has_text='can').evaluate('n => getComputedStyle(n).color')
    assert page.locator('.plan-inline-state').inner_text() == 'Saved'


def test_data_tab_shows_diagram_and_readonly_advanced_only(page, backend):
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-editor .erd-table').first.wait_for(state='attached', timeout=8000)
    assert page.locator('#plan-preview .plan-inline-text, #plan-preview textarea, #plan-preview [contenteditable="true"]').count() == 0
    assert page.locator('#plan-preview .plan-inline-save').count() == 0
    assert page.locator('.plan-drawer').count() == 0                         # Advanced drawer closed by default
    page.locator('.plan-advanced-open').click()
    drawer = page.locator('.plan-drawer')
    assert 'A ||--o{ B : "has"' in drawer.locator('pre').inner_text() or 'A ||--o{ B : has' in drawer.locator('pre').inner_text()
    assert drawer.locator('textarea, [contenteditable="true"]').count() == 0  # read-only
    page.keyboard.press('Escape')
    page.locator('.plan-drawer-backdrop').wait_for(state='detached')
    page.get_by_role("button", name="Documents", exact=True).click()
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.plan-advanced-open').wait_for()
    assert page.locator('.plan-drawer').count() == 0                         # not remembered open
    assert backend.saves == []
