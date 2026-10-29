"""Missing Development subview inventory, all APIs isolated and no save actions."""
import pytest
from playwright.sync_api import Page, expect

from tests.ui.test_development_responsive import BASE, capture, install_responsive_api
from tests.ui.test_development_harness_playwright import _install_api

pytestmark = pytest.mark.e2e_playwright
WIDTHS = [390, 768, 1280, 1440]
WIRE = '''screen Customers id=customers route=/customers
  heading Customers
  form "Customer details"
    input Name bind=Customer.name
    input Email type=email bind=Customer.email
    button Save primary
  link "Saved customers" route=/saved'''


@pytest.mark.parametrize('width', WIDTHS)
def test_board_settings_subviews(page: Page, width):
    install_responsive_api(page)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/development/boards/decisions/70/settings/')
    expect(page.locator('#board-link-dialog')).to_be_visible()
    expect(page.locator('#board-edit-name')).to_have_value('Decisions delivery')
    capture(page, 'board-settings', width)
    page.keyboard.press('Escape')
    if width == 390:
        page.locator('#sidebar-open').click()
    page.locator('#new-board-button').click()
    expect(page.locator('#board-link-title')).to_have_text('Create board')
    capture(page, 'board-create', width)
    page.keyboard.press('Escape')


@pytest.mark.parametrize('width', WIDTHS)
def test_planning_page_data_and_diagram_subviews(page: Page, width):
    workspace, _ = install_responsive_api(page)
    workspace['items'] += [
        {'id': 301, 'title': 'Customers page', 'item_type': 'wireframe', 'content_format': 'wire', 'content': WIRE},
        {'id': 302, 'title': 'Site map', 'item_type': 'flows', 'content_format': 'wire', 'content': WIRE + '\nscreen Saved id=saved route=/saved\n  heading "Customer saved"'},
        {'id': 303, 'title': 'Customer data', 'item_type': 'erd', 'content_format': 'mermaid', 'content': 'erDiagram\n CUSTOMER ||--o{ ORDER : places\n CUSTOMER {\n string name\n string email\n }\n ORDER {\n int id\n string status\n }'},
        {'id': 304, 'title': 'Application architecture', 'item_type': 'architecture', 'content_format': 'mermaid', 'content': 'flowchart LR\n Browser --> API\n API --> Database\n API --> Worker'},
    ]
    workspace['sitemap'] = {'coverage': 'verified', 'nodes': [
        {'id': 'customers', 'kind': 'page', 'label': 'Customers', 'route': '/customers', 'status': 'verified'},
        {'id': 'saved', 'kind': 'page', 'label': 'Saved', 'route': '/saved', 'status': 'verified'},
    ], 'edges': [{'from': 'customers', 'to': 'saved', 'kind': 'navigation'}], 'scaffolding': []}
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/development/boards/jira/jira-1/plan/')
    page.locator('[data-plan-tab="wireframes"]').click()
    expect(page.locator('#plan-preview iframe')).to_be_visible()
    capture(page, 'plan-wireframe', width)
    page.locator('.plan-preview-toolbar > button').click()
    expect(page.locator('.plan-wireframe-preview svg')).to_be_visible()
    capture(page, 'plan-stencil', width)
    for layer in ['planning', 'development']:
        page.locator(f'[data-layer="{layer}"]').click()
        expect(page.locator('.plan-page-hub')).to_be_visible()
        capture(page, f'plan-page-{layer}', width)
    page.locator('#plan-artifact-select').select_option('302')
    expect(page.locator('.plan-sitemap')).to_be_visible()
    assert page.locator('.plan-sitemap-node').evaluate_all('nodes => Math.min(...nodes.map(node => node.offsetLeft)) === 0')
    capture(page, 'plan-sitemap', width)
    page.locator('[data-plan-tab="erd"]').click()
    expect(page.locator('.plan-mermaid svg')).to_be_visible()
    capture(page, 'plan-erd', width)
    page.locator('#plan-artifact-select').select_option('304')
    expect(page.locator('.plan-mermaid svg')).to_be_visible()
    expect(page.locator('.plan-mermaid')).to_contain_text('Browser')
    capture(page, 'plan-architecture', width)


