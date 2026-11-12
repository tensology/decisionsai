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
def test_large_discovered_map_opens_at_readable_scale(page, width, height):
    backend = NodeBackend(content='flowchart LR\n  A[Devices] --> B[Device add]', fmt='mermaid', kind='flows')
    workspace = backend.workspace
    backend.workspace = lambda: {**workspace(), 'sitemap': {
        'coverage': 'incomplete',
        'nodes': [{'id': f'route-{n}', 'kind': 'page', 'label': f'Device route {n}',
                   'route': f'/account/device/{n}'} for n in range(158)],
        'edges': [],
    }}
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL)
    page.get_by_role('button', name='Interfaces', exact=True).click()
    page.get_by_label('Select artifact').select_option(label='Discovered pages')
    viewport = page.locator('.iface-viewport')
    cards = viewport.locator('.iface-node')
    expect(cards).to_have_count(158)
    zoom = page.locator('.iface-canvas .erd-zoom')
    expect(zoom).to_have_text('100%', timeout=1500)
    first = cards.first
    expect(first).to_be_in_viewport(ratio=1)
    expect(first.locator('.iface-shot-missing')).to_have_count(0)
    assert first.bounding_box()['width'] >= 190
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'large-map-readable-{width}.png'), animations='disabled')
    first.click()
    expect(page.get_by_label('Page details', exact=True).get_by_role('heading', name='Device route 0')).to_be_visible()
    page.get_by_role('button', name='Close page details').click()
    stage = viewport.locator('.erd-stage')
    initial = stage.evaluate('stage => stage.style.transform')
    viewport.evaluate("""canvas => {
        for (const [type, clientY] of [['pointerdown', 130], ['pointermove', 30], ['pointerup', 30]]) {
            canvas.dispatchEvent(new PointerEvent(type, { pointerId: 1, clientX: 50, clientY }));
        }
    }""")
    assert stage.evaluate('stage => stage.style.transform') != initial
    viewport.dispatch_event('wheel', {'deltaX': -100000, 'deltaY': -100000})
    expect(first).to_be_in_viewport(ratio=1)
    viewport.dispatch_event('wheel', {'deltaX': 100000, 'deltaY': 100000})
    expect(cards.last).to_be_in_viewport(ratio=1)
    page.get_by_label('Fit to view', exact=True).click()
    assert int(zoom.inner_text().rstrip('%')) < 100
    page.screenshot(path=str(OUT / f'large-map-overview-{width}.png'), animations='disabled')


@pytest.mark.parametrize('width,height', [(320, 800), *SIZES])
def test_plan_header_keeps_long_project_name_and_controls_visible(page, width, height):
    project = 'Snuza Device Registration and International Support Operations'
    backend = NodeBackend(content=WIRE)
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.route('**/api/projects', lambda route: route.fulfill(json=[
        {'id': 7, 'name': project, 'folder_location': '/tmp/decisions', 'kanban_board_id': 70},
    ]))
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL)
    header = page.locator('#development-task-header.plan-mode')
    expect(header.locator('#header-project')).to_have_text(project)
    sections = header.get_by_role('navigation', name='Plan sections')
    expect(sections.get_by_role('button')).to_have_count(3)
    geometry = header.evaluate("""async header => {
        await document.fonts.ready;
        const bounds = header.getBoundingClientRect();
        const project = header.querySelector('#header-project');
        const plan = header.querySelector('#header-task');
        const chrome = header.querySelector('#board-chrome-unit');
        const tabs = header.querySelector('.plan-tabs');
        const within = (r, box) => r.left >= box.left - 1 && r.right <= box.right + 1
            && r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
        const textFits = element => {
            const text = element.querySelector('.name-marquee__text:not([aria-hidden])') || element;
            const range = document.createRange();
            range.selectNodeContents(text);
            return range.getBoundingClientRect().width > 0 && [...range.getClientRects()].every(r =>
                within(r, element.getBoundingClientRect()) && within(r, bounds));
        };
        const usable = button => {
            const r = button.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && within(r, bounds)
                && button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
        };
        const tabsAndViews = [...chrome.querySelectorAll('.board-view-switch button'), ...tabs.querySelectorAll('button')];
        const projectActions = [...chrome.querySelectorAll('.board-chrome-play-group > button')]
            .filter(button => button.getClientRects().length);
        const overlaps = (a, b) => a.left < b.right - 1 && a.right > b.left + 1
            && a.top < b.bottom - 1 && a.bottom > b.top + 1;
        const crumbs = [project.getBoundingClientRect(), plan.getBoundingClientRect()];
        return {
            project: textFits(project), plan: textFits(plan), buttons: tabsAndViews.length === 7,
            clickable: [...tabsAndViews, ...projectActions]
                .filter(button => !usable(button) || crumbs.some(crumb => overlaps(crumb, button.getBoundingClientRect())))
                .map(button => button.title || button.textContent),
            content: bounds.bottom <= document.querySelector('.plan-artifacts').getBoundingClientRect().top + 1,
        };
    }""")
    assert geometry == {'project': True, 'plan': True, 'buttons': True, 'clickable': [], 'content': True}
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ('Data', 'Documents', 'Interfaces'):
        tab = sections.get_by_role('button', name=name, exact=True)
        tab.click()
        expect(tab).to_have_attribute('aria-pressed', 'true')
        header.screenshot(path=str(OUT / f'plan-header-{name.lower()}-{width}.png'), animations='disabled')
    page.screenshot(path=str(OUT / f'plan-header-long-project-{width}.png'), animations='disabled')


