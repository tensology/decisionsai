"""Primary identity survives avatar changes, activation and messages to another agent."""
import contextlib
import json
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def primary_app(monkeypatch):
    from distr.core import db, chat, chat_agents, chat_turns, settings, signals
    from distr.core.services import settings_service
    from distr.gui.web.routes import chat as routes
    from distr.core.db import Base, Settings

    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session_provider():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load():
        with session_provider() as session:
            row = session.query(Settings).first()
            return {col.name: getattr(row, col.name) for col in Settings.__table__.columns} if row else {}

    def save(values):
        with session_provider() as session:
            row = session.query(Settings).first()
            for key, value in values.items():
                if hasattr(row, key):
                    setattr(row, key, value)

    for module in (db, chat, chat_agents, chat_turns, routes):
        monkeypatch.setattr(module, 'get_session', session_provider)
    for module in (settings, settings_service, routes):
        monkeypatch.setattr(module, 'load_settings_from_db', load)
    for module in (settings, settings_service):
        monkeypatch.setattr(module, 'save_settings_to_db', save)
    signal_manager = Mock()
    monkeypatch.setattr(signals, 'signal_manager', signal_manager)
    monkeypatch.setattr(routes, 'validate_chat_skin', lambda skin: skin if skin in {'oracle', 'hayley', 'rusty'} else (_ for _ in ()).throw(ValueError('Choose an installed avatar')))
    with session_provider() as session:
        session.add(Settings(selected_oracle='oracle'))
    first, _ = chat.ChatService.create_new_chat(title='Main conversation', agent_name='Primary', skin_name='oracle', tts_provider='kokoro', tts_voice='af_sky', activate=False)
    second, _ = chat.ChatService.create_new_chat(title='Other conversation', agent_name='Other agent', skin_name='hayley', tts_provider='kokoro', tts_voice='af_sky', activate=False)
    save({'agent_current_chat_id':first, 'last_chat_id':first})
    app = FastAPI()
    app.include_router(routes.create_routes(ROOT / 'distr/gui/web/templates'), prefix='/api')
    yield TestClient(app), session_provider, load, signal_manager, first, second
    engine.dispose()


def test_primary_api_avatar_activation_and_message_routing(primary_app, monkeypatch):
    from distr.core.db import Chat, ChatAgent, PrimaryChatAgent
    from distr.core.chat_sidebar import restore_chat_skin, chat_skin
    from distr.core import chat_sidebar
    from distr.gui.web.routes import api_extensions

    client, sessions, load, signals, first, second = primary_app
    monkeypatch.setattr(chat_sidebar, 'validate_chat_skin', lambda value:value)
    monkeypatch.setattr(api_extensions, 'max_thread_row_id', lambda chat_id:0)
    primary = client.get('/api/agents/primary').json()['primary_agent']
    assert primary['main_conversation_id'] == first
    with sessions() as session:
        count = session.query(ChatAgent).count()
    assert client.patch('/api/agents/primary/avatar', json={'skin_name':'missing'}).status_code == 422
    assert client.patch('/api/agents/primary/avatar', json={'skin_name':'rusty'}).status_code == 200
    assert client.post(f'/api/chats/{second}/load-in-agent').status_code == 200
    restore_chat_skin(second)
    assert load()['selected_oracle'] == 'rusty'
    response = client.post(f'/api/chats/{second}/send-to-agent', json={'message':'Reply only pong', 'speak':False})
    assert response.status_code == 200, response.text
    assert signals.web_send_to_agent_requested.emit.call_args.args[0] == second
    assert signals.web_send_to_agent_requested.emit.call_args.args[2] is False
    saved = client.get('/api/agents/primary').json()['primary_agent']
    assert saved == {**primary, 'skin_name':'rusty'}
    with sessions() as session:
        assert chat_skin(session.get(Chat, second)) == 'hayley'
        assert chat_skin(session.get(Chat, first)) == 'rusty'
        assert session.query(PrimaryChatAgent).count() == 1
        assert session.query(ChatAgent).count() == count
    assert client.delete(f'/api/chats/{first}').status_code == 409


