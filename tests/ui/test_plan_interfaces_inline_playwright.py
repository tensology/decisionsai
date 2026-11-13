"""Interfaces tab: per-page description edited in place with the shared inline editor (toolbar, Edit markdown,
draft + baseRevision, Save with expected_revision, 409 conflict banner, 422 keeps the draft) and the Advanced
drawer with the editable page source (Apply / Cancel, same revision-checked save). Fixture server, mocked APIs.

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_interfaces_inline_playwright.py
"""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
SIZES = [(1440, 900), (768, 1024), (390, 844)]
PAGE = 'screen "Customers" id=customers route=/customers\n  #| Lists every **customer**.\n  heading "Customers"\n  button "Save" variant=primary\n'
FLOW = 'flowchart LR\n    A --> B\n'


class IfaceBackend(Backend):
    def __init__(self, content=PAGE, fmt='wire', kind='wireframe'):
        super().__init__()
        self.items = [i for i in self.items if i["item_type"] not in ("wireframe", "flows")]
        self.items.append({"id": 13, "item_type": kind, "title": "Customer pages", "content": content, "content_format": fmt, "status": "draft", "revision_count": 1})
        self.reject = None

    def route(self, route):
        req = route.request
        path = req.url.split('/api', 1)[1].split('?')[0]
        if path == '/workflows/studio/plan-items/13' and req.method == 'PATCH':
            body = json.loads(req.post_data or '{}')
            item = self.items[-1]
            if self.reject == 422:
                return route.fulfill(status=422, json={"detail": "Invalid Mermaid: parse error on line 2 (mock)"})
            if body.get("expected_revision") != item["revision_count"]:
                return route.fulfill(status=409, json={"detail": "This plan item has a newer revision. Your draft has been kept. Review the newer version before saving."})
            item["content"] = body["content"]; item["revision_count"] += 1; self.saves.append(body)
            return route.fulfill(json=item)
        return super().route(route)


def _open(page, w=1440, h=900, **kw):
    b = IfaceBackend(**kw)
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.locator('#plan-preview.plan-canvas-surface, #plan-preview .iface-canvas').first.wait_for()
    page.wait_for_timeout(700)
    return b, errors


def _no_overflow(page):
    g = page.evaluate("() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth })")
    assert g["sw"] <= g["iw"] + 1, g


def _surface_ok(page):
    g = page.evaluate("""() => { const s = document.querySelector('#plan-preview.plan-canvas-surface');
        return { callout: Boolean(s.querySelector(':scope > .plan-canvas-callout')), first: s.firstElementChild?.classList.contains('plan-canvas-callout'),
                 paneBottom: document.querySelector('#plan-artifacts').getBoundingClientRect().bottom, bottom: s.getBoundingClientRect().bottom,
                 panels: document.querySelectorAll('.plan-conversation').length }; }""")
    assert not g["callout"], g                                                # summary banner retired; quiet map status instead
    assert -1 <= g["paneBottom"] - g["bottom"] <= 24, g                      # footer flush, surface fills the pane
    assert g["panels"] == 1, g                                                # single shared chat panel


def _desc(page):
    return page.locator('.plan-page-description .plan-inline-text')