@pytest.mark.parametrize('width,height', SIZES)
@pytest.mark.parametrize('flow_only', [False, True])
def test_discovered_routes_are_browsable_before_an_outline_exists(page, width, height, flow_only):
    backend = NodeBackend(content='flowchart LR\n  A[Devices] --> B[Device add]', fmt='mermaid', kind='flows')
    if not flow_only:
        backend.items = [item for item in backend.items if item['item_type'] != 'flows']
    workspace = backend.workspace
    nodes = [
        {'id': 'devices', 'kind': 'page', 'label': 'Devices', 'route': '/devices'},
        {'id': 'device-add', 'kind': 'page', 'label': 'Device add', 'route': '/devices/add',
         'template': 'devices/device_add.html'},
    ]
    backend.workspace = lambda: {**workspace(), 'sitemap': {
        'coverage': 'verified', 'nodes': nodes,
        'edges': [{'from': 'devices', 'to': 'device-add', 'kind': 'navigation'}],
    }}
    _install_api(page, [])
    requests = []

    def route(request):
        requests.append((request.request.method, request.request.url))
        backend.route(request)

    page.route('**/api/**', route)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL)
    page.get_by_role('button', name='Interfaces', exact=True).click()
    if flow_only:
        picker = page.get_by_label('Select artifact')
        assert picker.evaluate('select => getComputedStyle(select).backgroundImage') != 'none'
        picker.select_option(label='Discovered pages')
    expect(page.locator('.iface-node')).to_have_count(2, timeout=4000)
    OUT.mkdir(parents=True, exist_ok=True)
    kind = 'flows' if flow_only else 'empty'
    page.screenshot(path=str(OUT / f'discovery-map-{kind}-{width}.png'))
    draft = page.get_by_label('Message', exact=True)
    draft.fill('Review device registration.')
    page.get_by_role('button', name='Find page', exact=True).click()
    search = page.get_by_role('searchbox', name='Search pages')
    search.fill('/devices')
    search.press('Enter')
    details = page.get_by_label('Page details', exact=True)
    details.get_by_role('button', name='Device add /devices/add').click()
    expect(details.get_by_role('heading', name='Device add')).to_be_focused()
    expect(details.get_by_text('devices/device_add.html', exact=True)).to_be_visible()
    if width >= 1280:
        assert page.locator('.iface-node.selected').evaluate("""node => {
            const card = node.getBoundingClientRect();
            const canvas = node.closest('.iface-canvas');
            return card.right <= canvas.querySelector('.iface-panel').getBoundingClientRect().left
                && card.left >= canvas.querySelector('.iface-viewport').getBoundingClientRect().left;
        }""")
    expect(page.get_by_role('toolbar', name='Edit pages')).to_have_count(0)
    expect(details.get_by_role('button', name='Create page outline')).to_have_count(0)
    page.screenshot(path=str(OUT / f'discovery-details-{kind}-{width}.png'))
    details.get_by_role('button', name='Back to results').click()
    expect(search).to_have_value('/devices')
    page.screenshot(path=str(OUT / f'discovery-results-{kind}-{width}.png'))
    search.press('Escape')
    if flow_only:
        page.get_by_label('Select artifact').select_option(label='Customer pages')
        expect(page.locator('.iface-node')).to_have_count(0)
        expect(page.locator('.plan-mermaid')).to_be_visible()
    expect(draft).to_have_value('Review device registration.')
    assert not any(method in {'PATCH', 'PUT', 'DELETE'} or (method == 'POST' and '/5/' in url)
                   for method, url in requests)


