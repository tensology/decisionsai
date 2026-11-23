"""Primary identity survives avatar changes, activation and messages to another agent."""
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
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
    from distr.core import db, chat, chat_agents, chat_turns, settings, signals, agent_groups
    from distr.core.services import settings_service
    from distr.gui.web.routes import chat as routes
    from distr.gui.web.routes.settings import skins
    from fastapi import APIRouter
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

    for module in (db, chat, chat_agents, chat_turns, routes, agent_groups):
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
    skins_router = APIRouter()
    skins.register_routes(skins_router, None)
    app.include_router(skins_router, prefix='/api')
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
    assert client.post(f'/api/agent-conversations/{second}/load-in-agent').status_code == 200
    restore_chat_skin(second)
    # The loaded conversation's own agent skin wins, not the primary's.
    assert load()['selected_oracle'] == 'hayley'
    restore_chat_skin(first)
    assert load()['selected_oracle'] == 'rusty'
    response = client.post(f'/api/agent-conversations/{second}/send-to-agent', json={'message':'Reply only pong', 'speak':False})
    assert response.status_code == 200, response.text
    assert signals.web_send_to_agent_requested.emit.call_args.args[0] == second
    assert signals.web_send_to_agent_requested.emit.call_args.args[2] is False
    saved = client.get('/api/agents/primary').json()['primary_agent']
    # Load moves the star: the live/loaded agent is the single primary.
    assert saved['main_conversation_id'] == second
    assert saved['name'] == 'Other agent'
    assert saved['skin_name'] == 'hayley'
    with sessions() as session:
        assert chat_skin(session.get(Chat, second)) == 'hayley'
        assert chat_skin(session.get(Chat, first)) == 'rusty'
        assert session.query(PrimaryChatAgent).count() == 1
        assert session.query(ChatAgent).count() == count
    assert client.delete(f'/api/agent-conversations/{second}?confirm={second}').status_code == 409
    assert client.delete(f'/api/agent-conversations/{first}?confirm={first}').status_code != 409


def test_oracle_background_is_saved_per_agent_and_survives_skin_changes(primary_app):
    from distr.gui.oracle.window import OracleWindow

    client, _, _, _, first, second = primary_app
    config = ROOT / 'assets/avatars/oracle/skin.json'
    original = config.read_bytes()
    desktop = SimpleNamespace(_get_skin_shortcut_order=lambda: ['oracle', 'hayley'])
    select = lambda index: OracleWindow._select_skin_by_shortcut_index(desktop, index)
    assert client.get('/api/agents/primary').json()['primary_agent']['main_conversation_id'] == first
    assert client.put('/api/skins/oracle/background', json={'filename':'1.webm'}).status_code == 200
    assert client.post(f'/api/agent-conversations/{second}/load-in-agent').status_code == 200
    select(1)
    assert client.put('/api/skins/oracle/background', json={'filename':'2.webm'}).status_code == 200
    assert client.put('/api/skins/oracle/background', json={'filename':'../2.webm'}).status_code == 422
    assert client.get('/api/skins/oracle/background').json()['filename'] == '2.webm'
    assert client.get(f'/api/agent-conversations/{first}').json()['oracle_background'] == '1.webm'
    assert client.get(f'/api/agent-conversations/{second}').json()['oracle_background'] == '2.webm'
    assert client.post(f'/api/agent-conversations/{first}/load-in-agent').status_code == 200
    select(2)
    rows = {row['id']: row for row in client.get('/api/agent-conversations').json()['chats']}
    assert (rows[first]['skin_name'], rows[first]['oracle_background']) == ('hayley', '1.webm')
    assert (rows[second]['skin_name'], rows[second]['oracle_background']) == ('oracle', '2.webm')
    select(1)
    assert client.get('/api/skins/oracle/background').json()['filename'] == '1.webm'
    desktop._check_eula_accepted = lambda: True
    desktop._skin_folder = 'oracle'
    desktop._skin_config = SimpleNamespace(events={'idle': SimpleNamespace(animation='1.webm')})
    OracleWindow._cycle_oracle_background(desktop, -1)
    assert client.get('/api/skins/oracle/background').json()['filename'] == '0.webm'
    assert client.get(f'/api/agent-conversations/{second}').json()['oracle_background'] == '2.webm'
    assert config.read_bytes() == original


