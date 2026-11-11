"""A successful scan must not imply that it understands an unsupported app."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('coverage', ['none', 'partial'])
def test_scan_coverage_is_visible_without_displacing_plan_actions(page, width, coverage):
    backend = Backend()
    original = backend.workspace
    backend.workspace = lambda: {**original(), 'scan': {'state': 'done', 'coverage': coverage}}
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(PLAN_URL)
    hint = page.locator('.plan-scan-hint')
    expect(hint).to_contain_text('could not map' if coverage == 'none' else 'partial')
    expect(hint).to_contain_text('before building tickets')
    bounds = hint.bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['y'] >= 0
    assert bounds['x'] + bounds['width'] <= width
    assert bounds['y'] + bounds['height'] <= 900
    assert hint.evaluate('n => n.scrollWidth <= n.clientWidth'), 'The coverage warning must remain readable'
    expect(page.get_by_role('button', name='Build tickets', exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    out = Path('/tmp/decisions-development-usability')
    out.mkdir(exist_ok=True)
    page.screenshot(path=str(out / f'scan-{coverage}-{width}.png'))
