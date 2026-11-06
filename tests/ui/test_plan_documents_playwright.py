"""Documents tab (was Requirements): document tabs, FRAC default, modes (Inline editor / Edit markdown) with
shared drafts, Print PDF stylesheet, user-documents flag. Fixture server, mocked APIs (no DB).

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_documents_playwright.py
"""
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
SIZES = [(1440, 900), (768, 1024), (390, 844)]
FRAC = "# FRAC\n\n## Sign in\n\n1. User enters email\n2. User gets a code\n\nFR-001: Customers can sign in.\n"
ORDER = ['FRAC', 'Brief', 'PRD', 'Handover', 'Skills', 'File structure', 'Decision', 'Overview', 'Discovery']


class DocsBackend(Backend):
    def __init__(self):
        super().__init__()
        arch = self.items[1]
        kinds = [('brief', 'Outcome brief'), ('prd', 'Product requirements'), ('discovery_proposal', 'Discovery proposal'), ('handover', 'Handover'),
                 ('skills', 'Skills'), ('frac', 'Functional requirements'), ('file_structure', 'File structure'), ('decision', 'Decision'), ('project_overview', 'Overview')]
        self.items = [{"id": 20 + i, "item_type": k, "title": t, "content": FRAC if k == 'frac' else f"# {t}\n\nBody of {t}.\n",
                       "content_format": "markdown", "status": "draft", "revision_count": 1, "sort_order": i} for i, (k, t) in enumerate(kinds)] + [arch]
        self.created, self.renamed, self.deleted = [], [], []

    def route(self, route):
        req = route.request
        path = req.url.split('/api', 1)[1].split('?')[0]
        if path.startswith('/workflows/studio/plan-items/') and req.method == 'PATCH':
            body = json.loads(req.post_data or '{}')
            item = next(i for i in self.items if i["id"] == int(path.rsplit('/', 1)[1]))
            if body.get("expected_revision") is not None and body["expected_revision"] != item["revision_count"]:
                return route.fulfill(status=409, json={"detail": "This plan item has a newer revision."})
            if "content" in body and body["content"] is not None:
                item["content"] = body["content"]; self.saves.append(body)
            if body.get("title"):
                item["title"] = body["title"]; self.renamed.append(body)
            item["revision_count"] += 1
            return route.fulfill(json=item)
        if path == '/workflows/studio/plan-workspaces/5/items' and req.method == 'POST':
            body = json.loads(req.post_data or '{}')
            item = {"id": 90 + len(self.created), "item_type": body["item_type"], "title": body.get("title") or "Document", "content": body.get("content") or "# Document\n\n",
                    "content_format": body.get("content_format") or "markdown", "status": "draft", "revision_count": 1, "sort_order": 50}
            self.items.append(item); self.created.append(body)
            return route.fulfill(json=item)
        if path.startswith('/workflows/studio/plan-workspaces/5/plan-items/') and req.method == 'DELETE':
            item_id = int(path.rsplit('/', 1)[1])
            item = next(i for i in self.items if i["id"] == item_id)
            expected = int(req.url.split('expected_revision=', 1)[1].split('&')[0]) if 'expected_revision=' in req.url else None
            if item["item_type"] != 'document':
                return route.fulfill(status=403, json={"detail": "Only user-added documents can be deleted."})
            if expected != item["revision_count"]:
                return route.fulfill(status=409, json={"detail": "This document has a newer revision."})
            self.items.remove(item); self.deleted.append((item_id, expected))
            return route.fulfill(json={"id": item_id, "archived": True, "revision": expected + 1, "archive_path": ""})
        return super().route(route)


