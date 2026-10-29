"""Header and right-click actions share one accessible, bounded menu."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("width", [390, 768, 1280, 1440])
def test_chat_actions_menu(width, tmp_path):
    template = (ROOT / "distr/gui/web/templates/chat/chat.html").read_text()
    header_start = template.index('        <div class="chat-settings-header"')
    header = template[header_start:template.index('        <!-- Empty State:', header_start)]
    menu_start = template.index('<div id="chatContextMenu"')
    menu = template[menu_start:template.index('\n</div>', menu_start) + 7]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 700})
        page.set_content(f'<base href="http://decisions.test/"><style>body{{margin:0;font-family:system-ui}}</style><div class="chat-shell">{header}</div>{menu}')
        page.route('http://decisions.test/api/skins/hayley/preview/idle.webp', lambda route: route.fulfill(path=str(ROOT / 'assets/avatars/hayley/idle.webp'), content_type='image/webp'))
        page.add_style_tag(path=str(ROOT / "distr/gui/web/static/chat/css/chat.css"))
        page.evaluate("""() => {
            for (const id of ['newChatBtn', 'sendButton', 'modalClose', 'modalCancel', 'modalCreate', 'llmProvider', 'messageInput', 'newChatModal']) {
                const el = document.createElement('input'); el.id = id; document.body.append(el);
            }
            document.getElementById('newChatModal').style.display = 'none';
            document.getElementById('chatSettingsHeader').style.removeProperty('display');
        }""")
        page.add_script_tag(path=str(ROOT / "distr/gui/web/static/chat/js/chat.js"))
        page.evaluate("""() => {
            currentChatId = 7;
            setupEventListeners();
            window.menuCalls = [];
            selectChat = async id => { currentChatId = id; };
            compactCurrentChat = id => window.menuCalls.push(['compact', id]);
            forkCurrentChat = id => window.menuCalls.push(['fork', id]);
            sidebarSkins = [{folder_name:'hayley', preview_image:'idle.webp'}];
            const row = createChatItem({id: 9, title: 'Conversation topic', voice_name: 'Hayley Williams', latest_message: 'Working on the sidebar', unread: true, skin_name:'hayley'});
            row.style.width = '280px';
            row.style.color = '#fff';
            document.querySelector('.chat-shell').append(row);
            currentChatId = 9;
            updateActiveChat();
        }""")
        row = page.locator('.chat-item[data-chat-id="9"]')
        assert row.locator('.chat-item-title').inner_text() == 'Hayley Williams'
        assert row.locator('.chat-item-preview').inner_text() == 'Working on the sidebar'
        assert row.locator('.chat-unread-dot').is_visible()
        page.wait_for_function("document.querySelector('.chat-thread-avatar img').naturalWidth > 0")
        row.screenshot(path=str(tmp_path / f'unread-row-{width}.png'))
        assert row.locator('button, .chat-item-in-agent-star, .chat-item-agent-badge').count() == 0
        assert row.get_attribute('aria-selected') == 'true'
        assert row.evaluate("el => getComputedStyle(el).borderLeftWidth === '0px'")
        assert row.locator('.chat-item-title').evaluate("el => getComputedStyle(el).color") == 'rgb(255, 255, 255)'
        page.evaluate('currentChatId = 7')
        trigger = page.get_by_role('button', name='Chat actions', exact=True)
        trigger.click()
        assert trigger.get_attribute('aria-expanded') == 'true'
        assert page.get_by_role('menu').is_visible()
        page.evaluate("document.dispatchEvent(new Event('scroll'))")
        assert page.get_by_role('menu').is_visible()
        assert page.get_by_role('separator').count() == 3
        assert page.locator('#compactChatButton, #forkChatButton').count() == 0
        assert page.get_by_role('menu').evaluate("el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; }")
        page.screenshot(path=str(tmp_path / f'header-menu-{width}.png'))
        page.get_by_role('menuitem', name='Compact', exact=True).click()
        page.wait_for_function("window.menuCalls.length === 1")
        assert page.evaluate('window.menuCalls') == [['compact', 7]]
        assert not page.get_by_role('menu').is_visible()
        trigger.press('Enter')
        assert page.get_by_role('menuitem', name='View', exact=True).evaluate('el => el === document.activeElement')
        page.keyboard.press('ArrowUp')
        assert page.get_by_role('menuitem', name='Delete', exact=True).evaluate('el => el === document.activeElement')
        page.keyboard.press('Escape')
        assert trigger.evaluate('el => el === document.activeElement')
        assert not page.get_by_role('menu').is_visible()
        row.click(button='right')
        assert trigger.get_attribute('aria-expanded') == 'false'
        assert page.get_by_role('menu').evaluate("el => { const r = el.getBoundingClientRect(); return r.right <= innerWidth && r.bottom <= innerHeight; }")
        page.screenshot(path=str(tmp_path / f'context-menu-{width}.png'))
        page.get_by_role('menuitem', name='Fork', exact=True).click()
        page.wait_for_function("window.menuCalls.length === 2")
        assert page.evaluate('window.menuCalls')[-1] == ['fork', 9]
        page.evaluate("""async () => {
            currentChatId = 9;
            const original = window.fetch;
            window.fetch = async () => new Response(JSON.stringify({unread:false}));
            await acknowledgeChatRead(9, 90);
            window.fetch = original;
            applyHeaderStatsPayload({title:'Updated intention'});
        }""")
        assert not row.locator('.chat-unread-dot').is_visible()
        assert row.locator('.chat-item-title').inner_text() == 'Hayley Williams'
        assert row.get_attribute('data-chat-title') == 'Updated intention'
        row.screenshot(path=str(tmp_path / f'read-row-{width}.png'))
        browser.close()
