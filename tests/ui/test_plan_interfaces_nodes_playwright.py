"""Interfaces node metadata (N6 phase 2): shape picker, note (shared inline editor, 500 cap), image (attachment
upload, asset id only, thumbnail + enlarge + missing placeholder), labelled links with click-through and a dangling
chip, orphan-key pruning, validation_warnings notice, 409 / 422 handling, keyboard, bottom sheet, no overflow.
Fixture server, mocked APIs (no real DB):

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_interfaces_nodes_playwright.py
"""
import base64
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
SIZES = [(1440, 900), (768, 1024), (390, 844)]
PAGE = 'screen "Customers" id=customers route=/customers\n  #| Lists every **customer**.\n  heading "Customers"\n'
SAVED = 'screen "Saved" route=/saved\n  text "Done"\n'
FLOW = 'flowchart LR\n  A{Ok?} -->|yes| B([Done])\n'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')


def meta_line(nodes, prefix='# decisions-flow:'):
    return f"{prefix} {json.dumps({'nodes': nodes, 'v': 1}, sort_keys=True, separators=(',', ':'))}\n"


class NodeBackend(Backend):
    def __init__(self, content=PAGE, fmt='wire', kind='wireframe', extra=None, assets=None):
        super().__init__()
        self.items = [i for i in self.items if i["item_type"] not in ("wireframe", "flows")]
        self.items.append({"id": 13, "item_type": kind, "title": "Customer pages", "content": content, "content_format": fmt, "status": "draft", "revision_count": 1})
        for row in extra or []:
            self.items.append(row)
        self.assets = list(assets or [])
        self.reject = None
        self.warnings = None
        self.uploads = 0

    def route(self, route):
        req = route.request
        path = req.url.split('/api', 1)[1].split('?')[0]
        if path.startswith('/workflows/studio/plan-items/') and req.method == 'PATCH':
            body = json.loads(req.post_data or '{}')
            item = next(i for i in self.items if i["id"] == int(path.rsplit('/', 1)[1]))
            if self.reject == 422:
                return route.fulfill(status=422, json={"detail": 'Unknown node shape "star". Use page, rectangle, square, circle, oval or diamond.'})
            if body.get("expected_revision") != item["revision_count"]:
                return route.fulfill(status=409, json={"detail": "This plan item has a newer revision. Your draft has been kept. Review the newer version before saving."})
            item["content"] = body["content"]; item["revision_count"] += 1; self.saves.append(body)
            reply = dict(item)
            if self.warnings:
                reply["validation_warnings"] = self.warnings
            return route.fulfill(json=reply)
        if path == '/workflows/studio/plan-workspaces/5/attachments' and req.method == 'POST':
            self.uploads += 1
            asset = {"id": 40 + self.uploads, "name": "shot.png", "mime_type": "image/png", "url": f"/api/workflows/studio/plan-workspaces/5/attachments/{40 + self.uploads}"}
            self.assets.append(asset)
            return route.fulfill(json=asset)
        if path.startswith('/workflows/studio/plan-workspaces/5/attachments/'):
            return route.fulfill(body=PNG, content_type='image/png')
        if path.endswith('/conversation'):
            return route.fulfill(json={"messages": [], "attachments": self.assets, "provider": "", "model_name": ""})
        return super().route(route)


def _open(page, w=1440, h=900, **kw):
    b = NodeBackend(**kw)
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.locator('#plan-preview.plan-canvas-surface').wait_for()
    page.wait_for_timeout(700)
    return b, errors


def _no_overflow(page):
    g = page.evaluate("() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth })")
    assert g["sw"] <= g["iw"] + 1, g


def _inside_viewport(page, selector):
    g = page.evaluate(f"""() => {{ const r = document.querySelector('{selector}').getBoundingClientRect();
        return {{ l: r.left, r: r.right, t: r.top, b: r.bottom, iw: innerWidth, ih: innerHeight }}; }}""")
    assert g["l"] >= -0.5 and g["r"] <= g["iw"] + 0.5 and g["t"] >= -0.5 and g["b"] <= g["ih"] + 0.5, g
    return g