def _open(page, w, h, flag=None, stub_print=False, docs=()):
    b = DocsBackend()
    for i, title in enumerate(docs):
        b.items.append({"id": 70 + i, "item_type": "document", "title": title, "content": f"# {title}\n", "content_format": "markdown",
                        "status": "draft", "revision_count": 1, "sort_order": 60 + i})
    if flag is not None:
        page.add_init_script(f"window.__DECISIONS_TEST_FLAGS__ = {{ userDocuments: {str(bool(flag)).lower()} }};")
    if stub_print:
        page.add_init_script("window.print = () => { window.__printed = (window.__printed || 0) + 1; };")
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Documents", exact=True).click()
    page.locator('.plan-doc-tab[aria-selected="true"]').wait_for()
    page.wait_for_timeout(300)
    return b, errors


def _labels(page):
    return page.locator('[role="tablist"][aria-label="Documents"] [role="tab"]').evaluate_all("(ns) => ns.map((n) => n.childNodes[0].textContent)")


def _rect(page, sel):
    return page.evaluate("(s) => { const r = document.querySelector(s).getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; }", sel)


@pytest.mark.parametrize("w,h", SIZES)
def test_documents_tab_bar_frac_default_and_keyboard(page, w, h):
    _, errors = _open(page, w, h)
    assert page.get_by_role("button", name="Requirements", exact=True).count() == 0
    assert page.locator('[data-plan-tab="requirements"]').inner_text() == 'Documents'
    assert _labels(page) == ORDER
    sel = page.locator('.plan-doc-tab[aria-selected="true"]')
    assert sel.inner_text().strip() == 'FRAC' and sel.get_attribute('tabindex') == '0'
    assert page.locator('.plan-doc-tab[aria-selected="true"]').count() == 1
    assert page.locator('.plan-inline-text').get_attribute('aria-label') == 'Functional requirements'
    assert page.locator('#plan-context-hint').inner_text() == 'Context: FRAC'
    assert page.locator('#plan-doc-add').count() == 1                      # flag on by default: Add document
    art, tabs, acts = _rect(page, '#plan-artifacts'), _rect(page, '.plan-doc-tabs'), _rect(page, '.plan-docs-actions')
    assert tabs['l'] - art['l'] <= 16 and tabs['t'] - art['t'] <= 16, (art, tabs)                  # top left
    assert art['r'] - acts['r'] <= 16 and acts['l'] >= tabs['l'], (art, acts)                     # far right
    tops = page.locator('.plan-doc-tab').evaluate_all("(ns) => ns.map((n) => Math.round(n.getBoundingClientRect().top))")
    assert len(set(tops)) == 1, tops                                                             # one row, no wrap collisions
    strip = page.evaluate("() => { const n = document.querySelector('.plan-doc-tabs'); return [n.scrollWidth, n.clientWidth, getComputedStyle(n).overflowX]; }")
    assert strip[2] == 'auto'
    if w == 390:
        assert strip[0] > strip[1]                                                              # scrolls sideways at 390
    assert page.evaluate("() => document.documentElement.scrollWidth") <= w
    if w != 390:
        assert abs(tabs['t'] - acts['t']) < 12                                                  # same row on wide screens
    page.screenshot(path=str(OUT / f'documents-{w}.png'))
    # keyboard: arrows / Home / End move selection and focus (automatic activation)
    sel.focus()
    page.keyboard.press('ArrowRight')
    assert page.evaluate("() => document.activeElement.textContent.trim()") == 'Brief'
    assert page.locator('.plan-doc-tab[aria-selected="true"]').inner_text().strip() == 'Brief'
    assert page.locator('#plan-context-hint').inner_text() == 'Context: Brief'
    page.keyboard.press('End')
    assert page.evaluate("() => document.activeElement.textContent.trim()") == 'Discovery'
    vis = page.evaluate("() => { const t = document.activeElement.getBoundingClientRect(), s = document.querySelector('.plan-doc-tabs').getBoundingClientRect(); return t.left >= s.left - 1 && t.right <= s.right + 1; }")
    assert vis                                                                                   # scrolled into view
    page.keyboard.press('Home')
    assert page.locator('.plan-doc-tab[aria-selected="true"]').inner_text().strip() == 'FRAC'
    page.keyboard.press('ArrowLeft')
    assert page.locator('.plan-doc-tab[aria-selected="true"]').inner_text().strip() == 'Discovery'
    # Interfaces / Data unaffected, chat hint only on Documents
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-editor, .plan-mermaid').first.wait_for()
    assert page.locator('#plan-context-hint').is_hidden()
    print('MEASURE docs', w, json.dumps({"artifacts": art, "tabs": tabs, "actions": acts, "strip": strip}))
    assert not errors, errors


