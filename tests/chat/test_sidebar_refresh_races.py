"""Late list responses and a failed primary lookup must preserve the roster."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sidebar_page():
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '<html><body>{% block content %}{% endblock %}</body></html>'}),
        FileSystemLoader(str(ROOT / 'distr/gui/web/templates')),
    ]), autoescape=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(env.get_template('chat/chat.html').render(base_path='/chat'))
        page.add_script_tag(path=str(ROOT / 'distr/gui/web/static/chat/js/chat.js'))
        page.evaluate('''() => {
            sidebarSkins = [];
            window.rendered = [];
            renderChatList = rows => { rendered = rows; };
            renderPrimaryAgent = () => {};
            applyChatsData = () => {};
            window.pendingLists = [];
            window.fetch = async url => {
                if (url === '/api/chats') return await new Promise(resolve => pendingLists.push(resolve));
                return {ok:true,json:async()=>({primary_agent:null})};
            };
        }''')
        yield page
        browser.close()


def test_late_sidebar_response_cannot_replace_newer_list(sidebar_page):
    page = sidebar_page
    page.evaluate('() => { window.oldLoad = loadChats(); window.newLoad = loadChats(); }')
    page.evaluate('pendingLists[1]({ok:true,json:async()=>({chats:[{id:2,agent_name:"New"}]})})')
    page.evaluate('newLoad')
    page.evaluate('pendingLists[0]({ok:true,json:async()=>({chats:[{id:1,agent_name:"Old"}]})})')
    page.evaluate('oldLoad')
    assert page.evaluate('rendered.map(row=>row.id)') == [2]


def test_primary_lookup_failure_does_not_hide_other_agents(sidebar_page):
    page = sidebar_page
    page.evaluate('''() => {
        primaryAgentData = {main_conversation_id:1, name:'Primary'};
        window.fetch = async url => {
            if (url === '/api/chats') return {ok:true,json:async()=>({chats:[{id:1},{id:2}]})};
            throw new Error('Offline');
        };
    }''')
    page.evaluate('loadChats()')
    assert page.evaluate('rendered.map(row=>row.id)') == [2]
    assert page.evaluate('primaryAgentData.name') == 'Primary'


def test_late_primary_lookup_cannot_undo_saved_avatar(sidebar_page):
    page = sidebar_page
    page.evaluate('''() => {
        primaryAgentData = {main_conversation_id:1, name:'Primary',skin_name:'oracle'};
        loadAssistantAvatarSkin = async () => {};
        window.fetch = async (url, options) => {
            if (url === '/api/chats') return {ok:true,json:async()=>({chats:[]})};
            if (options?.method === 'PATCH') return {ok:true,json:async()=>({primary_agent:{
                main_conversation_id:1,name:'Primary',skin_name:'hayley'
            }})};
            return await new Promise(resolve => { window.finishPrimary = () => resolve({ok:true,json:async()=>({
                primary_agent:{main_conversation_id:1,name:'Primary',skin_name:'oracle'}
            })}); });
        };
        window.loading = loadChats();
    }''')
    page.wait_for_function('typeof finishPrimary === "function"')
    page.evaluate("savePrimaryAvatar('hayley')")
    page.evaluate('finishPrimary()')
    page.evaluate('loading')
    assert page.evaluate('primaryAgentData.skin_name') == 'hayley'
