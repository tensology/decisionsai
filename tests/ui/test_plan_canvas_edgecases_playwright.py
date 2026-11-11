"""Human-readable Interfaces navigation, using real UI and mocked project APIs."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_plan_inline_edit_playwright import PLAN_URL
from tests.ui.test_plan_interfaces_nodes_playwright import NodeBackend

pytestmark = pytest.mark.e2e_playwright
OUT = Path("/tmp/decisions-edge-audit/planning-canvas")
SIZES = [(390, 844), (768, 1024), (1280, 900), (1440, 900)]
WIRE = '\n'.join([
    'screen "Home" id=home route=/',
    '  #| Customers start here, then review their orders.',
    '  heading "Home"',
    *[f'  link "Page {n}" route=/page-{n}' for n in range(1, 17)],
    *[f'screen "Page {n}" id=page-{n} route=/page-{n}\n  heading "Page {n}"\n  link "Back to Home" route=/' for n in range(1, 17)],
])


@pytest.mark.parametrize('width,height', SIZES)
def test_find_page_keeps_the_map_and_chat_draft(page, width, height):
    backend = NodeBackend(content=WIRE)
    workspace = backend.workspace
    backend.workspace = lambda: {**workspace(), 'sitemap': {'coverage': 'complete', 'edges': [], 'nodes': [
        {'id': 'archive', 'kind': 'page', 'label': 'Archive', 'route': '/archive', 'status': 'verified'},
    ]}}
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL, wait_until='domcontentloaded')
    page.get_by_role('button', name='Interfaces', exact=True).click()
    expect(page.locator('.iface-node')).to_have_count(18)
    page.get_by_label('Fit to view', exact=True).click()
    zoom = page.locator('.iface-canvas .erd-zoom').inner_text()
    draft = page.get_by_label('Message', exact=True)
    draft.fill('Review the current page flow.')
    finder = page.get_by_role('button', name='Find page', exact=True)
    dialog = page.get_by_role('dialog', name='Find page', exact=True)
    search = dialog.get_by_role('searchbox', name='Search pages', exact=True)
    finder.click(timeout=3000)
    expect(search).to_be_focused()
    expect(dialog.get_by_role('listitem')).to_have_count(18)
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'picker-open-{width}.png'))
    search.fill('missing route')
    expect(dialog.get_by_role('status')).to_have_text('No matching pages')
    page.screenshot(path=str(OUT / f'picker-empty-{width}.png'))
    search.press('Escape')
    expect(dialog).to_be_hidden()
    expect(finder).to_be_focused()
    finder.press('Enter')
    search.fill('  /PAGE-16  ')
    expect(dialog.get_by_role('listitem')).to_have_count(1)
    page.screenshot(path=str(OUT / f'picker-filtered-{width}.png'))
    search.press('ArrowDown')
    match = dialog.get_by_role('button', name='Page 16 /page-16', exact=True)
    expect(match).to_be_focused()
    match.press('Enter')
    expect(dialog).to_be_hidden()
    expect(page.locator('#plan-preview iframe')).to_have_attribute('title', 'Page 16')
    back = page.get_by_role('button', name='Back to all pages')
    expect(back).to_be_focused()
    expect(draft).to_have_value('Review the current page flow.')
    page.screenshot(path=str(OUT / f'picker-opened-page-{width}.png'))
    back.press('Enter')
    expect(finder).to_be_focused()
    expect(page.locator('.iface-canvas .erd-zoom')).to_have_text(zoom)
    expect(draft).to_have_value('Review the current page flow.')
    finder.click()
    dialog.get_by_role('button', name='Close page picker').click()
    expect(finder).to_be_focused()
    finder.click()
    page.mouse.click(4, 4)
    expect(finder).to_be_focused()
    finder.click()
    search.fill('arChiVe')
    expect(dialog.get_by_role('button', name='Archive /archive No outline yet')).to_be_visible()
    search.press('Enter')
    expect(dialog).to_be_hidden()
    details = page.get_by_label('Page details', exact=True)
    expect(details.get_by_role('heading', name='Archive')).to_be_visible()
    expect(details.get_by_role('button', name='Create page outline')).to_be_focused()
    page.screenshot(path=str(OUT / f'picker-route-details-{width}.png'))


@pytest.mark.parametrize("width,height", SIZES)
def test_focused_page_is_readable_panel_escape_and_page_continuity(page, width, height):
    backend = NodeBackend(content=WIRE)
    _install_api(page, [])
    page.route("**/api/**", backend.route)
    page.set_viewport_size({"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    OUT.mkdir(parents=True, exist_ok=True)
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.locator(".iface-node").first.wait_for()
    page.get_by_label("Fit to view", exact=True).click()
    zoom = page.locator(".iface-canvas .erd-zoom")
    fitted = zoom.inner_text()
    assert int(fitted.rstrip("%")) < 100
    page.screenshot(path=str(OUT / f"overview-{width}.png"))

    # Pointer selection keeps overview zoom, rather than unexpectedly jumping.
    node = page.locator(".iface-node").first
    node.click()
    assert zoom.inner_text() == fitted
    page.locator(".iface-panel [data-iface-open]").focus()
    page.keyboard.press("Escape")
    assert page.locator(".iface-panel").is_hidden()
    assert node.evaluate("el => el === document.activeElement")

    # A real Tab focus reveals the page at readable natural scale.
    page.get_by_label("Fit to view", exact=True).click()
    page.locator(".iface-viewport").focus()
    page.keyboard.press("Tab")
    assert node.evaluate("el => el === document.activeElement")
    assert zoom.inner_text() == "100%"
    geometry = node.evaluate("""el => {
      const card = el.getBoundingClientRect(), viewport = el.closest('.iface-viewport').getBoundingClientRect();
      const zoom = new DOMMatrix(getComputedStyle(el.closest('.erd-stage')).transform).a;
      return { left: card.left - viewport.left, right: viewport.right - card.right,
        top: card.top - viewport.top, bottom: viewport.bottom - card.bottom,
        text: parseFloat(getComputedStyle(el.querySelector('strong')).fontSize) * zoom };
    }""")
    assert min(geometry[key] for key in ("left", "right", "top", "bottom")) >= -1, geometry
    assert geometry["text"] >= 12, geometry
    assert node.evaluate("""el => {
      const card = el.getBoundingClientRect();
      return [...el.closest('.iface-canvas').querySelectorAll('.erd-dock')].every(dock => {
        const r = dock.getBoundingClientRect();
        return card.right <= r.left || card.left >= r.right || card.bottom <= r.top || card.top >= r.bottom;
      });
    }"""), "Focused card, including its route footer, must not sit beneath canvas controls"
    assert page.locator('.iface-lines').evaluate("""svg => {
      const boxes = [...svg.querySelectorAll('.iface-edge text')].map(text => text.getBBox());
      return boxes.every((a, i) => boxes.slice(i + 1).every(b =>
        a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y));
    }"""), "Dense reciprocal navigation labels must remain separate"

    page.screenshot(path=str(OUT / f"focused-page-{width}.png"))

    # Refocusing the same page must apply 100% even if it is already within
    # the canvas bounds and no pan would otherwise be necessary.
    page.get_by_label("Zoom out", exact=True).click()
    assert int(zoom.inner_text().rstrip("%")) < 100
    page.locator(".iface-viewport").focus()
    page.keyboard.press("Tab")
    assert node.evaluate("el => el === document.activeElement")
    assert zoom.inner_text() == "100%"
    assert node.evaluate("""el => {
      return new DOMMatrix(getComputedStyle(el.closest('.erd-stage')).transform).a === 1;
    }""")
    page.screenshot(path=str(OUT / f"refocused-page-{width}.png"))

    page.keyboard.press("Space")
    assert page.locator(".iface-panel").is_visible()
    assert page.locator(".iface-panel h4").evaluate("""heading => {
      const range = document.createRange(); range.selectNodeContents(heading);
      const bounds = range.getBoundingClientRect();
      const top = document.elementFromPoint(bounds.left + Math.min(12, bounds.width / 2), bounds.top + bounds.height / 2);
      return heading.closest('.iface-panel').contains(top);
    }"""), "The selected page heading must not be covered by screenshot or rebuild tools"
    assert page.locator(".iface-panel-close").evaluate("""button => {
      const bounds = button.getBoundingClientRect();
      return button.contains(document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
    }""")
    page.screenshot(path=str(OUT / f"page-details-{width}.png"))
    page.keyboard.press("Escape")
    assert page.locator(".iface-panel").is_hidden()

    # The keyboard action opens the same page and its perspective survives tab navigation.
    page.keyboard.press("Enter")
    page.get_by_role("button", name="Planning", exact=True).click()
    assert page.locator(".plan-page-hub h3").inner_text() == "Home"
    page.screenshot(path=str(OUT / f"page-planning-{width}.png"))
    page.get_by_role("button", name="Open Data", exact=True).click()
    page.get_by_role("button", name="Interfaces", exact=True).click()
    assert page.get_by_role("button", name="Planning", exact=True).get_attribute("aria-pressed") == "true"
    assert page.locator(".plan-page-hub h3").inner_text() == "Home"
    page.get_by_role("button", name="Development", exact=True).click()
    page.screenshot(path=str(OUT / f"page-development-{width}.png"))
    page.get_by_label("Back to all pages", exact=True).click()
    page.locator(".iface-node").first.wait_for()
    assert page.locator(".iface-canvas .erd-zoom").inner_text() == "100%"
    page.screenshot(path=str(OUT / f"map-return-{width}.png"))

    # Explicit user magnification is retained when a subsequent page gets focus.
    page.get_by_label("Zoom in", exact=True).click()
    magnified = page.locator(".iface-canvas .erd-zoom").inner_text()
    page.locator(".iface-viewport").focus()
    page.keyboard.press("Tab")
    assert page.locator(".iface-canvas .erd-zoom").inner_text() == magnified
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not backend.saves, "Navigation never mutates source documents"
    assert not errors, errors


@pytest.mark.parametrize("width,height", SIZES)
def test_gallery_chat_and_header_controls_remain_reachable(page, width, height):
    backend = NodeBackend(content=WIRE, assets=[{"id": 77, "name": "Screenshot.png", "mime_type": "image/png", "url": "/api/workflows/studio/plan-workspaces/5/attachments/77"}])
    _install_api(page, [])
    page.route("**/api/**", backend.route)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(PLAN_URL)
    page.get_by_role("button", name="Interfaces", exact=True).click()
    page.locator(".iface-node").first.wait_for()
    page.locator(".plan-asset").wait_for()
    assert page.locator("#plan-build").evaluate("""button => {
      const r = button.getBoundingClientRect(), host = button.closest('#development-task-header').getBoundingClientRect();
      return r.right <= host.right + 1 && button.scrollWidth <= button.clientWidth + 1 && button.contains(document.elementFromPoint(r.right - 4, r.top + r.height/2));
    }"""), "Build must fit and remain uncovered beside the view switch"
    assert page.locator(".iface-dock").evaluate("""dock => {
      const r = dock.getBoundingClientRect(), preview = dock.closest('.plan-preview').getBoundingClientRect();
      return r.bottom <= preview.bottom + 1 && r.top >= preview.top;
    }"""), "Attachment gallery must not clip the canvas zoom controls"
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f"gallery-map-{width}.png"))
    page.locator(".plan-composer-wrap").scroll_into_view_if_needed()
    assert page.locator("#plan-messages").evaluate("el => el.clientHeight >= 100")
    assert page.locator(".plan-conversation-empty").evaluate("""el => {
      const r = el.getBoundingClientRect(), log = el.closest('.plan-messages').getBoundingClientRect();
      return r.top >= log.top - 1 && r.bottom <= log.bottom + 1;
    }""")
    page.screenshot(path=str(OUT / f"gallery-chat-{width}.png"))
    documents = page.get_by_role("button", name="Documents", exact=True)
    documents.focus()
    page.keyboard.press("Enter")
    assert documents.evaluate("""el => {
      const r = el.getBoundingClientRect(), tabs = el.closest('.plan-tabs').getBoundingClientRect();
      return r.left >= tabs.left - 1 && r.right <= tabs.right + 1;
    }"""), "The selected Documents label must be fully visible"
    page.screenshot(path=str(OUT / f"documents-header-{width}.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not backend.saves


@pytest.mark.parametrize('width,height', SIZES)
@pytest.mark.parametrize('perspective', ['Planning', 'Development'])
def test_page_perspective_final_actions_scroll_above_edit_dock(page, width, height, perspective):
    backend = NodeBackend(content=WIRE)
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL)
    page.get_by_role('button', name='Interfaces', exact=True).click()
    node = page.locator('.iface-node').first
    page.locator('.iface-viewport').focus()
    page.keyboard.press('Tab')
    page.keyboard.press('Enter')
    page.get_by_role('button', name=perspective, exact=True).click()
    page.locator('#plan-preview').evaluate('n => {n.scrollTop=n.scrollHeight;}')
    last = page.locator('.plan-page-hub section').last
    assert last.evaluate('''n => {
      const b=n.getBoundingClientRect(), dock=document.querySelector('.plan-pinned-dock').getBoundingClientRect();
      return b.bottom <= dock.top - 4;
    }'''), 'The final page section must be fully readable above the pinned edit dock'
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'{perspective.lower()}-end-{width}.png'))