@pytest.mark.parametrize("w,h", SIZES)
def test_modes_share_drafts_and_save_rules(page, w, h):
    backend, errors = _open(page, w, h)
    modes = page.locator('.plan-doc-modes')
    assert modes.get_attribute('role') == 'group'
    assert modes.locator('[data-doc-mode="inline"]').get_attribute('aria-pressed') == 'true'
    text = page.locator('.plan-inline-text')
    text.click()
    page.keyboard.press('End' if False else 'Control+End')
    page.keyboard.type(' Inline edit.')
    assert page.locator('.plan-doc-tab[aria-selected="true"]').get_attribute('data-unsaved') == 'true'
    modes.locator('[data-doc-mode="raw"]').click()
    raw = page.locator('textarea.plan-inline-raw')
    raw.wait_for()
    assert modes.locator('[data-doc-mode="raw"]').get_attribute('aria-pressed') == 'true'
    assert 'Inline edit.' in raw.input_value()                                     # draft carried into raw mode
    assert page.locator('.plan-inline-state').inner_text() == 'Unsaved changes'
    raw.press('Control+End'); raw.type('\n\nRaw **added**.')
    page.screenshot(path=str(OUT / f'documents-raw-{w}.png'))
    fb, pv = page.locator('.plan-inline-footer').bounding_box(), _rect(page, '#plan-preview')
    assert abs(fb['y'] + fb['height'] - pv['b']) <= 1                               # footer flush in raw mode too
    # switch documents with unsaved edits: per-document drafts are kept
    page.locator('.plan-doc-tab', has_text='Brief').click()
    assert 'Body of Outcome brief' in page.locator('textarea.plan-inline-raw').input_value()
    page.locator('.plan-doc-tab', has_text='FRAC').click()
    assert 'Raw **added**.' in page.locator('textarea.plan-inline-raw').input_value()
    modes.locator('[data-doc-mode="inline"]').click()
    text = page.locator('.plan-inline-text')
    text.wait_for()
    assert 'Raw added.' in text.inner_text() and text.locator('strong', has_text='added').count() == 1
    page.locator('.plan-inline-save').click()
    page.locator('.plan-inline-tick:not([hidden])').wait_for(timeout=4000)
    saved = backend.saves[-1]
    assert saved["expected_revision"] == 1 and 'Raw **added**.' in saved["content"] and 'Inline edit.' in saved["content"]
    assert page.locator('.plan-inline-state').inner_text() == 'Saved'
    assert page.locator('.plan-doc-tab[aria-selected="true"]').get_attribute('data-unsaved') is None
    # mode is remembered for the session
    modes.locator('[data-doc-mode="raw"]').click()
    page.reload()
    page.get_by_role("button", name="Documents", exact=True).click()
    page.locator('textarea.plan-inline-raw').wait_for()
    assert 'Raw **added**.' in page.locator('textarea.plan-inline-raw').input_value()
    assert not errors, errors


