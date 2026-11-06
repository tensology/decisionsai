"""ERD Data tab: field TYPE dropdown, table ⋯ menu, canvas context menu, delete confirmation (fixture server, mocked APIs).

    python -m tests.ui.development_fixture_server   # port 8776
    DEVELOPMENT_TEST_URL=http://127.0.0.1:8776 pytest -m e2e_playwright tests/ui/test_erd_table_actions_playwright.py
"""
import json
from pathlib import Path

import pytest

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import Backend, PLAN_URL

pytestmark = pytest.mark.e2e_playwright
ERD = (Path(__file__).parent / 'fixtures/erd_merrypak.mmd').read_text()
OUT = Path('/tmp/planning-verify')
WIDTHS = [(1440, 900), (768, 1024), (390, 844)]


def _setup(page, w, h):
    b = Backend()
    b.items[1]["content"] = ERD
    _install_api(page, [])
    page.route("**/api/**", b.route)
    page.set_viewport_size({"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for(state='visible', timeout=10000)
    page.wait_for_timeout(250)
    return b, errors


def _select(page, name):
    page.evaluate("(n) => document.querySelector(`.erd-table[data-table=\"${n}\"]`).focus({ preventScroll: true })", name)
    page.keyboard.press('Enter')
    page.locator('.erd-panel input[data-p="rename"]').wait_for()


def _inside_viewport(page, selector):
    box = page.evaluate("(s) => { const r = document.querySelector(s).getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: innerWidth, h: innerHeight }; }", selector)
    assert box["l"] >= 0 and box["t"] >= 0 and box["r"] <= box["w"] and box["b"] <= box["h"], box
    return box


def _saved_types(content, table):
    lines = content.splitlines()
    side = json.loads(lines[-1].split(':', 1)[1])
    return lines, side["tables"][table].get("fields", {})


@pytest.mark.parametrize("w,h", WIDTHS)
def test_field_type_dropdown_round_trip(page, w, h):
    backend, errors = _setup(page, w, h)
    _select(page, 'CUSTOMER_CONTACT')
    rows = page.locator('.erd-panel [data-field]')
    base = lambda i: rows.nth(i).locator('select[data-f="type-base"]')
    param = lambda i: rows.nth(i).locator('input[data-f="type-param"]')
    assert base(0).input_value() == 'int' and base(0).get_attribute('aria-label') == 'Field type'
    assert not param(0).is_visible()
    assert base(2).input_value() == 'decimal' and param(2).input_value() == '12,2' and param(2).is_visible()
    assert backend.saves == []                                     # showing the dropdown never rewrites
    # keyboard: focus the select and change it with the keyboard
    base(1).focus()
    base(1).select_option('varchar')
    param(1).fill('80'); param(1).press('Tab')
    page.wait_for_function("() => document.querySelector('.erd-editor').dataset.saveState === 'saved'", timeout=6000)
    content = backend.saves[-1]["content"]
    lines, extras = _saved_types(content, 'CUSTOMER_CONTACT')
    assert '        varchar(80) notes_0' in lines
    assert '        decimal ref_1' in lines and extras["ref_1"]["type"] == 'decimal(12,2)'   # Mermaid-safe token + exact text in sidecar
    page.screenshot(path=str(OUT / f'data-field-type-{w}.png'))
    # reload: values come back exactly
    page.reload()
    page.get_by_role("button", name="Data", exact=True).click()
    page.locator('.erd-table').first.wait_for(state='visible', timeout=10000)
    _select(page, 'CUSTOMER_CONTACT')
    assert base(1).input_value() == 'varchar' and param(1).input_value() == '80'
    assert base(2).input_value() == 'decimal' and param(2).input_value() == '12,2'
    assert not errors, errors


def test_custom_type_is_kept_and_editable(page):
    backend, _ = _setup(page, 1440, 900)
    backend.items[1]["content"] = ERD
    page.evaluate("() => 0")
    _select(page, 'CUSTOMER_CONTACT')
    row = page.locator('.erd-panel [data-field="0"]')
    row.locator('select[data-f="type-base"]').select_option('__custom')
    custom = row.locator('input[data-f="type"]')
    assert custom.is_visible()
    custom.fill('VARCHAR2(30)'); custom.press('Tab')
    page.wait_for_function("() => document.querySelector('.erd-editor').dataset.saveState === 'saved'", timeout=6000)
    sel = page.locator('.erd-panel [data-field="0"] select[data-f="type-base"]')
    assert sel.locator('option:checked').inner_text() == 'Custom: VARCHAR2(30)'
    assert '        VARCHAR2(30) id PK "surrogate key"' in backend.saves[-1]["content"].splitlines()


@pytest.mark.parametrize("w,h", WIDTHS)
def test_panel_three_dots_menu(page, w, h):
    _setup(page, w, h)
    _select(page, 'CUSTOMER_CONTACT')
    assert page.locator('.erd-panel h4').first.inner_text() == 'Table'           # title unchanged
    assert page.locator('.erd-panel-actions').count() == 0
    more = page.locator('.erd-panel-more')
    assert more.get_attribute('aria-haspopup') == 'menu' and more.get_attribute('aria-expanded') == 'false'
    pb, mb = page.locator('.erd-panel').bounding_box(), more.bounding_box()
    assert mb['x'] + mb['width'] <= pb['x'] + pb['width'] and mb['x'] > pb['x'] + pb['width'] / 2 and mb['y'] < pb['y'] + 40   # top right
    more.click()
    menu = page.locator('.erd-menu[role="menu"]')
    assert more.get_attribute('aria-expanded') == 'true'
    items = menu.locator('[role="menuitem"]')
    assert [items.nth(i).inner_text() for i in range(items.count())] == ['View data', 'Duplicate', 'Delete']
    assert 'danger' in items.nth(2).get_attribute('class')
    color = items.nth(2).evaluate("(n) => getComputedStyle(n).color")
    assert color != items.nth(0).evaluate("(n) => getComputedStyle(n).color")
    box = _inside_viewport(page, '.erd-menu')
    assert page.evaluate("() => document.activeElement.textContent") == 'View data'
    page.keyboard.press('ArrowDown'); assert page.evaluate("() => document.activeElement.textContent") == 'Duplicate'
    page.keyboard.press('ArrowDown'); assert page.evaluate("() => document.activeElement.textContent") == 'Delete'
    page.keyboard.press('ArrowDown'); assert page.evaluate("() => document.activeElement.textContent") == 'View data'
    page.keyboard.press('End'); assert page.evaluate("() => document.activeElement.textContent") == 'Delete'
    page.keyboard.press('Home'); assert page.evaluate("() => document.activeElement.textContent") == 'View data'
    page.screenshot(path=str(OUT / f'data-table-menu-{w}.png'))
    page.keyboard.press('Escape')
    assert menu.count() == 0 and page.evaluate("() => document.activeElement.classList.contains('erd-panel-more')")
    assert more.get_attribute('aria-expanded') == 'false'
    more.click(); page.mouse.click(5, h - 5)                         # outside click closes
    assert menu.count() == 0
    more.click(); page.keyboard.press('ArrowDown'); page.keyboard.press('Enter')  # Duplicate
    page.locator('.erd-table[data-table="CUSTOMER_CONTACT_copy"]').wait_for()
    print('MEASURE menu', w, json.dumps(box))


@pytest.mark.parametrize("w,h", WIDTHS)
def test_canvas_context_menu(page, w, h):
    _setup(page, w, h)
    node = page.locator('.erd-table[data-table="CUSTOMER_CONTACT"]')
    # right-click at the very bottom-right corner of the window: menu must flip/clamp inside
    page.evaluate("""() => { const n = document.querySelector('.erd-table[data-table="CUSTOMER_CONTACT"] ul');
        n.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: innerWidth - 3, clientY: innerHeight - 3, button: 2 })); }""")
    menu = page.locator('.erd-menu[role="menu"]')
    items = menu.locator('[role="menuitem"]')
    assert [items.nth(i).inner_text() for i in range(items.count())] == ['Edit', 'View data', 'Duplicate', 'Delete']
    box = _inside_viewport(page, '.erd-menu')
    assert 'selected' in node.get_attribute('class')
    assert page.evaluate("() => document.querySelector('.erd-menu').classList.contains('board-context-menu')")
    page.screenshot(path=str(OUT / f'data-table-context-menu-{w}.png'))
    page.keyboard.press('Escape'); assert menu.count() == 0
    # scroll (wheel) closes
    page.evaluate("""() => document.querySelector('.erd-table[data-table="CUSTOMER_CONTACT"] ul').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 30, clientY: 30 }))""")
    assert menu.count() == 1
    _inside_viewport(page, '.erd-menu')
    page.evaluate("() => document.querySelector('.erd-viewport').dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 40 }))")
    assert menu.count() == 0
    # keyboard: Shift+F10 and ContextMenu on a focused table; Edit focuses the panel
    page.evaluate("() => document.querySelector('.erd-table[data-table=\"CUSTOMER_CONTACT\"]').focus({ preventScroll: true })")
    page.keyboard.press('Shift+F10'); assert menu.count() == 1
    page.keyboard.press('Escape')
    assert page.evaluate("() => document.activeElement.dataset.table") == 'CUSTOMER_CONTACT'
    page.keyboard.press('ContextMenu'); assert menu.count() == 1
    page.keyboard.press('Enter')                                      # Edit
    assert page.evaluate("() => document.activeElement.dataset.p") == 'rename'
    assert page.locator('.erd-panel input[data-p="rename"]').input_value() == 'CUSTOMER_CONTACT'
    print('MEASURE context', w, json.dumps(box))


def _open_delete(page, entry):
    if entry == 'panel':
        _select(page, 'CUSTOMER_CONTACT')
        page.locator('.erd-panel-more').click()
        page.locator('.erd-menu [role="menuitem"]', has_text='Delete').click()
    elif entry == 'context':
        page.evaluate("""() => document.querySelector('.erd-table[data-table="CUSTOMER_CONTACT"] ul').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 200 }))""")
        page.locator('.erd-menu [role="menuitem"]', has_text='Delete').click()
    else:
        page.evaluate("() => document.querySelector('.erd-table[data-table=\"CUSTOMER_CONTACT\"]').focus({ preventScroll: true })")
        page.keyboard.press('Delete')
    dialog = page.locator('#decisions-confirm-modal')
    dialog.wait_for(state='visible', timeout=3000)
    return dialog


@pytest.mark.parametrize("entry", ['panel', 'context', 'key'])
@pytest.mark.parametrize("w,h", WIDTHS)
def test_delete_confirmation_all_entry_points(page, w, h, entry):
    backend, errors = _setup(page, w, h)
    node = page.locator('.erd-table[data-table="CUSTOMER_CONTACT"]')
    dialog = _open_delete(page, entry)
    assert 'CUSTOMER_CONTACT' in dialog.inner_text()
    _inside_viewport(page, '#decisions-confirm-modal .decisions-confirm-modal')
    if entry == 'panel':
        page.screenshot(path=str(OUT / f'data-delete-confirm-{w}.png'))
    dialog.locator('.decisions-confirm-cancel').click()
    assert dialog.count() == 0 and node.count() == 1                  # cancel keeps it
    page.wait_for_timeout(1500)
    assert all('CUSTOMER_CONTACT {' in s["content"] for s in backend.saves)
    dialog = _open_delete(page, entry)
    dialog.locator('.decisions-confirm-ok').click()
    node.wait_for(state='detached')
    assert page.locator('.erd-table').count() == 50
    page.wait_for_function("() => document.querySelector('.erd-editor').dataset.saveState === 'saved'", timeout=6000)
    assert 'CUSTOMER_CONTACT {' not in backend.saves[-1]["content"]
    assert not errors, errors
