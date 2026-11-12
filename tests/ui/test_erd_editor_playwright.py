"""ERD editor (Data tab) on the fixture server with a 51-entity Merrypak-sized ERD (mocked APIs, no DB).

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_erd_editor_playwright.py
"""
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api, TEST_BASE_URL
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
ERD = (Path(__file__).parent / 'fixtures/erd_merrypak.mmd').read_text()


@pytest.fixture
def backend(page):
    b = Backend()
    b.items[1]["content"] = ERD
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": 1440, "height": 900})
    return b


def _open_data(page):
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for(state='visible', timeout=10000)
    page.wait_for_timeout(200)


def test_data_tab_is_canvas_only_with_readonly_advanced(page, backend):
    _open_data(page)
    assert page.locator('.erd-table').count() == 51
    assert page.locator('.erd-rel').count() == 30
    assert page.locator('#plan-preview textarea, #plan-preview .plan-inline-text').count() == 0
    assert page.locator('details.plan-advanced').count() == 0               # no inline Advanced section
    assert page.locator('.plan-drawer').count() == 0                         # drawer closed by default
    assert 'read-only' in page.locator('.plan-advanced-open').get_attribute('aria-label')
    assert page.locator('.erd-warnings').is_visible()           # unrepresentable lines are reported, kept
    assert backend.saves == []                                   # opening never rewrites the item


def test_add_table_field_relation_zoom_and_saved_mermaid(page, backend):
    _open_data(page)
    zoom = page.locator('.erd-zoom')
    before = zoom.inner_text()
    page.locator('[data-erd="zoom-in"]').click()
    assert zoom.inner_text() != before
    page.locator('[data-erd="fit"]').click()
    page.locator('[data-erd="add-table"]').click()
    node = page.locator('.erd-table[data-table="NEW_TABLE"]')
    node.wait_for()
    page.locator('.erd-panel input[data-p="rename"]').fill('LOYALTY_CARD')
    page.locator('.erd-panel input[data-p="rename"]').press('Tab')
    node = page.locator('.erd-table[data-table="LOYALTY_CARD"]')
    node.wait_for()
    node.locator('[data-add-field]').click()
    field = page.locator('.erd-panel [data-field="1"] input[data-f="name"]')
    field.fill('points')
    field.press('Tab')
    page.locator('.erd-panel [data-field="1"] select[data-f="type-base"]').select_option('int')
    # draw a relation: drag the table handle onto CUSTOMER
    handle = node.locator('[data-handle]').bounding_box()
    target = page.locator('.erd-table[data-table="CUSTOMER"]').bounding_box()
    page.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
    page.mouse.down()
    page.mouse.move(target['x'] + target['width'] / 2, target['y'] + 4, steps=8)
    page.mouse.up()
    page.locator('.erd-panel select[data-r="preset"]').select_option(label='Many to many')
    page.locator('.erd-panel input[data-r="label"]').fill('earned by')
    page.locator('.erd-panel input[data-r="label"]').press('Tab')
    page.locator('.erd-editor[data-save-state="saved"]').wait_for(timeout=6000)
    page.locator('.erd-tick').wait_for(state='attached')
    saved = backend.saves[-1]["content"]
    assert 'LOYALTY_CARD {' in saved and 'int points' in saved
    assert 'LOYALTY_CARD }o--o{ CUSTOMER : "earned by"' in saved
    assert 'style CUSTOMER fill:#f97316' in saved and "weird line ??? that mermaid parser here can't map" in saved
    assert saved.strip().splitlines()[-1].startswith('%% decisions-erd: ')
    side = json.loads(saved.strip().splitlines()[-1].split(':', 1)[1])
    assert side["tables"]["LOYALTY_CARD"]["interfaces"] == [] and "view" in side
    # undo restores the previous model, then autosaves again
    page.locator('[data-erd="undo"]').click()
    page.locator('.plan-advanced-open').click()
    assert 'LOYALTY_CARD' in page.locator('.plan-drawer .plan-advanced-source').inner_text()
    page.keyboard.press('Escape')


def test_drag_table_moves_and_persists_position(page, backend):
    _open_data(page)
    page.get_by_label('Fit to view', exact=True).click()
    node = page.locator('.erd-table[data-table="ORDER"]')
    box = node.locator('.erd-table-head').bounding_box()
    page.mouse.move(box['x'] + 20, box['y'] + box['height'] / 2)
    page.mouse.down()
    page.mouse.move(box['x'] + 140, box['y'] + 90, steps=12)
    page.mouse.up()
    page.locator('.erd-editor[data-save-state="saved"]').wait_for(timeout=6000)
    side = json.loads(backend.saves[-1]["content"].strip().splitlines()[-1].split(':', 1)[1])
    page.reload()
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table[data-table="ORDER"]').wait_for()
    t = page.locator('.erd-table[data-table="ORDER"]').evaluate('n => n.style.transform')
    assert t == f'translate({side["tables"]["ORDER"]["x"]}px, {side["tables"]["ORDER"]["y"]}px)'
