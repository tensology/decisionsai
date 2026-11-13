"""Canvas chrome on the Data (ERD) and Interfaces tabs: overlay placement at 1440 / 768 / 390, the
dismissable call-out, the Advanced source drawer (modal, Escape, focus trap, focus return), and the
canvas filling the window. Mocked APIs on the fixture server (no DB). Screenshots: /tmp/planning-verify/.

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_canvas_chrome_playwright.py
"""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
ERD = (Path(__file__).parent / 'fixtures/erd_merrypak.mmd').read_text()
WIRE = '''screen "Customers" id=customers route=/customers
  heading "Customers"
  button "Save" variant=primary
screen "Saved" id=saved route=/saved
  heading "Saved"
'''
SIZES = [(1440, 900), (768, 1024), (390, 844)]
MEASURE = {}


def record(key, value):
    MEASURE[key] = value
    with (OUT / 'chrome-measurements.jsonl').open('a') as fh:
        fh.write(json.dumps({key: value}) + '\n')


@pytest.fixture
def backend(page):
    b = Backend()
    b.items[1]["content"] = ERD
    b.items.append({"id": 13, "item_type": "wireframe", "title": "Customer pages", "content": WIRE, "content_format": "wire", "status": "draft", "revision_count": 1})
    _install_api(page, [])
    page.route("**/api/**", b.route)
    OUT.mkdir(parents=True, exist_ok=True)
    return b


def box(page, selector):
    b = page.locator(selector).first.bounding_box()
    assert b is not None, selector
    return {k: round(v, 1) for k, v in b.items()}


def inside(inner, outer, pad=0):
    return inner['x'] >= outer['x'] + pad - 0.5 and inner['y'] >= outer['y'] + pad - 0.5 and \
        inner['x'] + inner['width'] <= outer['x'] + outer['width'] - pad + 0.5 and inner['y'] + inner['height'] <= outer['y'] + outer['height'] - pad + 0.5


def disjoint(a, b):
    return a['x'] + a['width'] <= b['x'] or b['x'] + b['width'] <= a['x'] or a['y'] + a['height'] <= b['y'] or b['y'] + b['height'] <= a['y']


def source_gap(page):
    # bottom edge of the read-only source vs the drawer's content (padding) bottom edge
    return page.evaluate("""() => { const pre = document.querySelector('.plan-drawer .plan-advanced-source');
        const box = pre.parentElement, cs = getComputedStyle(box), p = pre.getBoundingClientRect(), b = box.getBoundingClientRect();
        const inner = b.bottom - parseFloat(cs.paddingBottom) - parseFloat(cs.borderBottomWidth);
        return { pre_top: Math.round(p.top), pre_bottom: Math.round(p.bottom * 10) / 10, pre_height: Math.round(p.height), container_inner_bottom: Math.round(inner * 10) / 10, gap: Math.round((inner - p.bottom) * 10) / 10, window: innerHeight }; }""")