def test_chat_config_background_updates_only_its_appearance_owner(primary_app):
    from distr.core.db import Chat, Settings

    client, sessions, _, _, first, second = primary_app
    first_background = client.get(f'/api/agent-conversations/{first}').json()['oracle_background']
    assert client.patch(f'/api/agent-conversations/{second}', json={
        'skin_name': 'oracle', 'oracle_background': '../1.webm',
    }).status_code == 422
    assert client.get(f'/api/agent-conversations/{second}').json()['skin_name'] == 'hayley'
    saved = client.patch(f'/api/agent-conversations/{second}', json={
        'skin_name': 'oracle', 'oracle_background': '1.webm',
    })
    assert saved.status_code == 200
    assert saved.json()['oracle_background'] == '1.webm'
    assert client.get(f'/api/agent-conversations/{first}').json()['oracle_background'] == first_background
    assert client.patch(f'/api/agent-conversations/{second}', json={'skin_name': 'hayley'}).status_code == 200
    assert client.get(f'/api/agent-conversations/{second}').json()['oracle_background'] == '1.webm'
    assert client.patch(f'/api/agent-conversations/{second}', json={'oracle_background': None}).status_code == 200
    assert client.get(f'/api/agent-conversations/{second}').json()['oracle_background'] is None

    with sessions() as session:
        legacy = Chat(title='Standalone')
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id
    assert client.patch(f'/api/agent-conversations/{legacy_id}', json={'oracle_background': '2.webm'}).status_code == 200
    assert client.get(f'/api/agent-conversations/{legacy_id}').json()['oracle_background'] == '2.webm'

    with sessions() as session:
        session.query(Settings).one().default_oracle_background = '1.webm'
    creation = {'agent_name': 'New Oracle', 'skin_name': 'oracle'}
    assert client.post('/api/agent-conversations', json={**creation, 'oracle_background': '../2.webm'}).status_code == 422
    for options, expected in (({}, '1.webm'), ({'oracle_background': None}, None),
                              ({'oracle_background': '2.webm'}, '2.webm')):
        created = client.post('/api/agent-conversations', json={**creation, **options})
        assert created.status_code == 200
        assert client.get(f"/api/agent-conversations/{created.json()['id']}").json()['oracle_background'] == expected

    thread = client.post('/api/agent-conversations', json={'skin_name': 'oracle', 'oracle_background': '2.webm'})
    assert thread.status_code == 200
    assert client.get(f"/api/agent-conversations/{thread.json()['id']}").json()['oracle_background'] == '2.webm'


def test_compacting_viewed_chat_keeps_runtime_active_and_preserves_new_voice_turn(primary_app, monkeypatch):
    from distr.core.chat import ChatService
    from distr.gui.web.routes import chat as routes

    client, _, _, signals, first, second = primary_app
    ChatService.add_user_message(second, "Earlier context")

    def summarize(messages, **kwargs):
        assert messages[-1]["content"] == "Earlier context"
        ChatService.add_user_message(second, "Voice turn recorded during compaction")
        return "Earlier context summarized.", "test"

    monkeypatch.setattr(routes, "_summarize_chat_with_model", summarize)
    monkeypatch.setattr(routes, "_maybe_refresh_chat_title", lambda *args, **kwargs: None)
    monkeypatch.setattr("distr.core.orchestrator.emit_event", lambda **kwargs: None)
    result = client.post(f"/api/agent-conversations/{second}/compact", json={})

    assert result.status_code == 200
    assert client.get("/api/agent-conversations").json()["agent_current_chat_id"] == first
    signals.web_load_chat_in_agent_requested.emit.assert_not_called()
    signals.web_chat_compacted.emit.assert_called_once_with(second)
    history = ChatService.get_chat_history(second)
    assert history[-1] == {"role": "user", "content": "Voice turn recorded during compaction"}
    assert len(history) == 2


@pytest.mark.parametrize('width', [390,768,1280,1440])
def test_primary_star_roster_and_switching(width, primary_app, tmp_path):
    """The primary lives in the agent list with a star; the old card stays hidden.
    Clicking another agent's star makes it the loaded/active agent and loads it."""
    client, sessions, load, signals, first, second = primary_app
    assert client.get('/api/agents/primary').json()['primary_agent']['main_conversation_id'] == first
    env = Environment(loader=ChoiceLoader([DictLoader({'base.html':'<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>{% block content %}{% endblock %}</body></html>'}), FileSystemLoader(str(ROOT/'distr/gui/web/templates'))]), autoescape=True)
    html = env.get_template('chat/chat.html').render(base_path='/chat')

    def api(route):
        request=route.request
        path=urlparse(request.url).path
        if path=='/api/skins':
            route.fulfill(json={'skins':[{'name':name.title(),'folder_name':name,'preview_image':'idle.webp'} for name in ('oracle','hayley','rusty')], 'selected_skin':load()['selected_oracle']})
        elif '/preview/' in path:
            route.fulfill(path=str(ROOT/'assets/avatars/hayley/idle.webp'), content_type='image/webp')
        elif path.startswith(('/api/agent-conversations','/api/agents')):
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
        assert not page.get_by_role('region',name='Primary agent',exact=True).is_visible()
        primary_row=page.locator(f'#chatList .chat-item[data-chat-id="{first}"]')
        other_row=page.locator(f'#chatList .chat-item[data-chat-id="{second}"]')
        assert primary_row.count()==1 and other_row.count()==1
        assert 'is-primary-agent' in primary_row.get_attribute('class')
        assert primary_row.locator('.agent-star').inner_text()=='★'
        assert primary_row.locator('.agent-star').get_attribute('aria-label')=='Active agent'
        assert other_row.locator('.agent-star').get_attribute('aria-label')=='Make this the active agent'
        if width == 1280:
            before = primary_row.locator('.chat-thread-avatar img').element_handle()
            page.evaluate('loadChats()')
            assert before.evaluate('node => node === document.querySelector(".is-primary-agent .chat-thread-avatar img")')
        page.screenshot(path=str(tmp_path/f'primary-star-roster-{width}.png'))
        other_row.hover()
        other_row.locator('.agent-star').click()
        page.wait_for_function(f'primaryAgentData && primaryAgentData.main_conversation_id === {second}')
        page.wait_for_function(f'document.querySelector(\'#chatList .chat-item[data-chat-id="{second}"]\').classList.contains("is-primary-agent")')
        assert client.get('/api/agents/primary').json()['primary_agent']['main_conversation_id']==second
        assert page.locator(f'#chatList .chat-item[data-chat-id="{first}"] .agent-star').inner_text()=='☆'
        # The old primary can now be deleted; the new primary cannot.
        assert client.delete(f'/api/agent-conversations/{second}').status_code == 409
        browser.close()