@pytest.mark.parametrize('width,height', SIZES)
def test_discovered_routes_replace_a_stale_no_routes_preview(page, width, height):
    placeholder = 'screen "No routes found" device=desktop\n  stack\n    heading "No route table detected"\n'
    backend = NodeBackend(content=placeholder)
    workspace = backend.workspace
    backend.workspace = lambda: {**workspace(), 'sitemap': {'coverage': 'incomplete', 'edges': [], 'nodes': [
        {'id': 'device-add', 'kind': 'page', 'label': 'Device add', 'route': '/account/device/add',
         'template': 'accounts/device_add.html', 'status': 'verified'},
        {'id': 'device-edit', 'kind': 'page', 'label': 'Device edit', 'route': '/account/device/edit/:id',
         'template': 'accounts/device_edit.html', 'status': 'verified'},
        {'id': 'device-confirm', 'kind': 'page', 'label': 'Confirm device', 'route': '/account/device/add/confirm',
         'template': 'accounts/device_confirm.html', 'status': 'verified'},
        {'id': 'device-address', 'kind': 'page', 'label': 'Address', 'route': '/account/device/address',
         'template': 'accounts/device_address.html', 'status': 'verified'},
        {'id': 'blog-month', 'kind': 'page', 'label': 'Blog month', 'route': '/blog/:year/:month'},
        {'id': 'blog-tag', 'kind': 'page', 'label': 'Blog tag', 'route': '/blog/tag/:slug'},
        {'id': 'catch-all', 'kind': 'page', 'label': 'Generic page', 'route': '/:slug'},
    ]}}
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL)
    page.get_by_role('button', name='Interfaces', exact=True).click()
    expect(page.locator('.iface-node')).to_have_count(7)
    expect(page.get_by_text('No routes found', exact=True)).to_have_count(0)
    page.get_by_role('button', name='Find page', exact=True).click()
    search = page.get_by_role('searchbox', name='Search pages')
    search.fill('device_add.html')
    expect(page.get_by_role('dialog').get_by_role('listitem')).to_have_count(1)
    results = page.get_by_role('dialog', name='Find page').get_by_role('listitem')
    search.fill('/account/')
    expect(results.last).to_contain_text('Generic page')
    search.fill('/blog/tag/unknown')
    expect(results).to_have_count(2)
    expect(results.first).to_contain_text('/blog/tag/:slug')
    search.fill('https://snuza.example/account/device/edit/42/?tab=details#serial')
    expect(results).to_have_count(1)
    expect(results.first).to_contain_text('/account/device/edit/:id')
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'picker-copied-url-{width}.png'), animations='disabled')
    search.fill('/account/device/add/?from=dashboard#form')
    expect(results).to_have_count(3)
    expect(results.first).to_contain_text('Device add')
    expect(results.nth(1)).to_contain_text('Confirm device')
    page.screenshot(path=str(OUT / f'picker-exact-route-{width}.png'))
    search.press('Enter')
    details = page.get_by_label('Page details', exact=True)
    expect(details.get_by_role('heading', name='Device add')).to_be_visible()
    expect(details.get_by_text('accounts/device_add.html', exact=True)).to_be_visible()
    expect(details.get_by_role('button', name='Create page outline')).to_be_focused()
    assert details.evaluate("""panel => {
      const toolbar = panel.closest('.iface-canvas').querySelector('.iface-navigation');
      return panel.getBoundingClientRect().top >= toolbar.getBoundingClientRect().bottom;
    }""")
    draft = page.get_by_role('textbox', name='Message', exact=True)
    draft.fill('Review the device registration flow.')
    details.get_by_role('button', name='Create page outline').click()
    page.get_by_role('button', name='Create outline', exact=True).click()
    expect(page.get_by_role('dialog')).to_be_hidden()
    expect(page.locator('.plan-page-hub').get_by_role('heading', name='Device add')).to_be_visible()
    expect(page.get_by_role('region', name='Page description')).to_be_visible()
    expect(page.get_by_role('group', name='Board view')).to_be_visible()
    back = page.get_by_role('button', name='Back to all pages')
    expect(back).to_be_focused()
    expect(back).to_be_in_viewport(ratio=1)
    expect(draft).to_have_value('Review the device registration flow.')
    page.screenshot(path=str(OUT / f'outline-created-{width}.png'), animations='disabled')
    page.get_by_role('button', name='Back to results', exact=True).click()
    expect(search).to_have_value('/account/device/add/?from=dashboard#form')
    expect(search).to_be_focused()
    page.screenshot(path=str(OUT / f'outline-created-results-{width}.png'), animations='disabled')
    search.press('Escape')
    expect(page.locator('.iface-node')).to_have_count(7)
    expect(details.get_by_role('heading', name='Device add')).to_be_visible()
    expect(details.get_by_role('button', name='Open page')).to_be_visible()
    expect(page.get_by_text('No routes found', exact=True)).to_have_count(0)
    page.screenshot(path=str(OUT / f'outline-created-map-{width}.png'), animations='disabled')
    assert len(backend.saves) == 1


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
    expect(page.get_by_role('button', name='Show all links', exact=True)).to_have_count(0)
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
    first_result = dialog.get_by_role('listitem').first.get_by_role('button')
    second_result = dialog.get_by_role('listitem').nth(1).get_by_role('button')
    search.press('Tab')
    expect(first_result).to_be_focused()
    first_result.press('ArrowDown')
    expect(second_result).to_be_focused()
    second_result.press('Shift+Tab')
    expect(search).to_be_focused()
    assert dialog.locator('li button[tabindex="0"]').count() == 1
    result_list = dialog.get_by_role('list', name='Matching pages')
    saved_scroll = result_list.evaluate('(list) => { list.scrollTop = 200; return list.scrollTop; }')
    assert saved_scroll > 0
    search.press('Escape')
    expect(finder).to_be_focused()
    finder.click()
    assert result_list.evaluate('(list) => list.scrollTop') == saved_scroll
    previous_result = dialog.get_by_role('listitem').nth(12).get_by_role('button')
    previous_result.focus()
    previous_result.press('Escape')
    finder.press('Enter')
    search.press('Tab')
    expect(previous_result).to_be_focused()
    previous_result.press('ArrowDown')
    next_result = dialog.get_by_role('listitem').nth(13).get_by_role('button')
    expect(next_result).to_be_focused()
    next_result.press('Shift+Tab')
    expect(search).to_be_focused()
    page.screenshot(path=str(OUT / f'picker-keyboard-return-{width}.png'))
    search.fill('page')
    assert result_list.evaluate('(list) => list.scrollTop') == 0
    search.fill('')
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'picker-open-{width}.png'))
    search.fill('missing route')
    expect(dialog.get_by_role('status')).to_have_text('No matching pages')
    page.screenshot(path=str(OUT / f'picker-empty-{width}.png'))
    search.press('Escape')
    expect(dialog).to_be_hidden()
    expect(finder).to_be_focused()
    finder.press('Enter')
    expect(search).to_have_value('missing route')
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
    expect(search).to_have_value('  /PAGE-16  ')
    expect(match).to_be_visible()
    page.screenshot(path=str(OUT / f'picker-returned-{width}.png'))
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
    results = details.get_by_role('button', name='Back to results')
    results.click()
    expect(search).to_be_focused()
    expect(search).to_have_value('arChiVe')
    search.press('Escape')
    expect(results).to_be_focused()
    results.click()
    search.fill('/page-16')
    search.press('Enter')
    page.get_by_role('button', name='Back to results').click()
    expect(search).to_be_focused()
    expect(search).to_have_value('/page-16')
    expect(draft).to_have_value('Review the current page flow.')