def _open(page, tab, ready, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(PLAN_URL)
    page.evaluate("() => sessionStorage.clear()")
    page.goto(PLAN_URL)
    page.get_by_role("button", name=tab, exact=True).click()
    page.locator(ready).first.wait_for(state='visible', timeout=10000)
    page.wait_for_timeout(350)


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}")
def test_data_chrome_corners_no_overlap_and_fills_window(page, backend, size):
    _open(page, "Data", '.erd-table', size)
    canvas = box(page, '.erd-viewport')
    body = box(page, '.erd-body')
    left, right, callout = box(page, '.erd-dock-left'), box(page, '.erd-dock-right'), box(page, '.erd-callout')
    for name, b in (('left', left), ('right', right)):
        assert inside(b, canvas, pad=4), (name, b, canvas)
    # corners: bottom-left and bottom-right docks. The summary is a full-width bar above the canvas.
    assert left['x'] - canvas['x'] <= 14 and canvas['y'] + canvas['height'] - (left['y'] + left['height']) <= 14
    assert canvas['x'] + canvas['width'] - (right['x'] + right['width']) <= 14 and canvas['y'] + canvas['height'] - (right['y'] + right['height']) <= 14
    assert abs(callout['x'] - body['x']) <= 2 and abs(callout['width'] - body['width']) <= 2, (callout, body)
    assert callout['y'] + callout['height'] <= canvas['y'] + 1
    assert disjoint(left, right) and disjoint(left, callout) and disjoint(right, callout) and disjoint(callout, canvas)
    # the canvas fills the space left (no fixed height): its bottom sits within 24px of the artifacts pane
    # bottom, which is the window at 1440 and the top of the stacked chat panel at 768 / 390
    pane = box(page, '#plan-artifacts')
    assert -1 <= pane['y'] + pane['height'] - (canvas['y'] + canvas['height']) <= 24, (canvas, pane)
    assert size[0] < 1000 or size[1] - (canvas['y'] + canvas['height']) <= 24
    # no persistent save badge in the chrome
    assert page.locator('.erd-tick').is_hidden() and page.locator('.erd-alert').is_hidden()
    for label in ("Add table", "Undo", "Redo", "Zoom in", "Zoom out", "Fit to view", "Dismiss summary"):
        assert page.get_by_label(label, exact=True).count() >= 1, label
    page.screenshot(path=str(OUT / f'data-chrome-{size[0]}.png'))
    record(f'data-{size[0]}', {"canvas": canvas, "left": left, "right": right, "callout": callout, "zoom": page.locator('.erd-zoom').inner_text()})
    # Advanced drawer: closed by default, opens from the dock, modal + read-only, Escape closes, focus returns
    opener = page.locator('.plan-advanced-open')
    assert page.locator('.plan-drawer').count() == 0
    opener.click()
    drawer = page.locator('.plan-drawer')
    drawer.wait_for()
    page.wait_for_timeout(250)
    assert drawer.get_attribute('aria-modal') == 'true' and drawer.locator('textarea, [contenteditable="true"]').count() == 0
    d = box(page, '.plan-drawer')
    assert abs(d['x'] + d['width'] - size[0]) <= 1 and d['height'] >= size[1] - 1      # right-side, full height
    for _ in range(4):                                                                  # focus trap
        page.keyboard.press('Tab')
        assert page.evaluate("() => !!document.activeElement.closest('.plan-drawer')")
    page.screenshot(path=str(OUT / f'data-advanced-open-{size[0]}.png'))
    gap = source_gap(page)
    assert abs(gap['gap']) <= 1, gap                                        # source text box fills the drawer
    page.screenshot(path=str(OUT / f'data-advanced-drawer-{size[0]}.png'))
    page.set_viewport_size({"width": size[0], "height": size[1] - 180})     # resize while open
    page.wait_for_timeout(250)
    gap_resized = source_gap(page)
    assert abs(gap_resized['gap']) <= 1, gap_resized
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.wait_for_timeout(250)
    record(f'data-drawer-{size[0]}', {"drawer": d, "source": gap, "after_resize": gap_resized})
    page.keyboard.press('Escape')
    page.locator('.plan-drawer-backdrop').wait_for(state='detached')
    assert page.evaluate("() => document.activeElement.classList.contains('plan-advanced-open')")
    # close button + backdrop also close it
    opener.click(); page.locator('.plan-drawer-close').click(); page.locator('.plan-drawer-backdrop').wait_for(state='detached')
    opener.click(); page.wait_for_timeout(250); page.mouse.click(5, size[1] // 2); page.locator('.plan-drawer-backdrop').wait_for(state='detached')


def test_callout_dismisses_for_the_session_and_canvas_stays_interactive(page, backend):
    _open(page, "Data", '.erd-table', (1440, 900))
    callout = page.locator('.erd-callout')
    assert '51 tables, 30 relations' in callout.inner_text()
    area = box(page, '.erd-callout')
    page.get_by_label("Dismiss summary").click()
    assert callout.is_hidden()
    # the area the call-out covered is canvas again: a drag there pans the view
    before = page.locator('.erd-stage').evaluate('n => n.style.transform')
    page.mouse.move(area['x'] + 40, area['y'] + 20)
    page.mouse.wheel(0, 120)                                        # wheel over that spot pans the canvas
    page.wait_for_timeout(100)
    assert page.locator('.erd-stage').evaluate('n => n.style.transform') != before
    hit = page.evaluate(f"() => document.elementFromPoint({area['x'] + 40}, {area['y'] + 20}).closest('.erd-viewport') !== null")
    assert hit
    # stays closed in-session: re-render (tab switch) and a full reload
    page.get_by_role("button", name="Documents", exact=True).click()
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for()
    assert page.locator('.erd-callout').is_hidden()
    page.reload()
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for()
    assert page.locator('.erd-callout').is_hidden()
    # a save flashes the green tick briefly, then it goes away (never persistent)
    page.locator('[data-erd="add-table"]').click()
    page.locator('.erd-tick').wait_for(state='visible', timeout=6000)
    page.locator('.erd-tick').wait_for(state='hidden', timeout=4000)


@pytest.mark.parametrize("size", [*SIZES, (320, 900)], ids=lambda s: f"{s[0]}")
def test_interfaces_surface_fills_window_with_callout(page, backend, size):
    _open(page, "Interfaces", '.iface-viewport', size)
    surface = box(page, '.iface-viewport')
    body = box(page, '.iface-canvas .erd-body')
    assert page.locator('.iface-canvas .plan-canvas-callout').count() == 0             # banner retired (planning fixes)
    callout = box(page, '.iface-canvas .iface-map-status')                              # quiet one-line status instead
    assert inside(callout, box(page, '.iface-canvas'), pad=4), (callout, body)
    pane = box(page, '#plan-artifacts')
    assert -1 <= pane['y'] + pane['height'] - (surface['y'] + surface['height']) <= 24, (surface, pane)
    edit = box(page, '.iface-canvas .erd-dock-left')
    zoom = box(page, '.iface-canvas .erd-dock-right')
    assert inside(edit, surface, pad=4) and inside(zoom, surface, pad=4) and disjoint(edit, zoom)
    pages = page.get_by_role('list', name='Pages', exact=True).get_by_role('listitem')
    assert pages.count() == 2
    assert any('Customers' in name for name in pages.all_text_contents())
    page.screenshot(path=str(OUT / f'interfaces-chrome-{size[0]}.png'), animations='disabled')
    record(f'interfaces-{size[0]}', {"surface": surface, "callout": callout})
    assert pages.count() == 2
    page.wait_for_function("""() => {
        const pane = document.querySelector('#plan-artifacts').getBoundingClientRect();
        const canvas = document.querySelector('.iface-viewport').getBoundingClientRect();
        return pane.bottom - canvas.bottom >= -1 && pane.bottom - canvas.bottom <= 24;
    }""")
    page.screenshot(path=str(OUT / f'interfaces-dismissed-{size[0]}.png'), animations='disabled')


def test_resize_refills_and_repacks_unsaved_layout(page, backend):
    _open(page, "Data", '.erd-table', (1440, 900))
    zooms = {}
    for w, h in ((1440, 900), (1440, 640), (390, 844), (768, 1024)):
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(450)
        c = box(page, '.erd-viewport')
        pane = box(page, '#plan-artifacts')
        assert -1 <= pane['y'] + pane['height'] - (c['y'] + c['height']) <= 24 or c['height'] == 220, (w, h, c, pane)
        zooms[f'{w}x{h}'] = page.locator('.erd-zoom').inner_text()
        overlaps = page.evaluate("""() => { const r = [...document.querySelectorAll('.erd-table')].map(n => n.getBoundingClientRect()); let c = 0;
            for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) { const a = r[i], b = r[j]; if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) c++; } return c; }""")
        assert overlaps == 0
    record('resize-zoom', zooms)
    (OUT / 'chrome-measurements.json').write_text(json.dumps(MEASURE, indent=1))
    print('CHROMEMEASURE', json.dumps(MEASURE))


def test_data_docks_are_separate_on_a_320px_screen(page, backend):
    _open(page, "Data", '.erd-table', (320, 900))
    canvas = box(page, '.erd-viewport')
    edit = box(page, '.erd-dock-left')
    zoom = box(page, '.erd-dock-right')
    assert inside(edit, canvas, pad=4) and inside(zoom, canvas, pad=4)
    assert disjoint(edit, zoom)
    level = page.locator('.erd-zoom')
    initial = level.inner_text()
    page.get_by_label('Zoom in', exact=True).click()
    assert level.inner_text() != initial
    page.get_by_label('Zoom out', exact=True).click()
    page.screenshot(path=str(OUT / 'data-chrome-320.png'), animations='disabled')
    tables = page.locator('.erd-table')
    count = tables.count()
    page.get_by_label('Add table', exact=True).click()
    assert tables.count() == count + 1
    page.screenshot(path=str(OUT / 'data-edit-320.png'), animations='disabled')


@pytest.mark.parametrize('size', [(390, 844), (768, 1024), (1280, 900), (1440, 900)])
def test_dense_data_diagram_opens_readably_and_can_fit(page, backend, size):
    backend.items[1]['content'] = 'erDiagram\n' + '\n'.join(
        f'    DEVICE_{n:02d} ||--o{{ DEVICE_{n+1:02d} : tracks' for n in range(82)
    )
    _open(page, 'Data', '.erd-table', size)
    viewport = page.get_by_label('ERD canvas', exact=True)
    zoom = page.locator('.erd-zoom')
    expect(zoom).to_have_text('100%', timeout=1500)
    stage = viewport.locator('.erd-stage')
    visible_table = viewport.locator('.erd-table-head').evaluate_all("""heads => {
        const canvas = heads[0].closest('.erd-viewport').getBoundingClientRect();
        return heads.find(head => {
            const box = head.getBoundingClientRect();
            return box.left >= canvas.left && box.right <= canvas.right && box.top >= canvas.top && box.bottom <= canvas.bottom;
        })?.dataset.drag;
    }""")
    assert visible_table
    page.screenshot(path=str(OUT / f'dense-data-start-{size[0]}.png'), animations='disabled')
    header = viewport.get_by_role('group', name=f'Table {visible_table}', exact=True).locator('.erd-table-head')
    header.click()
    details = page.get_by_label('Selection details', exact=True)
    expect(details.get_by_label('Table name', exact=True)).to_have_value(visible_table)
    assert inside(box(page, '.erd-panel[data-mode="edit"]'), box(page, '.erd-viewport'))
    page.screenshot(path=str(OUT / f'dense-data-details-{size[0]}.png'), animations='disabled')
    details.get_by_role('button', name='Close selection details').click()
    expect(viewport).to_be_focused()
    expect(details).to_be_hidden()
    message = page.get_by_role('textbox', name='Message', exact=True)
    message.fill('Review the device schema.')
    viewport.focus()
    before = stage.get_attribute('style')
    viewport.press('ArrowRight')
    assert stage.get_attribute('style') != before
    viewport.press('ArrowLeft')
    assert stage.get_attribute('style') == before
    page.get_by_label('Fit to view', exact=True).click()
    assert int(zoom.inner_text().rstrip('%')) < 65
    page.screenshot(path=str(OUT / f'dense-data-fit-{size[0]}.png'), animations='disabled')
    page.get_by_role('button', name='Documents', exact=True).click()
    page.get_by_role('button', name='Data', exact=True).click()
    assert int(zoom.inner_text().rstrip('%')) < 65
    expect(message).to_have_value('Review the device schema.')
    assert backend.saves == []


def test_dense_data_diagram_respects_saved_view(page, backend):
    backend.items[1]['content'] = 'erDiagram\n' + '\n'.join(
        f'    DEVICE_{n:02d} ||--o{{ DEVICE_{n+1:02d} : tracks' for n in range(24)
    ) + '\n%% decisions-erd: {"v":1,"view":{"zoom":0.45,"x":32,"y":48}}\n'
    _open(page, 'Data', '.erd-table', (390, 844))
    expect(page.locator('.erd-zoom')).to_have_text('45%')
    expect(page.locator('.erd-stage')).to_have_attribute('style', 'transform: translate(32px, 48px) scale(0.45);')
    assert backend.saves == []