@pytest.mark.parametrize('width', [390,768,1280,1440])
def test_primary_card_picker_and_switching(width, primary_app, tmp_path):
    client, sessions, load, signals, first, second = primary_app
    env = Environment(loader=ChoiceLoader([DictLoader({'base.html':'<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>{% block content %}{% endblock %}</body></html>'}), FileSystemLoader(str(ROOT/'distr/gui/web/templates'))]), autoescape=True)
    html = env.get_template('chat/chat.html').render(base_path='/chat')
    fail_next = [True]

    def api(route):
        request=route.request
        path=urlparse(request.url).path
        if path=='/api/skins':
            route.fulfill(json={'skins':[{'name':name.title(),'folder_name':name,'preview_image':'idle.webp'} for name in ('oracle','hayley','rusty')], 'selected_skin':load()['selected_oracle']})
        elif '/preview/' in path:
            route.fulfill(path=str(ROOT/'assets/avatars/hayley/idle.webp'), content_type='image/webp')
        elif path=='/api/agents/primary/avatar' and fail_next[0]:
            fail_next[0]=False
            route.fulfill(status=503,json={'detail':'Could not save the avatar. Please try again.'})
        elif path.startswith(('/api/chats','/api/agents/primary')):
            r=client.request(request.method,path,content=request.post_data,headers={'content-type':'application/json'})
            route.fulfill(status=r.status_code,content_type='application/json',body=r.content)
        else:
            route.fulfill(json={})

    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        page=browser.new_page(viewport={'width':width,'height':900})
        page.route('http://decisions.test/api/**',api)
        page.set_content('<base href="http://decisions.test/">'+html)
        page.add_style_tag(content='body{margin:0;background:#101735;color:#ececf1;font-family:Arial}.hidden{display:none}.fixed{position:fixed}')
        page.add_style_tag(path=str(ROOT/'distr/gui/web/static/chat/css/chat.css'))
        page.add_script_tag(path=str(ROOT/'distr/gui/web/static/chat/js/chat.js'))
        page.evaluate('''async () => {
            setupEventListeners();
            loadAssistantAvatarSkin=async()=>{};
            scheduleRefreshChatHeaderStats=()=>{};
            startChatWebSocket=()=>{};
            saveLastChatId=async()=>{};
            await loadChats();
        }''')
        card=page.get_by_role('region',name='Primary agent',exact=True)
        assert card.is_visible()
        assert page.locator('#primaryAgentAvatar').evaluate('e => getComputedStyle(e).borderRadius') == '50%'
        assert page.locator('#primaryAgentImage').evaluate('e => getComputedStyle(e).borderRadius') == '50%'
        # Skin transforms must reach the primary renderer as they do roster avatars.
        media_style = page.evaluate('''() => {
            sidebarSkins.push({folder_name:'scaled-oracle', type:'oracle', idle_animation:'0.webm',
                image_scale:1.55, image_offset_x:8, image_offset_y:-16});
            const media = primaryAvatarMedia('scaled-oracle');
            return {classes:media.className, scale:media.style.getPropertyValue('--assistant-image-scale'),
                x:media.style.getPropertyValue('--assistant-image-offset-x'),
                y:media.style.getPropertyValue('--assistant-image-offset-y')};
        }''')
        assert media_style == {'classes':'assistant-avatar-image oracle', 'scale':'1.55', 'x':'6.25%', 'y':'-12.5%'}
        assert page.locator(f'#chatList .chat-item[data-chat-id="{first}"]').count()==0
        assert page.locator(f'#chatList .chat-item[data-chat-id="{second}"]').count()==1
        page.screenshot(path=str(tmp_path/f'primary-roster-{width}.png'))
        trigger=page.get_by_role('button',name='Change primary agent avatar',exact=True)
        trigger.click()
        modal=page.get_by_role('dialog',name='Primary agent avatar',exact=True)
        page.get_by_role('button',name='Rusty',exact=True).click()
        page.get_by_text('Could not save the avatar. Please try again.',exact=True).wait_for()
        page.screenshot(path=str(tmp_path/f'primary-picker-error-{width}.png'))
        page.get_by_role('button',name='Rusty',exact=True).click()
        modal.wait_for(state='hidden')
        assert trigger.evaluate('e=>e===document.activeElement')
        page.evaluate('async id=>{ await loadChat(id); await loadChats(); }',second)
        assert page.evaluate('primaryAgentData.main_conversation_id')==first
        assert page.evaluate('primaryAgentData.skin_name')=='rusty'
        page.evaluate("document.body.classList.remove('chat-has-active-thread')")
        assert card.is_visible()
        page.screenshot(path=str(tmp_path/f'primary-after-switch-{width}.png'))
        trigger.click()
        assert page.get_by_role('button',name='Rusty',exact=True).get_attribute('aria-pressed')=='true'
        page.get_by_role('button',name='Rusty',exact=True).focus()
        page.keyboard.press('Tab')
        assert page.get_by_role('button',name='Close primary avatar picker',exact=True).evaluate('e=>e===document.activeElement')
        page.keyboard.press('Escape')
        assert not modal.is_visible()
        assert trigger.evaluate('e=>e===document.activeElement')
        page.get_by_role('button',name='Open primary agent Primary',exact=True).click()
        page.locator('#chatTitle').filter(has_text='Primary').wait_for()
        assert page.locator('#chatTitle').inner_text()=='Primary'
        assert page.evaluate('primaryAgentData.main_conversation_id')==first
        assert client.delete(f'/api/chats/{second}').status_code == 200
        page.evaluate('loadChats()')
        assert page.locator('#chatList').inner_text() == 'No other agents yet'
        page.evaluate("document.body.classList.remove('chat-has-active-thread')")
        assert card.is_visible()
        page.screenshot(path=str(tmp_path/f'primary-empty-roster-{width}.png'))
        browser.close()
