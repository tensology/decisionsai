"""Primary activity takes precedence over unread state and recovers from durable data."""
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_primary_badge_states_and_recovery(width, tmp_path):
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>{% block content %}{% endblock %}</body></html>'}),
        FileSystemLoader(str(ROOT / 'distr/gui/web/templates')),
    ]), autoescape=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 900})
        page.set_content(env.get_template('chat/chat.html').render(base_path='/chat'))
        page.add_style_tag(content='body{margin:0;background:#101735;color:#ececf1;font-family:Arial}.hidden{display:none}.fixed{position:fixed}')
        page.add_style_tag(path=str(ROOT / 'distr/gui/web/static/chat/css/chat.css'))
        page.add_script_tag(path=str(ROOT / 'distr/gui/web/static/chat/js/chat.js'))
        page.evaluate('''() => {
            sidebarSkins=[];
            primaryAgentData={main_conversation_id:1,name:'Primary',skin_name:'oracle'};
            currentChatId=2;
            primaryAgentUnread=false;
            primaryAgentWorking=false;
            renderPrimaryAgent();
        }''')
        badge = page.locator('.primary-agent-badge')
        assert badge.get_attribute('data-state') == 'idle'
        assert badge.inner_text() == '★'
        assert badge.evaluate('e=>getComputedStyle(e).bottom') == '4px'
        assert badge.evaluate('e=>getComputedStyle(e).right') == '4px'
        page.screenshot(path=str(tmp_path / f'idle-{width}.png'))
        # A different agent cannot affect the primary badge.
        page.evaluate('handleChatEventStreamStarted({chat_id:3})')
        assert badge.get_attribute('data-state') == 'idle'
        page.evaluate('handleChatEventStreamStarted({chat_id:1})')
        assert badge.get_attribute('data-state') == 'working'
        assert badge.evaluate('e=>getComputedStyle(e,"::after").animationName') == 'primary-agent-spin'
        assert badge.get_attribute('aria-label') == 'Primary agent is working'
        page.screenshot(path=str(tmp_path / f'working-{width}.png'))
        page.evaluate('primaryAgentUnread=true; renderPrimaryAgentBadge()')
        assert badge.get_attribute('data-state') == 'working'
        page.evaluate('handleChatEventStreamFinished({chat_id:1})')
        assert badge.get_attribute('data-state') == 'unread'
        assert badge.evaluate('e=>getComputedStyle(e).backgroundColor') == 'rgb(59, 130, 246)'
        assert badge.inner_text() == ''
        page.screenshot(path=str(tmp_path / f'unread-{width}.png'))
        # Read acknowledgement restores the star. Failed acknowledgement retains dot.
        page.evaluate('''async () => {
            currentChatId=1;
            document.hasFocus=()=>true;
            window.fetch=async()=>({ok:false});
            await acknowledgeChatRead(1,7);
        }''')
        assert badge.get_attribute('data-state') == 'unread'
        page.evaluate('''async () => {
            window.fetch=async()=>({ok:true,json:async()=>({unread:false})});
            await acknowledgeChatRead(1,7);
        }''')
        assert badge.get_attribute('data-state') == 'idle'
        # Durable activity restores spinner after navigation/reload, then clears it.
        page.evaluate('''async () => {
            window.fetch=async()=>({ok:true,json:async()=>({turns:[{active:true,done:false}]})});
            await refreshPrimaryAgentActivity();
        }''')
        assert badge.get_attribute('data-state') == 'working'
        page.evaluate('handleChatEventStreamError({chat_id:1})')
        assert badge.get_attribute('data-state') == 'idle'
        # A stale poll must not override a newer live start.
        page.evaluate('''() => {
            window.fetch=()=>new Promise(resolve=>window.finishPoll=resolve);
            window.poll=refreshPrimaryAgentActivity();
            updatePrimaryAgentActivity(1,true);
            finishPoll({ok:true,json:async()=>({turns:[]})});
        }''')
        page.evaluate('poll')
        assert badge.get_attribute('data-state') == 'working'
        page.evaluate('''async () => {
            window.fetch=async()=>({ok:true,json:async()=>({turns:[]})});
            await refreshPrimaryAgentActivity();
        }''')
        assert badge.get_attribute('data-state') == 'idle'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        browser.close()
