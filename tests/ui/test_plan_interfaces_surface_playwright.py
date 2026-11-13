"""Interfaces tab surface: the gridded canvas surface and the "Interfaces summary" call-out survive the wire
renderers (which replace the preview contents) and also appear on the empty state. Fixture server, mocked APIs.

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_plan_interfaces_surface_playwright.py
"""
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL
from tests.ui.test_plan_canvas_chrome_playwright import WIRE

pytestmark = pytest.mark.e2e_playwright
OUT = Path('/tmp/planning-verify')
SIZES = [(1440, 900), (768, 1024), (390, 844)]
ONE_PAGE = 'screen "Customers" id=customers route=/customers\n  heading "Customers"\n  button "Save" variant=primary\n'


def _open(page, w, h, content, surface):
    b = Backend()
    b.items = [i for i in b.items if i["item_type"] not in ("wireframe", "flows")]
    if content:
        b.items.append({"id": 13, "item_type": "wireframe", "title": "Customer pages", "content": content, "content_format": "wire", "status": "draft", "revision_count": 1})
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": w, "height": h})
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Interfaces", exact=True).click()
    surface.wait_for()
    page.wait_for_timeout(900)                     # let every async wire render finish replacing the contents


def _check(page):
    g = page.evaluate("""() => {
        const s = document.querySelector('#plan-preview'), c = s.querySelector('.iface-map-status');
        const grid = s.querySelector('.iface-viewport') || s;
        const rs = s.getBoundingClientRect(), rc = c ? c.getBoundingClientRect() : null;
        const top = rc ? document.elementFromPoint(rc.left + rc.width / 2, rc.top + rc.height / 2) : null;
        const pane = document.querySelector('#plan-artifacts').getBoundingClientRect();
        return { surface: [rs.left, rs.top, rs.width, rs.height], callout: rc && [rc.left, rc.top, rc.width, rc.height],
                 uncovered: Boolean(top && c.contains(top)), grid: getComputedStyle(grid).backgroundImage, paneBottom: pane.bottom, surfaceBottom: rs.bottom,
                 banner: Boolean(s.querySelector('.plan-canvas-callout')), map: Boolean(s.querySelector('.iface-viewport')) };
    }""")
    s, c = g["surface"], g["callout"]
    assert not g["banner"], g                                 # the old summary banner is gone (planning fixes 12 Nov)
    if g["map"]:                                              # the map carries a quiet one-line status instead
        assert c and c[2] > 0 and c[3] > 0, g
        assert s[0] - .5 <= c[0] and c[0] + c[2] <= s[0] + s[2] + .5 and s[1] - .5 <= c[1] and c[1] + c[3] <= s[1] + s[3] + .5, g
        assert g["uncovered"], g
    assert 'radial-gradient' in g["grid"], g
    assert -1 <= g["paneBottom"] - g["surfaceBottom"] <= 24, g
    return g


@pytest.mark.parametrize("w,h", SIZES)
@pytest.mark.parametrize("kind", ["populated", "single", "empty"])
def test_interfaces_surface_and_callout_visible(page, w, h, kind):
    surface = page.locator('#plan-preview .iface-viewport' if kind == 'populated' else '#plan-preview.plan-canvas-surface')
    _open(page, w, h, {"populated": WIRE, "single": ONE_PAGE, "empty": ""}[kind], surface)
    if kind == "empty":
        assert page.locator('#plan-preview .plan-canvas-empty').is_visible()
    elif kind == 'populated':
        assert page.locator('#plan-preview .iface-node').count() >= 2
    else:
        kids = page.evaluate("() => [...document.querySelector('#plan-preview').children].map((n) => n.className || n.tagName)")
        assert not any('plan-canvas-callout' in str(k) for k in kids), kids
        assert len(kids) >= 1, kids                                   # the rendered page content is still there
    g = _check(page)
    page.set_viewport_size({"width": w, "height": h - 60}); page.wait_for_timeout(400)      # resize keeps it
    _check(page)
    page.set_viewport_size({"width": w, "height": h}); page.wait_for_timeout(400)
    suffix = {"populated": "", "single": "-single", "empty": "-empty"}[kind]
    page.screenshot(path=str(OUT / f'interfaces-chrome-fixed{suffix}-{w}.png'))
    print('MEASURE', kind, w, json.dumps(g))
    page.get_by_role("button", name="Data", exact=True).click(); page.wait_for_timeout(300)
    page.get_by_role("button", name="Interfaces", exact=True).click(); page.wait_for_timeout(600)
    assert page.locator('#plan-preview .plan-canvas-callout').count() == 0   # no banner comes back on return
    assert surface.is_visible()