@pytest.mark.parametrize('width,height', SIZES)
def test_page_details_follow_discovered_links_without_losing_chat(page, width, height):
    backend = NodeBackend(content=WIRE)
    workspace = backend.workspace
    backend.workspace = lambda: {**workspace(), 'sitemap': {'coverage': 'incomplete', 'nodes': [
        {'id': 'devices', 'kind': 'page', 'label': 'Devices', 'route': '/devices'},
        {'id': 'device-add', 'kind': 'page', 'label': 'Device add', 'route': '/devices/add'},
        {'id': 'registration', 'kind': 'page', 'label': 'Registration', 'route': '/registration'},
    ], 'edges': [
        {'from': 'devices', 'to': 'device-add', 'kind': 'navigation', 'label': 'Add a device'},
        {'from': 'device-add', 'to': 'devices', 'kind': 'navigation', 'label': 'Back'},
        {'from': 'registration', 'to': 'device-add', 'kind': 'navigation', 'label': 'Set up device'},
        {'from': 'registration', 'to': 'devices', 'kind': 'navigation', 'label': 'Manage devices'},
    ]}}
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL)
    page.get_by_role('button', name='Interfaces', exact=True).click()
    draft = page.get_by_label('Message', exact=True)
    draft.fill('Review device setup.')
    finder = page.get_by_role('button', name='Find page', exact=True)
    finder.click()
    search = page.get_by_role('searchbox', name='Search pages')
    search.fill('/devices/add')
    search.press('Enter')
    details = page.get_by_label('Page details', exact=True)
    reciprocal = details.get_by_role('group', name='Links both ways')
    linked_from = details.get_by_role('group', name='Linked from')
    expect(linked_from.get_by_role('button', name='Registration /registration')).to_be_visible(timeout=2000)
    follow = reciprocal.get_by_role('button', name='Devices /devices')
    expect(details.get_by_role('button', name='Devices /devices')).to_have_count(1)
    map_lines = page.locator('.iface-lines')
    expect(page.locator('.iface-lines.has-selected-page')).to_have_count(1)
    expect(map_lines.locator('.iface-edge.related')).to_have_count(3)
    unrelated = map_lines.locator('.iface-edge:not(.related)').filter(has_text='Manage devices')
    expect(unrelated).to_have_css('opacity', '0.13')
    expect(unrelated.locator('text')).to_have_css('visibility', 'hidden')
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'page-links-{width}.png'))
    follow.press('Enter')
    expect(details.get_by_role('heading', name='Devices', exact=True)).to_be_focused()
    expect(reciprocal.get_by_role('button', name='Device add /devices/add')).to_be_visible()
    expect(draft).to_have_value('Review device setup.')
    page.screenshot(path=str(OUT / f'followed-page-link-{width}.png'))
    details.get_by_role('button', name='Close page details').click()
    expect(page.locator('.iface-lines.has-selected-page')).to_have_count(0)
    expect(unrelated).to_have_css('opacity', '1')
    finder.click()
    expect(search).to_have_value('/devices/add')


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
    perspective = page.get_by_role("tablist", name="Page perspective")
    perspective.get_by_role("button", name="Planning", exact=True).click()
    assert page.locator(".plan-page-hub h3").inner_text() == "Home"
    page.screenshot(path=str(OUT / f"page-planning-{width}.png"))
    page.get_by_role("button", name="Open Data", exact=True).click()
    page.get_by_role("button", name="Interfaces", exact=True).click()
    assert perspective.get_by_role("button", name="Planning", exact=True).get_attribute("aria-pressed") == "true"
    assert page.locator(".plan-page-hub h3").inner_text() == "Home"
    perspective.get_by_role("button", name="Development", exact=True).click()
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
    assert page.get_by_role("navigation", name="Plan sections").evaluate("""nav => {
      const tabs = nav.getBoundingClientRect(), artifacts = document.querySelector('.plan-artifacts').getBoundingClientRect();
      return tabs.bottom <= artifacts.top + 1 && [...nav.querySelectorAll('button')].every(button => {
        const r = button.getBoundingClientRect();
        return r.left >= tabs.left - 1 && r.right <= tabs.right + 1
          && button.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2));
      });
    }"""), "Plan tabs must fit above the artifact pane"
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
    page.get_by_role('tablist', name='Page perspective').get_by_role('button', name=perspective, exact=True).click()
    page.locator('#plan-preview').evaluate('n => {n.scrollTop=n.scrollHeight;}')
    last = page.locator('.plan-page-hub section').last
    assert last.evaluate('''n => {
      const b=n.getBoundingClientRect(), dock=document.querySelector('.plan-pinned-dock').getBoundingClientRect();
      return b.bottom <= dock.top - 4;
    }'''), 'The final page section must be fully readable above the pinned edit dock'
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUT / f'{perspective.lower()}-end-{width}.png'))