def _pop(page):
    page.locator('.plan-interfaces-tools .plan-node-open').click()
    pop = page.locator('.plan-node-popover[role="dialog"]')
    pop.wait_for()
    return pop


def _sidecar(content):
    last = content.rstrip('\n').split('\n')[-1]
    assert last.startswith('# decisions-flow: ') or last.startswith('%% decisions-flow: '), content
    return json.loads(last.split(': ', 1)[1])


@pytest.mark.parametrize("w,h", SIZES)
def test_shape_change_renders_and_saves(page, w, h):
    backend, errors = _open(page, w, h)
    assert page.locator('.plan-node-strip').count() == 0                       # no metadata: nothing new rendered
    pop = _pop(page)
    g = _inside_viewport(page, '.plan-node-popover')
    if w <= 600:
        assert 'plan-node-sheet' in pop.get_attribute('class') and abs(g["b"] - g["ih"]) <= 1 and g["l"] <= 0.5, g
    assert pop.locator('input[value="page"]').is_checked()
    page.screenshot(path=str(OUT / f'n6-popover-{w}.png'))
    pop.locator('label.plan-node-shape', has_text='Diamond').click()
    page.locator('.plan-node-status', has_text='Shape saved').wait_for(timeout=4000)
    body = backend.saves[-1]
    assert body["expected_revision"] == 1
    assert body["content"].startswith(PAGE)                                    # "#|" region and source untouched
    assert _sidecar(body["content"]) == {"nodes": {"customers": {"shape": "diamond"}}, "v": 1}
    strip = page.locator('.plan-node-strip')
    strip.wait_for()
    assert strip.locator('.plan-node-badge svg.plan-node-glyph').count() == 1
    _no_overflow(page)
    page.screenshot(path=str(OUT / f'n6-shape-{w}.png'))
    page.keyboard.press('Escape')
    page.locator('.plan-node-popover').wait_for(state='detached')
    assert not errors, errors


def test_keyboard_navigation_and_focus(page):
    _open(page)
    opener = page.locator('.plan-interfaces-tools .plan-node-open')
    opener.focus()
    page.keyboard.press('Enter')
    page.locator('.plan-node-popover').wait_for()
    assert page.evaluate("() => document.activeElement.matches('.plan-node-popover input[type=radio]')")
    for _ in range(30):                                                         # focus stays trapped in the popover
        page.keyboard.press('Tab')
        assert page.evaluate("() => Boolean(document.activeElement.closest('.plan-node-popover'))")
    page.keyboard.press('Escape')
    page.locator('.plan-node-popover').wait_for(state='detached')
    assert page.evaluate("() => document.activeElement.classList.contains('plan-node-open')")


def test_note_saves_with_cap(page):
    backend, errors = _open(page)
    pop = _pop(page)
    note = pop.locator('.plan-node-note .plan-inline-text')
    note.click()
    page.keyboard.type('Admins only')
    pop.locator('.plan-node-note .plan-inline-save').click()
    page.locator('.plan-node-status', has_text='Note saved').wait_for(timeout=4000)
    assert _sidecar(backend.saves[-1]["content"])["nodes"]["customers"] == {"note": "Admins only"}
    assert page.locator('.plan-node-strip-note').inner_text() == 'Admins only'
    count = len(backend.saves)
    note = page.locator('.plan-node-popover .plan-node-note .plan-inline-text')
    note.evaluate("(el, text) => { el.textContent = text; el.dispatchEvent(new Event('input', { bubbles: true })); }", 'x' * 501)
    assert page.locator('.plan-node-count').get_attribute('data-over') == 'true'
    page.locator('.plan-node-popover .plan-node-note .plan-inline-save').click()
    page.locator('.plan-node-popover .plan-inline-state[data-state="error"]').wait_for(timeout=4000)
    assert 'at most 500' in page.locator('.plan-node-popover .plan-inline-state').inner_text()
    assert len(backend.saves) == count                                          # never sent
    assert not errors, errors