def test_raw_save_applies_the_same_allowlist_and_conflict_rules(page):
    backend, _ = _open(page, 1440, 900)
    page.locator('[data-doc-mode="raw"]').click()
    raw = page.locator('textarea.plan-inline-raw')
    raw.fill('# FRAC\n\n> quote kept\n\n<script>alert(1)</script><u>ok</u> <img src=x onerror=alert(1)>\n')
    page.locator('.plan-inline-save').click()
    page.locator('.plan-inline-tick:not([hidden])').wait_for(timeout=4000)
    stored = backend.saves[-1]["content"]
    assert '<script' not in stored and '<img' not in stored and '<u>ok</u>' in stored and '> quote kept' in stored
    assert raw.input_value() == stored
    # stale revision: 409 locks Save and shows the conflict banner (same rules as inline)
    next(i for i in backend.items if i["item_type"] == 'frac')["revision_count"] = 9
    raw.fill(stored + 'more\n')
    page.locator('.plan-inline-save').click()
    page.locator('.plan-inline-conflict:not([hidden])').wait_for(timeout=4000)
    assert page.locator('.plan-inline-save').is_disabled()


def test_enter_in_numbered_list_continues_numbering(page):
    backend, _ = _open(page, 1440, 900)
    page.evaluate("""() => { const t = document.querySelector('.plan-inline-text'); const w = document.createTreeWalker(t, NodeFilter.SHOW_TEXT); let n;
        while ((n = w.nextNode())) { const i = n.nodeValue.indexOf('gets a code'); if (i >= 0) { const r = document.createRange(); r.setStart(n, i + 11); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); t.focus(); return; } } }""")
    page.keyboard.press('Enter'); page.keyboard.type('User signs in')
    page.locator('.plan-inline-save').click()
    page.locator('.plan-inline-tick:not([hidden])').wait_for(timeout=4000)
    lines = backend.saves[-1]["content"].splitlines()
    assert '2. User gets a code' in lines and '3. User signs in' in lines


@pytest.mark.parametrize("w,h", SIZES)
def test_print_pdf_uses_print_only_container(page, w, h):
    _, errors = _open(page, w, h, stub_print=True)
    print_btn = page.locator('#plan-doc-print')
    assert print_btn.evaluate("n => n.tagName") == 'BUTTON'
    assert 'header-button' in print_btn.get_attribute('class')
    assert page.locator('.plan-doc-modes button').evaluate_all("ns => ns.map((n) => n.dataset.docMode)") == ['inline', 'raw']
    modes_box, print_box = page.locator('.plan-doc-modes').bounding_box(), print_btn.bounding_box()
    assert print_box['x'] >= modes_box['x'] + modes_box['width'] - 1, (modes_box, print_box)
    assert page.locator('.plan-docs-actions > :last-child').get_attribute('id') == 'plan-doc-print'
    print_btn.click()
    assert page.evaluate("() => window.__printed") == 1
    page.emulate_media(media='print')
    root = page.locator('#plan-print-root')
    assert root.is_visible() and 'Customers can sign in.' in root.inner_text() and 'FRAC' in root.inner_text()
    shown = page.evaluate("() => [...document.body.children].filter((n) => n.id !== 'plan-print-root' && getComputedStyle(n).display !== 'none').map((n) => n.tagName + '#' + n.id + '.' + n.className)")
    assert shown == [], shown                                                       # all app chrome hidden
    for sel in ('.plan-docs-bar', '.plan-conversation', '.plan-detail-header', '.md-toolbar'):
        assert page.locator(sel).count() == 0 or not page.locator(sel).first.is_visible(), sel
    assert page.locator('#plan-print-root [contenteditable]').count() == 0
    page.screenshot(path=str(OUT / f'documents-print-{w}.png'))
    page.emulate_media(media='screen')
    assert not root.is_visible()                                                    # screen: never shown
    page.evaluate("() => window.dispatchEvent(new Event('afterprint'))")
    assert page.locator('#plan-print-root').count() == 0
    assert not errors, errors


def test_flag_off_built_in_tabs_have_no_user_controls(page):
    _open(page, 1440, 900, flag=False)
    assert page.locator('#plan-doc-add, #plan-doc-rename, #plan-doc-delete, .plan-doc-tab[data-user-doc]').count() == 0


def test_user_documents_are_on_by_default(page):
    _open(page, 1440, 900)                                                                 # no test override
    assert page.locator('#plan-doc-add').count() == 1