@pytest.mark.parametrize('width,height', SIZES)
def test_large_interface_map_shows_only_selected_links_until_requested(page, width, height):
    backend = NodeBackend(content=WIRE)
    workspace = backend.workspace
    nodes = [
        {'id': f'device-{n}', 'kind': 'page', 'label': f'Device {n}', 'route': f'/devices/{n}'}
        for n in range(1, 47)
    ]
    edges = [
        {'from': f'device-{n}', 'to': f'device-{n + 1}', 'kind': 'navigation'}
        for n in range(1, 46)
    ]
    backend.workspace = lambda: {**workspace(), 'sitemap': {'nodes': nodes, 'edges': edges}}
    _install_api(page, [])
    page.route('**/api/**', backend.route)
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(PLAN_URL, wait_until='domcontentloaded')
    interfaces = page.get_by_role('button', name='Interfaces', exact=True)
    interfaces.click()
    toggle = page.get_by_role('button', name='Show all links', exact=True)
    expect(toggle).to_have_attribute('aria-pressed', 'false')
    all_edges = page.locator('.iface-edge')
    expect(all_edges.first).to_have_css('visibility', 'hidden')

    canvas = page.locator('.iface-viewport')
    cards = canvas.locator('.iface-node')
    first, second = cards.nth(0), cards.nth(1)
    canvas.focus()
    page.keyboard.press('Tab')
    expect(first).to_be_focused()
    page.keyboard.press('ArrowRight')
    expect(second).to_be_focused()
    expect(canvas.locator('.iface-node[tabindex="0"]')).to_have_count(1)
    page.keyboard.press('Tab')
    expect(page.get_by_role('button', name='Add page', exact=True)).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(second).to_be_focused()
    page.keyboard.press('End')
    expect(cards.last).to_be_focused()
    page.keyboard.press('Home')
    expect(first).to_be_focused()
    OUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=OUT / f'dense-keyboard-page-{width}.png', animations='disabled')
    page.get_by_role('button', name='Fit to view').click()
    zoom = page.locator('.iface-canvas .erd-zoom').inner_text()
    assert int(zoom.rstrip('%')) < 100

    page.screenshot(path=OUT / f'dense-overview-{width}.png', animations='disabled')
    toggle.press('Space')
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    expect(all_edges.first).to_have_css('visibility', 'visible')
    expect(page.locator('.iface-canvas .erd-zoom')).to_have_text(zoom)
    page.screenshot(path=OUT / f'dense-all-links-{width}.png', animations='disabled')
    page.get_by_role('button', name='Documents', exact=True).click()
    interfaces.click()
    expect(toggle).to_have_attribute('aria-pressed', 'true')
    toggle.click()
    page.get_by_role('button', name='Find page', exact=True).click()
    search = page.get_by_role('searchbox', name='Search pages')
    search.fill('/devices/25')
    search.press('Enter')
    details = page.get_by_label('Page details', exact=True)
    expect(details.get_by_role('heading', name='Device 25')).to_be_visible()
    expect(details.get_by_role('button', name='Device 26 /devices/26')).to_be_visible()
    expect(page.locator('.iface-edge.related')).to_have_count(2)
    expect(page.locator('.iface-edge.related').first).to_have_css('visibility', 'visible')
    expect(page.locator('.iface-edge:not(.related)').first).to_have_css('visibility', 'hidden')
    page.screenshot(path=OUT / f'dense-selected-{width}.png', animations='disabled')
    details.get_by_role('button', name='Close page details').click()
    expect(page.locator('.iface-edge.related')).to_have_count(0)
    expect(all_edges.first).to_have_css('visibility', 'hidden')