@pytest.mark.parametrize("w,h", SIZES)
def test_image_upload_thumbnail_enlarge_and_missing(page, w, h):
    backend, errors = _open(page, w, h)
    pop = _pop(page)
    with page.expect_file_chooser() as chooser:
        pop.locator('.plan-node-image-pick').click()
    chooser.value.set_files(files=[{"name": "shot.png", "mimeType": "image/png", "buffer": PNG}])
    page.locator('.plan-node-status', has_text='Image saved').wait_for(timeout=4000)
    assert _sidecar(backend.saves[-1]["content"])["nodes"]["customers"] == {"image": "41"}   # asset id only
    thumb = page.locator('.plan-node-popover .plan-node-thumb-button img')
    thumb.wait_for()
    page.wait_for_function("() => document.querySelector('.plan-node-popover .plan-node-thumb-button img').naturalWidth > 0")
    _no_overflow(page)
    page.screenshot(path=str(OUT / f'n6-image-{w}.png'))
    page.locator('.plan-node-popover .plan-node-thumb-button').click()
    page.locator('.plan-node-popover').wait_for(state='detached')
    page.locator('#plan-preview img[alt="shot.png"]').wait_for(timeout=4000)         # existing asset viewer
    assert not errors, errors


def test_missing_image_placeholder(page):
    _open(page, content=PAGE + meta_line({"customers": {"image": "99"}}))
    page.locator('.plan-node-strip .plan-node-thumb-missing[aria-label="Image 99 is missing"]').wait_for()
    pop = _pop(page)
    assert pop.locator('.plan-node-thumb-missing').count() == 1
    page.screenshot(path=str(OUT / 'n6-missing-image-1440.png'))


def test_link_click_through_and_dangling_chip(page):
    other = {"id": 14, "item_type": "wireframe", "title": "Saved page", "content": 'screen "Saved" id=saved route=/saved\n  text "Done"\n', "content_format": "wire", "status": "draft", "revision_count": 1}
    backend, errors = _open(page, content=PAGE + meta_line({"customers": {"links": {"gone": "old"}}}), extra=[other])
    dangling = page.locator('.plan-node-strip .plan-node-link-dangling')
    dangling.wait_for()
    assert dangling.is_disabled() and 'gone' in dangling.inner_text()
    pop = _pop(page)
    pop.locator('.plan-node-link-add select').select_option('saved')
    pop.locator('.plan-node-link-add input').fill('on save')
    pop.locator('.plan-node-link-save').click()
    page.locator('.plan-node-status', has_text='Link saved').wait_for(timeout=4000)
    assert _sidecar(backend.saves[-1]["content"])["nodes"]["customers"]["links"] == {"gone": "old", "saved": "on save"}
    page.screenshot(path=str(OUT / 'n6-links-1440.png'))
    page.keyboard.press('Escape')
    page.locator('.plan-node-strip .plan-node-link:not(.plan-node-link-dangling)').click()
    page.wait_for_function("() => document.querySelector('#plan-artifact-select')?.value === '14'", timeout=4000)
    assert not errors, errors


def test_first_metadata_adds_screen_id_and_prunes_orphans(page):
    content = SAVED + meta_line({"gone": {"note": "old"}})
    backend, errors = _open(page, content=content)
    pop = _pop(page)
    pop.locator('label.plan-node-shape', has_text='Oval').click()
    page.locator('.plan-node-status', has_text='removed metadata for 1 deleted node').wait_for(timeout=4000)
    saved = backend.saves[-1]["content"]
    assert saved.startswith('screen "Saved" id=saved route=/saved\n  text "Done"\n')
    assert _sidecar(saved) == {"nodes": {"saved": {"shape": "oval"}}, "v": 1}
    assert len(backend.saves) == 1                                              # id + shape + prune in one revision
    assert not errors, errors


def test_validation_warnings_notice_is_non_blocking(page):
    backend, errors = _open(page)
    backend.warnings = ['Flow metadata for "old" has no matching node; it will be removed on the next edit.']
    pop = _pop(page)
    pop.locator('label.plan-node-shape', has_text='Circle').click()
    notice = page.locator('.plan-flow-warnings[role="status"]')
    notice.wait_for(timeout=4000)
    assert 'no matching node' in notice.inner_text()
    assert page.locator('.plan-node-popover').is_visible()                     # editing continues
    page.screenshot(path=str(OUT / 'n6-warnings-1440.png'))
    _no_overflow(page)
    page.keyboard.press('Escape')
    notice.locator('.plan-flow-warnings-close').click()
    assert page.locator('.plan-flow-warnings').count() == 0
    assert not errors, errors