def _confirm(page):
    dialog = page.locator('#decisions-confirm-modal')
    dialog.wait_for(state='visible')
    return dialog


def test_delete_absent_on_built_in_tabs(page):
    _open(page, 1440, 900, docs=['Runbook'])
    for label in ORDER:
        page.locator('.plan-doc-tab', has_text=label).first.click()
        assert page.locator('#plan-doc-delete').count() == 0, label


@pytest.mark.parametrize("w,h", SIZES)
def test_delete_user_document_confirm_falls_back_to_frac(page, w, h):
    backend, errors = _open(page, w, h, docs=['Runbook'])
    page.locator('.plan-doc-tab[data-user-doc="true"]').click()
    page.locator('#plan-doc-delete').click()
    dialog = _confirm(page)
    assert 'Runbook' in dialog.inner_text()
    if w == 1440:
        page.screenshot(path=str(OUT / 'documents-delete-confirm.png'))
    dialog.locator('.decisions-confirm-ok').click()
    page.locator('.plan-doc-tab[data-user-doc="true"]').wait_for(state='detached')
    assert backend.deleted == [(70, 1)]
    selected = page.locator('.plan-doc-tab[aria-selected="true"]')
    assert selected.inner_text().strip() == 'FRAC'
    assert 'Runbook' not in _labels(page)
    assert not errors, errors


def test_delete_cancel_keeps_document(page):
    backend, _ = _open(page, 1440, 900, docs=['Runbook'])
    page.locator('.plan-doc-tab[data-user-doc="true"]').click()
    page.locator('#plan-doc-delete').click()
    _confirm(page).locator('.decisions-confirm-cancel').click()
    page.wait_for_timeout(200)
    assert backend.deleted == [] and 'Runbook' in _labels(page)
    assert page.locator('.plan-doc-tab[data-user-doc="true"]').get_attribute('aria-selected') == 'true'


def test_delete_stale_revision_shows_conflict_message(page):
    backend, _ = _open(page, 1440, 900, docs=['Runbook'])
    page.locator('.plan-doc-tab[data-user-doc="true"]').click()
    next(i for i in backend.items if i["id"] == 70)["revision_count"] = 4            # someone saved meanwhile
    page.locator('#plan-doc-delete').click()
    _confirm(page).locator('.decisions-confirm-ok').click()
    toast = page.locator('#studio-toast')
    toast.wait_for(state='visible')
    assert 'newer revision' in toast.inner_text()
    assert backend.deleted == [] and 'Runbook' in _labels(page)


def test_flag_on_add_and_rename_user_document(page):
    backend, errors = _open(page, 1440, 900, flag=True)
    page.locator('.plan-doc-tab', has_text='FRAC').click()
    assert page.locator('#plan-doc-rename').count() == 0                                  # built-ins: no rename
    page.locator('#plan-doc-add').click()
    name = page.locator('.plan-doc-rename-input')
    name.wait_for()
    assert backend.created == [{"item_type": "document", "title": "Untitled document", "content": "# Untitled document\n\n", "content_format": "markdown"}]
    name.fill('Runbook'); name.press('Enter')
    tab = page.locator('.plan-doc-tab[data-user-doc="true"]')
    tab.wait_for()
    assert tab.inner_text().strip() == 'Runbook' and tab.get_attribute('aria-selected') == 'true'
    assert backend.renamed[-1] == {"title": "Runbook", "expected_revision": 1}
    assert _labels(page)[-1] == 'Runbook' and _labels(page)[0] == 'FRAC'
    assert page.locator('#plan-context-hint').inner_text() == 'Context: Runbook'
    page.locator('#plan-doc-rename').click()
    page.locator('.plan-doc-rename-input').fill('Ops runbook'); page.keyboard.press('Enter')
    page.locator('.plan-doc-tab', has_text='Ops runbook').wait_for()
    assert backend.renamed[-1] == {"title": "Ops runbook", "expected_revision": 2}
    assert not errors, errors
