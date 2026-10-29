"""Read-only responsive journeys for the secondary local web surfaces."""
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

OUTPUT = Path('/tmp/decisions-secondary-responsive')
BASE = 'http://127.0.0.1:8765'


@pytest.fixture
def secondary_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        skill = {'id': 'responsive-review', 'name': 'Review responsive interfaces',
                 'description': 'Check readable tables, controls, navigation, and layouts.',
                 'tags': ['design'], 'editable': True, 'content': '# Review\n\nCheck every screen and viewport.'}

        def api(route):
            path = urlparse(route.request.url).path
            if path == '/docs/api/endpoints':
                route.continue_()
                return
            if path == '/api/skills':
                data = [skill]
            elif path.startswith('/api/skills/'):
                data = skill
            elif path == '/api/projects':
                data = [{'id': 1, 'name': 'Responsive review', 'path': '/tmp/review'}]
            elif path == '/api/diagrams/history':
                data = {'diagrams': []}
            elif path == '/api/diagrams/1':
                data = {'id': 1, 'title': 'A readable responsive diagram', 'code': 'flowchart TD\n A[Open page] --> B[Review layout]\n B --> C[Confirm readability]'}
            elif path == '/api/workflows':
                data = [{'id': 1, 'name': 'Review the interface across all screen sizes'}]
            elif path == '/api/workflows/1':
                data = {'id': 1, 'name': 'Review the interface across all screen sizes',
                        'steps': [{'id': 1, 'name': 'Check readability and alignment', 'step_type': 'instruction',
                                   'instruction': 'Review the current screen.'}], 'runs': []}
            else:
                data = {}
            route.fulfill(json=data)

        page.context.route('**/api/**', api)
        yield page
        browser.close()