def test_409_and_422(page):
    backend, errors = _open(page)
    pop = _pop(page)
    backend.reject = 422
    pop.locator('label.plan-node-shape', has_text='Square').click()
    err = page.locator('.plan-node-error:not([hidden])')
    err.wait_for(timeout=4000)
    assert 'Not saved: Unknown node shape' in err.inner_text() and backend.saves == []
    backend.reject = None
    backend.items[-1]["revision_count"] = 7                                    # someone saved meanwhile
    page.locator('label.plan-node-shape', has_text='Rectangle').click()
    page.locator('.plan-node-error .plan-node-reload').wait_for(timeout=4000)
    assert 'newer revision' in page.locator('.plan-node-error').inner_text() and backend.saves == []
    page.screenshot(path=str(OUT / 'n6-conflict-1440.png'))
    page.locator('.plan-node-reload').click()
    page.locator('.plan-node-popover').wait_for(state='detached')
    assert not errors, errors


def test_mermaid_flow_node_notes_no_shape_picker(page):
    backend, errors = _open(page, content=FLOW, fmt='mermaid', kind='flows')
    page.locator('.erd-dock .plan-node-open').click()
    page.locator('.plan-node-picker').wait_for()
    assert [t.strip() for t in page.locator('.plan-node-pick span').all_inner_texts()] == ['A', 'B']
    page.locator('.plan-node-pick', has_text='A').click()
    pop = page.locator('.plan-node-popover[role="dialog"]:not(.plan-node-picker)')
    pop.wait_for()
    assert pop.locator('.plan-node-shapes').count() == 0
    pop.locator('.plan-node-note .plan-inline-text').click()
    page.keyboard.type('Check stock')
    pop.locator('.plan-node-note .plan-inline-save').click()
    page.locator('.plan-node-status', has_text='Note saved').wait_for(timeout=4000)
    saved = backend.saves[-1]["content"]
    assert saved.startswith(FLOW) and saved.rstrip('\n').split('\n')[-1] == '%% decisions-flow: {"nodes":{"A":{"note":"Check stock"}},"v":1}'
    page.locator('.plan-node-strip-flow').wait_for()
    page.screenshot(path=str(OUT / 'n6-mermaid-1440.png'))
    assert not errors, errors


@pytest.mark.parametrize("w,h", SIZES)
def test_sitemap_cards_render_shapes(page, w, h):
    content = 'screen "Customers" id=customers route=/customers\n  heading "C"\nscreen "Saved" id=saved route=/saved\n  text "Done"\n' + meta_line({"customers": {"shape": "diamond", "note": "n"}, "saved": {"shape": "circle"}})
    b = NodeBackend(content=content)
    base = b.workspace
    b.workspace = lambda: {**base(), "sitemap": {"coverage": "complete", "note": "", "scaffolding": [], "edges": [],
                                                  "nodes": [{"id": "p-c", "kind": "page", "label": "Customers", "route": "/customers", "status": "verified"},
                                                            {"id": "p-s", "kind": "page", "label": "Saved", "route": "/saved", "status": "verified"}]}}
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.locator('.plan-sitemap-node[data-shape="diamond"]').wait_for(timeout=6000)
    circle = page.locator('.plan-sitemap-node[data-shape="circle"]').bounding_box()
    assert abs(circle["width"] - circle["height"]) <= 1, circle
    radius = page.locator('.plan-sitemap-node[data-shape="circle"]').evaluate("el => getComputedStyle(el).borderTopLeftRadius")
    assert radius.endswith('%') or float(radius.rstrip('px')) >= circle["width"] / 2 - 1, radius
    page.screenshot(path=str(OUT / f'n6-sitemap-{w}.png'))
    _no_overflow(page)
    page.locator('.plan-sitemap-node[data-shape="circle"] .plan-node-open-card').click()
    page.locator('.plan-node-popover input[value="circle"]:checked').wait_for()
    assert not errors, errors