def install_thread_api(page):
    install_responsive_api(page)
    _install_api(page, [], direct_execution=True, terminal_session=False)
    page.route('**/api/workflows/studio/sidebar/pins', lambda route: route.fulfill(json={}))
    page.route('**/api/chats?*', lambda route: route.fulfill(json={'chats': [{'id': 17, 'title': 'Review implementation', 'project_id': 7, 'development': True, 'development_source_type': 'prompt'}]}))
    page.route('**/api/chats', lambda route: route.fulfill(json={'chats': [{'id': 17, 'title': 'Review implementation', 'project_id': 7, 'development': True, 'development_source_type': 'prompt'}]}))
    page.route('**/api/chats/17?*', lambda route: route.fulfill(json={
        'id': 17, 'title': 'Review implementation', 'project_id': 7, 'provider': 'OpenAI',
        'model_name': 'gpt-5.3-codex', 'backend': 'codex', 'route_mode': 'manual', 'execution_profile': 'code',
        'autonomy_level': 'full', 'development': True, 'development_source_type': 'prompt',
        'development_workflow_id': None, 'development_ticket_id': 42,
        'development_execution': {'id': 'direct-91', 'direct': True, 'status': 'completed', 'backend_id': 'codex',
            'changes': {'files': [{'path': 'index.html', 'status': 'M', 'additions': 7, 'deletions': 1}]}},
        'permission_profile': {'mode': 'standard'}, 'messages': [{'role': 'user', 'content': 'Review the implementation'}],
    }))
    page.route('**/api/workflows/studio/tasks/17/plans', lambda route: route.fulfill(json={'items': [
        {'id': 21, 'revision': 1, 'chat_id': 17, 'mode': 'plan', 'status': 'draft', 'instruction': 'Review the implementation', 'snapshot': {'steps': [{'id': 1, 'name': 'Review', 'status': 'completed'}]}},
        {'id': 22, 'revision': 2, 'chat_id': 17, 'mode': 'goal', 'status': 'executing', 'instruction': 'Verify the implementation', 'snapshot': {'steps': [{'id': 1, 'name': 'Verify', 'status': 'completed'}]}},
    ]}))
    page.route('**/api/workflows/orchestrator-setup', lambda route: route.fulfill(json={'enabled': True, 'routing': {
        level: {'backend': 'codex', 'model_provider': 'openai', 'model': 'gpt-5.3-codex'} for level in ['low', 'medium', 'high']
    }}))


def open_thread_context(page, item, width):
    if width == 390 and 'open' not in page.locator('#studio-sidebar').get_attribute('class').split():
        page.locator('#sidebar-open').click()
    page.locator('[data-chat-id="17"]').first.click(button='right')
    page.locator(f'#thread-context-{item}').click()


@pytest.mark.parametrize('width', WIDTHS)
def test_thread_configuration_and_inspector_subviews(page: Page, width):
    install_thread_api(page)
    page.set_default_timeout(5000)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/development/threads/17/')
    expect(page.locator('#task-prompt')).to_be_enabled()
    expect(page).to_have_url(BASE + '/development/threads/17/')
    page.locator('#model-button').click()
    expect(page.locator('#model-dialog')).to_be_visible()
    capture(page, 'thread-model', width)
    for pane in ['harness', 'model', 'effort', 'speed']:
        button = page.locator(f'[data-model-pane="{pane}"]')
        if not button.is_visible():
            continue  # Backend capability hides unsupported effort/speed rows.
        button.click()
        expect(page.locator('#model-submenu')).to_be_visible()
        capture(page, f'thread-model-{pane}', width)
        page.locator('#model-submenu-back').click()
    page.locator('#complexity-settings-button').click()
    expect(page.locator('.complexity-row')).to_have_count(3)
    capture(page, 'thread-auto-settings', width)
    page.locator('#complexity-dialog-close').click()
    # Attachments now use the OS file picker; the old source dialog has no reachable control.
    page.locator('#composer-plan-button').click()
    page.locator('[data-composer-action="skills"]').click()
    expect(page.locator('#skills-browser-dialog')).to_be_visible()
    capture(page, 'thread-skills', width)
    page.locator('[data-skills-browser-close]').first.click()
    page.locator('#composer-plan-button').click()
    page.locator('[data-composer-action="visual"]').click()
    expect(page.locator('#visual-plan-dialog')).to_be_visible()
    assert page.locator('#visual-plan-done').evaluate('node => node.getBoundingClientRect().bottom <= node.closest(\"dialog\").getBoundingClientRect().bottom')
    capture(page, 'thread-visual-plan', width)
    page.locator('[data-visual-plan-close]').first.click()
    page.locator('#thread-more-button').click()
    page.locator('#thread-menu-details').click()
    expect(page.locator('#task-inspector')).to_be_visible()
    for tab in ['run', 'plan', 'goal', 'artifacts', 'changes']:
        button = page.locator(f'.inspector-tabs [data-tab="{tab}"]')
        expect(button).to_be_visible()
        button.click()
        if tab == 'changes':
            expect(page.locator('[data-review-file]').first).to_be_visible()
            assert page.locator('#model-button').evaluate('node => node.getBoundingClientRect().left >= document.querySelector(\".permission-control\").getBoundingClientRect().right')
        if tab == 'plan':
            expect(page.locator('.plan-revision').first).to_contain_text('Version 1')
            assert page.locator('.plan-revision > header').first.evaluate('node => getComputedStyle(node).display === \"flex\"')
        capture(page, f'thread-inspector-{tab}', width)
        if tab == 'artifacts':
            page.locator('[data-artifact-index]').first.click()
            expect(page.locator('#artifact-dialog')).to_be_visible()
            capture(page, 'thread-artifact', width)
            page.locator('#artifact-dialog').get_by_role('button', name='Done', exact=True).click()
        if tab == 'changes':
            page.locator('[data-review-file]').first.click()
            expect(page.locator('.change-file-review')).to_be_visible()
            capture(page, 'thread-file-diff', width)
    page.locator('#inspector-close').click()
    page.locator('#thread-more-button').click()
    page.locator('#thread-menu-edit-time').click()
    expect(page.locator('#time-dialog')).to_be_visible()
    capture(page, 'thread-time', width)
    page.keyboard.press('Escape')
    for item in ['rename', 'edit']:
        open_thread_context(page, item, width)
        dialog = '#thread-rename-dialog' if item == 'rename' else '#thread-dialog'
        expect(page.locator(dialog)).to_be_visible()
        capture(page, f'thread-{item}', width)
        page.keyboard.press('Escape')