def _type_at_end(page, text):
    page.evaluate("""() => { const t = document.querySelector('.plan-page-description .plan-inline-text'); const r = document.createRange();
        r.selectNodeContents(t); r.collapse(false); t.focus(); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
    page.keyboard.type(text)


@pytest.mark.parametrize("w,h", SIZES)
def test_description_inline_edit_and_save(page, w, h):
    backend, errors = _open(page, w, h)
    desc = _desc(page)
    desc.wait_for()
    assert 'Lists every customer.' in desc.inner_text()
    assert desc.locator('strong').inner_text() == 'customer'
    _surface_ok(page); _no_overflow(page)
    page.locator('.plan-page-description').scroll_into_view_if_needed()
    _type_at_end(page, ' Sorted by name.')
    assert page.locator('.plan-page-description .plan-inline-state').inner_text() == 'Unsaved changes'
    page.screenshot(path=str(OUT / f'interfaces-inline-edit-{w}.png'))
    page.locator('.plan-page-description .plan-inline-save').click()
    page.locator('.plan-page-description .plan-inline-state[data-state="saved"]').wait_for(timeout=4000)
    body = backend.saves[-1]
    assert body["expected_revision"] == 1
    lines = body["content"].splitlines()
    assert lines[0] == 'screen "Customers" id=customers route=/customers'
    assert lines[1].startswith('  #| Lists every **customer**.') and 'Sorted by name.' in lines[1]
    assert lines[2:] == ['  heading "Customers"', '  button "Save" variant=primary']
    _surface_ok(page); _no_overflow(page)
    assert not errors, errors


def test_description_edit_markdown_mode_and_toolbar(page):
    backend, errors = _open(page)
    _desc(page).wait_for()
    _desc(page).click()
    page.locator('.md-toolbar').first.wait_for(state='attached', timeout=4000)           # shared floating toolbar
    page.locator('[data-desc-mode="raw"]').click()
    raw = page.locator('.plan-page-description textarea.plan-inline-raw')
    assert raw.input_value() == 'Lists every **customer**.'
    raw.fill('Lists every **customer**.\n\n- Paged by 50')
    page.locator('.plan-page-description .plan-inline-save').click()
    page.locator('.plan-page-description .plan-inline-state[data-state="saved"]').wait_for(timeout=4000)
    assert '  #| Lists every **customer**.\n  #|\n  #| - Paged by 50\n  heading' in backend.saves[-1]["content"]
    assert not errors, errors


@pytest.mark.parametrize('w,h', SIZES + [(1280, 900)])
def test_description_keeps_edits_made_during_save(page, w, h):
    backend, errors = _open(page, w, h)
    section = page.locator('.plan-page-description')
    section.locator('[data-desc-mode="raw"]').click()
    raw = section.locator('textarea.plan-inline-raw')
    save = section.get_by_role('button', name='Save', exact=True)
    status = section.locator('.plan-inline-state')
    first = 'Lists every **customer**.'
    submitted = first + '\n\n- Paged by 50'
    follow_up = submitted + '\n- Sorted by name'
    endpoint = '**/api/workflows/studio/plan-items/13'
    held = []
    page.route(endpoint, lambda route: held.append(route))

    raw.fill(submitted)
    save.click()
    expect(status).to_have_attribute('data-state', 'saving')
    raw.fill(follow_up)
    backend.route(held[0])
    page.unroute(endpoint)
    expect(raw).to_have_value(follow_up)
    expect(status).to_have_text('Unsaved changes')
    expect(section.locator('.plan-inline-tick')).to_be_hidden()
    assert 'Sorted by name' not in backend.saves[-1]['content']
    page.locator('#plan-preview').evaluate('node => { node.scrollTop = node.scrollHeight; }')
    assert status.evaluate('''node => {
        const dock = document.querySelector('.plan-pinned-dock').getBoundingClientRect();
        return node.getBoundingClientRect().bottom <= dock.top - 4;
    }'''), 'The save status must scroll clear of the pinned edit dock'
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'description-edit-during-save-{w}.png'))

    save.click()
    expect(status).to_have_text('Saved')
    assert backend.saves[-1]['expected_revision'] == 2
    assert backend.saves[-1]['content'] == PAGE.replace(
        '  #| Lists every **customer**.',
        '  #| Lists every **customer**.\n  #|\n  #| - Paged by 50\n  #| - Sorted by name',
    )
    expect(raw).to_have_value(follow_up)
    page.screenshot(path=str(OUT / f'description-edit-follow-up-saved-{w}.png'))
    assert not errors, errors


@pytest.mark.parametrize('w,h', SIZES + [(1280, 900)])
def test_description_reads_and_saves_the_selected_page(page, w, h):
    orders = 'screen "Orders" id=orders route=/orders\n  #| Lists recent orders.\n  heading "Orders"\n'
    backend, errors = _open(page, w, h, content=PAGE + orders)

    def open_page(name):
        node = page.locator('.iface-node', has_text=name)
        node.focus()
        node.press('Enter')
        page.get_by_role('tablist', name='Page perspective').get_by_role('button', name='Planning', exact=True).click()

    open_page('Customers')
    page.locator('[data-desc-mode="raw"]').click()
    raw = page.locator('.plan-page-description textarea.plan-inline-raw')
    raw.fill('Customers draft.')
    page.get_by_role('button', name='Back to all pages').click()
    open_page('Orders')
    page.locator('[data-desc-mode="raw"]').click()
    expect(raw).to_have_value('Lists recent orders.')
    raw.fill('Lists recent orders.\n\n- Show the newest orders first.')
    save = page.locator('.plan-page-description .plan-inline-save')
    save.click()
    expect(page.locator('.plan-page-description .plan-inline-state')).to_have_text('Saved')
    assert backend.saves[-1]['content'] == PAGE + orders.replace(
        '  #| Lists recent orders.', '  #| Lists recent orders.\n  #|\n  #| - Show the newest orders first.',
    )
    page.locator('#plan-preview').evaluate('node => { node.scrollTop = node.scrollHeight; }')
    page.screenshot(path=str(OUT / f'description-selected-page-saved-{w}.png'))
    page.get_by_role('button', name='Back to all pages').click()
    open_page('Customers')
    expect(raw).to_have_value('Customers draft.')
    save.click()
    expect(page.locator('.plan-page-description .plan-inline-state')).to_have_text('Saved')
    assert backend.saves[-1]['expected_revision'] == 2
    assert backend.saves[-1]['content'] == backend.saves[-2]['content'].replace(
        '  #| Lists every **customer**.', '  #| Customers draft.',
    )
    assert not errors, errors


def test_description_stale_revision_shows_conflict_banner(page):
    backend, _ = _open(page)
    _desc(page).wait_for()
    _type_at_end(page, ' More.')
    backend.items[-1]["revision_count"] = 5                                   # someone saved meanwhile
    page.locator('.plan-page-description .plan-inline-save').click()
    page.locator('.plan-page-description .plan-inline-conflict:not([hidden])').wait_for(timeout=4000)
    assert page.locator('.plan-page-description .plan-inline-save').is_disabled()
    assert 'More.' in _desc(page).inner_text() and backend.saves == []
    page.screenshot(path=str(OUT / 'interfaces-inline-conflict-1440.png'))
    page.locator('.plan-page-description .plan-inline-reload').click()
    page.wait_for_function("() => !document.querySelector('.plan-page-description .plan-inline-conflict:not([hidden])')", timeout=4000)


def test_description_422_shows_inline_and_keeps_draft(page):
    backend, _ = _open(page)
    _desc(page).wait_for()
    _type_at_end(page, ' Kept.')
    backend.reject = 422
    page.locator('.plan-page-description .plan-inline-save').click()
    state = page.locator('.plan-page-description .plan-inline-state[data-state="error"]')
    state.wait_for(timeout=4000)
    assert 'Invalid Mermaid' in state.inner_text()
    assert 'Kept.' in _desc(page).inner_text()
    assert not page.locator('.plan-page-description .plan-inline-save').is_disabled()
    # the draft survives a re-render (switch tab and back)
    page.get_by_role("button", name="Data", exact=True).click()
    page.get_by_role("button", name="Interfaces", exact=True).click()
    _desc(page).wait_for()
    assert 'Kept.' in _desc(page).inner_text()


def _drawer_geometry(page):
    return page.evaluate("""() => { const d = document.querySelector('.plan-drawer').getBoundingClientRect();
        const h = document.querySelector('.plan-drawer-head').getBoundingClientRect(), f = document.querySelector('.plan-drawer-foot').getBoundingClientRect();
        const t = document.querySelector('.plan-drawer textarea.plan-advanced-source').getBoundingClientRect();
        return { d: [d.left, d.top, d.right, d.bottom], head: h.bottom, foot: f.top, footBottom: f.bottom, t: [t.left, t.top, t.right, t.bottom], iw: innerWidth, ih: innerHeight }; }""")


@pytest.mark.parametrize("w,h", SIZES)
def test_advanced_drawer_cancel_and_apply(page, w, h):
    backend, errors = _open(page, w, h)
    page.locator('.plan-advanced-open').click()
    page.wait_for_function("() => document.querySelector('.plan-drawer-backdrop.open') && Math.abs(document.querySelector('.plan-drawer').getBoundingClientRect().right - innerWidth) < 0.1", timeout=3000)
    g = _drawer_geometry(page)
    assert 0 <= g["d"][0] and abs(g["d"][2] - g["iw"]) < 1 and g["d"][1] == 0 and abs(g["d"][3] - g["ih"]) < 1, g
    assert abs(g["t"][1] - g["head"]) <= 1 and abs(g["t"][3] - g["foot"]) <= 1, g         # text box fills the body
    assert abs(g["footBottom"] - g["ih"]) <= 1 and g["t"][2] <= g["iw"] + .5, g
    box = page.locator('.plan-drawer textarea.plan-advanced-source')
    assert box.input_value() == PAGE
    assert page.locator('.plan-drawer-apply').is_disabled()
    page.screenshot(path=str(OUT / f'interfaces-inline-drawer-{w}.png'))
    box.fill(PAGE + '  text "Draft"\n')
    page.locator('.plan-drawer-cancel').click()
    page.locator('.plan-drawer-backdrop').wait_for(state='detached')
    assert backend.saves == []
    page.locator('.plan-advanced-open').click()
    box = page.locator('.plan-drawer textarea.plan-advanced-source')
    assert box.input_value() == PAGE                                          # cancel discarded the edit
    box.fill(PAGE + '  text "Applied"\n')
    page.locator('.plan-drawer-apply').click()
    page.locator('.plan-drawer-backdrop').wait_for(state='detached')
    assert backend.saves[-1] == {"content": PAGE + '  text "Applied"\n', "expected_revision": 1}
    _surface_ok(page); _no_overflow(page)
    assert 'Lists every customer.' in _desc(page).inner_text()               # description survives
    assert not errors, errors


def test_advanced_drawer_rejects_invalid_and_stale(page):
    backend, _ = _open(page)
    page.locator('.plan-advanced-open').click()
    box = page.locator('.plan-drawer textarea.plan-advanced-source')
    box.fill('screen "X"\n  wobble "nope"\n')
    page.locator('.plan-drawer-apply').click()
    err = page.locator('.plan-drawer-error:not([hidden])')
    err.wait_for()
    assert 'Invalid page outline' in err.inner_text() and backend.saves == []
    assert box.input_value() == 'screen "X"\n  wobble "nope"\n'
    backend.items[-1]["revision_count"] = 3
    box.fill(PAGE + '  text "Stale"\n')
    page.locator('.plan-drawer-apply').click()
    page.wait_for_function("() => /newer revision/.test(document.querySelector('.plan-drawer-error')?.textContent || '')")
    assert page.locator('.plan-drawer').is_visible() and 'Stale' in box.input_value() and backend.saves == []
    page.keyboard.press('Escape')
    page.locator('.plan-drawer-backdrop').wait_for(state='detached')


def test_mermaid_flow_drawer_422_shown_inline(page):
    backend, _ = _open(page, content=FLOW, fmt='mermaid', kind='flows')
    page.locator('.plan-advanced-open').click()
    box = page.locator('.plan-drawer textarea.plan-advanced-source')
    assert box.input_value() == FLOW
    backend.reject = 422
    box.fill('flowchart LR\n    A -->\n')
    page.locator('.plan-drawer-apply').click()
    err = page.locator('.plan-drawer-error:not([hidden])')
    err.wait_for()
    assert 'Invalid Mermaid' in err.inner_text() and box.input_value() == 'flowchart LR\n    A -->\n'