def capture(page, name, width):
    OUTPUT.mkdir(exist_ok=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
    page.screenshot(path=str(OUTPUT / f'{width}-{name}.png'), animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_secondary_pages(secondary_page, width):
    page = secondary_page
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(BASE + '/skills/')
    expect(page.locator('.skill-card')).to_be_visible()
    capture(page, 'skills', width)
    page.locator('.skill-card h4').click()
    expect(page.locator('#skill-modal-content')).to_contain_text('Check every screen')
    capture(page, 'skill-detail', width)
    page.locator('#skill-modal-close').click()
    page.locator('#skills-create-btn').click()
    expect(page.locator('#skill-editor-modal')).to_be_visible()
    capture(page, 'skill-create', width)
    page.keyboard.press('Escape')
    expect(page.locator('#skill-editor-modal')).to_be_hidden()

    page.goto(BASE + '/docs/')
    expect(page.locator('.endpoint-header').first).to_be_visible()
    capture(page, 'docs', width)
    assert page.locator('.endpoint-header').evaluate_all('''headers => headers.every(header => {
        const badge = header.querySelector('.method-badge').getBoundingClientRect();
        const path = header.querySelector('.endpoint-path').getBoundingClientRect();
        return path.left >= badge.right || path.top >= badge.bottom;
    })''')
    if width < 760:
        assert page.locator('.endpoint-header').evaluate_all('''headers => headers.every(header => {
            const path = header.querySelector('.endpoint-path').getBoundingClientRect();
            const summary = header.querySelector('.endpoint-summary').getBoundingClientRect();
            return summary.top >= path.bottom;
        })''')
    for section in page.locator('.docs-section').all():
        section.scroll_into_view_if_needed()
        capture(page, 'docs-' + section.get_attribute('id'), width)
    page.evaluate('scrollTo(0,0)')
    page.locator('.endpoint-header').first.click()
    expect(page.locator('.endpoint-body').first).to_be_visible()
    capture(page, 'docs-schema', width)
    page.locator('.try-btn').first.click()
    expect(page.locator('#try-modal')).to_be_visible()
    capture(page, 'docs-request-preview', width)
    page.keyboard.press('Shift+Tab')
    expect(page.locator('#try-modal-send')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.locator('#try-modal-close')).to_be_focused()
    page.keyboard.press('Escape')
    expect(page.locator('#try-modal')).to_be_hidden()
    expect(page.locator('.try-btn').first).to_be_focused()
    page.locator('.try-btn').first.click()
    page.locator('#try-modal').click(position={'x': 2, 'y': 2})
    expect(page.locator('#try-modal')).to_be_hidden()
    schema_card = page.locator('.endpoint-card').filter(has=page.locator('.schema-table')).first
    schema_card.locator('.endpoint-header').focus()
    schema_card.locator('.endpoint-header').press('Enter')
    expect(schema_card.locator('.endpoint-header')).to_have_attribute('aria-expanded', 'true')
    schema_card.locator('.schema-table').scroll_into_view_if_needed()
    capture(page, 'docs-table', width)
    assert schema_card.locator('th[scope="col"]').count() >= 4

    page.goto(BASE + '/diagram/?id=1')
    expect(page.locator('.mermaid-viewer-canvas-wrap svg')).to_be_visible(timeout=15000)
    capture(page, 'diagram', width)
    page.locator('.mermaid-viewer-code').fill('invalid diagram syntax')
    page.locator('[data-action="render"]').click()
    expect(page.locator('.mermaid-viewer-status-error')).to_be_visible()
    expect(page.locator('body > div:not(#mermaid-viewer-root)')).to_have_count(0)
    capture(page, 'diagram-error', width)

    page.goto(BASE + '/workflows/?mode=blueprint')
    expect(page.locator('#wf-board-pane-toggle-header')).to_be_visible()
    capture(page, 'blueprint-board', width)
    page.locator('#wf-board-pane-toggle-header').click()
    expect(page.locator('#wf-split-right')).to_be_visible()
    expect(page.locator('#wf-detail')).to_be_visible()
    capture(page, 'blueprint-workflow', width)
    for tab in ['tickets', 'cli']:
        page.locator(f'.wf-tab[data-tab="{tab}"]').click()
        expect(page.locator(f'#wf-tab-{tab}')).to_be_visible()
        if tab == 'cli':
            expect(page.locator('#wf-cli-input')).to_be_visible()
            assert page.locator('#wf-cli-input').bounding_box()['width'] >= 180
            if width <= 1100:
                assert page.locator('.wf-cli-terminal-wrap').bounding_box()['height'] >= 160
        capture(page, 'blueprint-' + tab, width)
    page.locator('#wf-menu-btn').click()
    expect(page.locator('#sr-llm-modal')).to_be_visible()
    for tab in ['run-policy', 'context-rules', 'execution']:
        page.locator(f'[data-wf-config-tab="{tab}"]').click()
        expect(page.locator(f'#wf-config-tab-{tab}')).to_be_visible()
        capture(page, 'blueprint-config-' + tab, width)
        if tab == 'execution' and width < 1024:
            routing = page.locator('#wf-global-exec-routes')
            assert routing.evaluate('(node) => node.scrollWidth > node.clientWidth')
            routing.evaluate('(node) => node.scrollTo({left: node.scrollWidth, behavior: "instant"})')
            capture(page, 'blueprint-config-execution-end', width)
        if tab == 'execution':
            page.locator('#wf-global-exec-routes').evaluate('(node) => node.scrollTo({left: 0, behavior: "instant"})')
            page.locator('#wf-config-tab-execution').evaluate('(node) => node.scrollTo({top: node.scrollHeight, behavior: "instant"})')
            capture(page, 'blueprint-config-execution-bottom', width)
    page.locator('#sr-llm-close').click()
    page.locator('#wf-new-workflow-btn').click()
    expect(page.locator('#wf-create-modal')).to_be_visible()
    capture(page, 'blueprint-create', width)
    page.locator('#wf-create-cancel').click()
    page.locator('#wf-board-pane-reopen').click()
    expect(page.locator('#wf-split-left')).to_be_visible()

    page.goto(BASE + '/board/')
    capture(page, 'board-empty', width)
    page.goto(BASE + '/chat/')
    expect(page.locator('.message-input')).to_be_visible()
    expect(page.locator('#emptyStateForm')).to_be_visible()
    capture(page, 'chat', width)
    assert page.locator('#inputContainer').evaluate('(node) => node.getBoundingClientRect().bottom <= innerHeight')
    if width < 760:
        page.locator('#emptyState').evaluate('(node) => node.scrollTo({top: node.scrollHeight, behavior: "instant"})')
        capture(page, 'chat-setup-bottom', width)
    chat = {'id': 1, 'title': 'Review the responsive interface with a long conversation title',
            'provider': 'openai', 'model_name': 'gpt-test', 'voice_provider': 'openai', 'voice_model': 'alloy',
            'messages': [{'role': 'user', 'content': 'Please review the mobile layouts.', 'timestamp': 1793200000},
                         {'role': 'assistant', 'content': 'The tables and editors remain readable across screen sizes.', 'timestamp': 1793200001}]}
    page.route('**/api/chats', lambda route: route.fulfill(json={'chats': [chat], 'last_chat_id': 1, 'agent_current_chat_id': 1}))
    page.route('**/api/chats/1', lambda route: route.fulfill(json=chat))
    page.reload()
    expect(page.locator('#chatMessages')).to_be_visible()
    expect(page.locator('#chatMessages')).to_contain_text('The tables and editors')
    capture(page, 'chat-conversation', width)
    if width < 1024:
        about = page.locator('#llamaClickTarget').bounding_box()
        footer = page.locator('.input-footer').bounding_box()
        assert about['y'] >= footer['y']
        assert page.locator('#chatTitle').evaluate('(node) => getComputedStyle(node).textOverflow') == 'ellipsis'
    page.locator('#configureChatButton').click()
    expect(page.locator('#chatConfigModal')).to_be_visible()
    capture(page, 'chat-configure', width)
    page.locator('#chatConfigClose').click()
    page.evaluate("content => renderMessages([{role: 'assistant', content, timestamp: Date.now()}])", '```mermaid\nflowchart TD\n A[Review] --> B[Verify]\n```')
    expect(page.locator('[data-mermaid-open]')).to_be_visible()
    expect(page.locator('.mermaid-inline-preview')).to_contain_text('A[Review] --> B[Verify]')
    assert page.locator('.mermaid-inline-block').evaluate('(node) => node.getBoundingClientRect().right <= node.closest(".message-content").getBoundingClientRect().right')
    capture(page, 'chat-diagram', width)
    if width < 760:
        assert page.locator('[data-mermaid-open]').bounding_box()['height'] >= 44
    with page.expect_popup() as opened:
        page.locator('[data-mermaid-open]').click()
    popup = opened.value
    try:
        expect(popup.locator('.mermaid-viewer-canvas svg')).to_be_visible(timeout=5000)
    except AssertionError:
        popup.screenshot(path=str(OUTPUT / f'{width}-diagram-popup-failure.png'))
        print(popup.locator('body').inner_text()[:1200])
        raise
    popup.close()
