"""Interfaces chrome matches the Data canvas: bottom-left dock (same component, inset, height, styling),
top-right call-out, Advanced drawer geometry; dock stays pinned while the page content scrolls / re-renders.
Mocked APIs on the fixture server. Screenshots: /tmp/planning-verify/ifaces-match-*.png."""
import json

import pytest

from tests.ui.test_plan_canvas_chrome_playwright import OUT, SIZES, _open, backend, box, inside  # noqa: F401

pytestmark = pytest.mark.e2e_playwright
STYLE = "(s) => { const c = getComputedStyle(document.querySelector(s)); return [c.borderRadius, c.backgroundColor, c.boxShadow, c.paddingTop, c.height]; }"
SINGLE = 'screen "Customers" id=customers route=/customers\n#| The customer list.\n  heading "Customers"\n  button "Save" variant=primary\n'


def corner(dock, surface):
    return round(dock['x'] - surface['x'], 1), round(surface['y'] + surface['height'] - (dock['y'] + dock['height']), 1)


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}")
def test_interfaces_dock_matches_data_dock(page, backend, size):
    _open(page, "Data", '.erd-table', size)
    data_dock, data_canvas, data_callout = box(page, '.erd-dock-left'), box(page, '.erd-viewport'), box(page, '.erd-callout')
    data_style = page.evaluate(STYLE, '.erd-dock-left')
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.locator('.plan-interfaces-tools .plan-advanced-open').wait_for(state='visible', timeout=10000)
    page.wait_for_timeout(350)
    dock, surface, callout = box(page, '.plan-interfaces-tools'), box(page, '#plan-preview'), box(page, '.plan-canvas-callout')
    assert inside(dock, surface, pad=4), (dock, surface)
    dx, dy = corner(dock, surface)
    ddx, ddy = corner(data_dock, data_canvas)
    assert abs(dx - ddx) <= 2 and abs(dy - ddy) <= 2, ((dx, dy), (ddx, ddy))          # same inset as Data
    assert abs(dock['height'] - data_dock['height']) <= 1
    assert page.evaluate(STYLE, '.plan-interfaces-tools')[:4] == data_style[:4]        # same chrome
    assert inside(callout, surface, pad=4)
    assert abs((surface['x'] + surface['width'] - callout['x'] - callout['width']) - (data_canvas['x'] + data_canvas['width'] - data_callout['x'] - data_callout['width'])) <= 24
    assert page.locator('.plan-preview-toolbar .plan-advanced-open').count() == 0        # no longer above the surface
    page.screenshot(path=str(OUT / f'ifaces-match-{size[0]}.png'))
    page.locator('#plan-preview').evaluate('n => n.scrollTop = n.scrollHeight')        # content scrolls, dock stays
    page.wait_for_timeout(200)
    assert box(page, '.plan-interfaces-tools') == dock
    page.locator('.plan-interfaces-tools .plan-advanced-open').click()
    page.locator('.plan-drawer').wait_for()
    page.wait_for_timeout(250)
    d = box(page, '.plan-drawer')
    assert abs(d['x'] + d['width'] - size[0]) <= 1 and d['height'] >= size[1] - 1
    page.screenshot(path=str(OUT / f'ifaces-match-drawer-{size[0]}.png'))
    page.keyboard.press('Escape')
    page.wait_for_timeout(250)
    assert page.evaluate("() => document.activeElement.classList.contains('plan-advanced-open')")


def test_single_page_dock_holds_node_opener_and_survives_tab_switch(page, backend):
    backend.items[2]["content"] = SINGLE
    _open(page, "Interfaces", '.plan-interfaces-tools', (1440, 900))
    assert page.locator('.plan-interfaces-tools .plan-node-open').count() == 1
    assert page.locator('.plan-interfaces-tools').count() == 1
    page.screenshot(path=str(OUT / 'ifaces-match-single-page.png'))
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for()
    assert page.locator('.plan-interfaces-tools').count() == 0                         # cleaned up with the tab
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.locator('.plan-interfaces-tools').wait_for()
    assert page.locator('.plan-interfaces-tools').count() == 1


def test_rebuild_interfaces_shows_summary_and_scan_warnings(page, backend):
    backend.provider = "openrouter"
    backend.model = "selected-model"
    _open(page, "Interfaces", '#plan-rebuild-sitemap', (1440, 900))
    calls = []

    def rebuild(route):
        calls.append(json.loads(route.request.post_data or "{}"))
        return route.fulfill(json={"action": "rebuilt", "workspace": backend.workspace(), "updated": ["flows"],
                                   "validation_warnings": ['wireframe: Flow metadata for "ghost" has no matching node; it was kept.', 'nosuchtype: ignored'],
                                   "message": "Interfaces: 2 regenerated, 1 added, 1 kept (hand-made), 1 stale."})
    page.route("**/api/workflows/studio/plan-workspaces/5/rebuild-interfaces", rebuild)
    item = page.locator('#plan-rebuild-sitemap')
    assert item.is_visible()
    item.click()
    page.get_by_text('Interfaces: 2 regenerated, 1 added, 1 kept (hand-made), 1 stale.').wait_for(timeout=8000)
    notice = page.locator('.plan-flow-warnings')
    notice.wait_for()
    assert 'Flow metadata for "ghost" has no matching node' in notice.inner_text() and 'ignored' not in notice.inner_text()
    assert calls == [{"provider": "openrouter", "model_name": "selected-model", "chat_id": None}]
    page.screenshot(path=str(OUT / 'ifaces-rebuild-summary.png'))


def test_node_popover_rename_id(page, backend):
    backend.items[2]["content"] = SINGLE + '# decisions-flow: {"nodes":{"customers":{"note":"Admins"}},"v":1}\n'
    _open(page, "Interfaces", '.plan-interfaces-tools', (1440, 900))
    page.locator('.plan-interfaces-tools .plan-node-open').click()
    pop = page.locator('.plan-node-popover')
    pop.wait_for()
    b, vp = box(page, '.plan-node-popover'), page.viewport_size
    assert b['y'] >= 0 and b['y'] + b['height'] <= vp['height'] + 0.5                    # opens upwards from the dock
    pop.locator('.plan-node-rename input').fill('clients')
    pop.locator('.plan-node-rename input').press('Enter')
    page.get_by_text('Id renamed to clients.').wait_for()
    saved = backend.saves[-1]["content"]
    assert 'id=clients' in saved and '"clients":{"note":"Admins"}' in saved and '#| The customer list.' in saved
    pop.locator('.plan-node-rename input').fill('9x')
    pop.locator('.plan-node-rename-save').click()
    assert 'letter first' in pop.locator('.plan-node-error').inner_text()
    assert len(backend.saves) == 1
    page.screenshot(path=str(OUT / 'ifaces-node-rename.png'))
