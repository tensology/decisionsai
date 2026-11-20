"""Multi-agent chat: roster order, primary/orchestrator, groups, agent-to-agent,
per-agent memory, capability enforcement, chat browser gating."""
import contextlib
import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def ma(monkeypatch, tmp_path):
    from distr.core import db, chat, chat_agents, chat_turns, settings, signals, agent_groups, agent_capabilities
    from distr.core import orchestrator_memory
    from distr.core.services import settings_service
    from distr.core.memory import files as memfiles
    from distr.gui.web.routes import chat as routes
    from distr.core.db import Base, Settings
    import distr.core.db.orchestrator  # noqa: F401  (registers memory tables)

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

    for module in (db, chat, chat_agents, chat_turns, routes, agent_groups, agent_capabilities, orchestrator_memory):
        monkeypatch.setattr(module, 'get_session', session_provider)
    for module in (settings, settings_service, routes):
        monkeypatch.setattr(module, 'load_settings_from_db', load)
    for module in (settings, settings_service):
        monkeypatch.setattr(module, 'save_settings_to_db', save)
    monkeypatch.setattr(orchestrator_memory, 'ensure_orchestrator_memory_tables', lambda: None)
    monkeypatch.setattr(memfiles, 'default_memory_dir', lambda: tmp_path)
    monkeypatch.setattr(signals, 'signal_manager', Mock())
    with session_provider() as session:
        session.add(Settings(selected_oracle='oracle'))
    ids = {}
    for name in ('Hayley', 'Uncle Roger', 'Ada'):
        chat_id, _ = chat.ChatService.create_new_chat(title=name, agent_name=name, skin_name='oracle',
                                                      llm_provider='openai', llm_model='m',
                                                      tts_provider='kokoro', tts_voice='af_sky', activate=False)
        with session_provider() as session:
            ids[name] = chat_agents.agent_for_conversation(session, chat_id).id
        ids[name + ':chat'] = chat_id
    agent_groups.set_primary(ids['Hayley'])
    save({'agent_current_chat_id': ids['Hayley:chat']})
    app = FastAPI()
    app.include_router(routes.create_routes(ROOT / 'distr/gui/web/templates'), prefix='/api')
    yield {'client': TestClient(app), 'sessions': session_provider, 'ids': ids, 'save': save, 'tmp': tmp_path}
    engine.dispose()


def fake_generate(replies):
    """replies: {agent_name: [text, ...]} consumed in order."""
    lock = threading.Lock()

    def generate(agent, messages, emit):
        with lock:
            queue = replies.setdefault(agent.name, [])
            text = queue.pop(0) if queue else f"{agent.name} here."
        emit({'type': 'token', 'agent_id': agent.id, 'text': text})
        return text
    return generate


# ---- roster ---------------------------------------------------------------

def test_new_agents_go_to_bottom_and_reorder_persists_without_changing_primary(ma):
    from distr.core import agent_groups as ag

    names = [a['name'] for a in ag.list_agents()]
    assert names == ['Hayley', 'Uncle Roger', 'Ada']
    ids = ma['ids']
    ag.reorder_agents([ids['Ada'], ids['Hayley'], ids['Uncle Roger']])
    agents = ag.list_agents()
    assert [a['name'] for a in agents] == ['Ada', 'Hayley', 'Uncle Roger']
    assert [a['name'] for a in agents if a['is_primary']] == ['Hayley']
    resp = ma['client'].get('/api/chats')
    rows = [r for r in resp.json()['chats'] if r.get('agent_id')]
    assert sorted(rows, key=lambda r: r['agent_rank'])[0]['agent_name'] == 'Ada'
    assert next(r for r in rows if r['agent_name'] == 'Hayley')['is_primary'] is True


def test_reorder_rejects_unknown_agents(ma):
    assert ma['client'].put('/api/agents/order', json={'agent_ids': [999]}).status_code == 422


def test_make_primary_and_orchestrator_is_unique(ma):
    client, ids = ma['client'], ma['ids']
    assert client.post(f"/api/agents/{ids['Uncle Roger']}/primary").json()['primary_agent']['name'] == 'Uncle Roger'
    assert client.get('/api/agents/primary').json()['primary_agent']['name'] == 'Uncle Roger'
    client.patch(f"/api/agents/{ids['Ada']}", json={'is_orchestrator': True})
    client.patch(f"/api/agents/{ids['Hayley']}", json={'is_orchestrator': True, 'role': 'Planner', 'name': 'Hayley W'})
    agents = {a['id']: a for a in client.get('/api/agents').json()['agents']}
    assert [a['name'] for a in agents.values() if a['is_orchestrator']] == ['Hayley W']
    assert agents[ids['Hayley']]['role'] == 'Planner'


def test_restore_chat_skin_uses_the_loaded_agents_own_skin(ma, monkeypatch):
    from distr.core import chat_sidebar
    from distr.core.db import ChatAgent
    from distr.core.services import settings_service

    with ma['sessions']() as session:
        session.get(ChatAgent, ma['ids']['Uncle Roger']).skin_name = 'rusty'
    seen = []
    monkeypatch.setattr(chat_sidebar, 'validate_chat_skin', lambda value: value)
    monkeypatch.setattr(settings_service, 'update_oracle_skin', lambda skin, persist_chat=True: seen.append(skin))
    chat_sidebar.restore_chat_skin(ma['ids']['Uncle Roger:chat'])
    assert seen == ['rusty']


# ---- delete ---------------------------------------------------------------

def test_primary_cannot_be_deleted_and_delete_removes_everything(ma):
    from distr.core import agent_groups as ag
    from distr.core.db import AgentConversation, Chat, ChatAgent, ChatAgentPrefs, ChatGroupMember
    from distr.core.memory.files import agent_memory_path, append_agent_memory

    client, ids = ma['client'], ma['ids']
    assert client.delete(f"/api/agents/{ids['Hayley']}").status_code == 409
    group = ag.create_group([ids['Hayley'], ids['Ada']])
    append_agent_memory(ids['Ada'], 'Ada secret')
    response = client.delete(f"/api/agents/{ids['Ada']}")
    assert response.status_code == 200, response.text
    assert response.json()['deleted_chat_ids'] == [ids['Ada:chat']]
    with ma['sessions']() as session:
        assert session.get(ChatAgent, ids['Ada']) is None
        assert session.get(Chat, ids['Ada:chat']) is None
        assert not session.query(AgentConversation).filter_by(agent_id=ids['Ada']).count()
        assert not session.query(ChatGroupMember).filter_by(agent_id=ids['Ada']).count()
        assert session.get(ChatAgentPrefs, ids['Ada']) is None
        assert session.get(Chat, group['id']) is not None  # the group survives with Hayley
    assert not agent_memory_path(ids['Ada']).exists()


# ---- groups ---------------------------------------------------------------

def test_group_chats_are_hidden_from_the_agent_list(ma):
    from distr.core import agent_groups as ag

    group = ag.create_group([ma['ids']['Hayley'], ma['ids']['Ada']], 'Team')
    chats = ma['client'].get('/api/chats').json()['chats']
    assert group['id'] not in {c['id'] for c in chats}
    assert [g['title'] for g in ma['client'].get('/api/groups').json()['groups']] == ['Team']


def test_without_orchestrator_or_mention_the_primary_answers(ma):
    from distr.core import agent_groups as ag

    ids = ma['ids']
    group = ag.create_group([ids['Uncle Roger'], ids['Hayley'], ids['Ada']])
    replies = ag.run_turn(group['id'], 'Hello team', generate=fake_generate({}))
    assert [r['agent_name'] for r in replies] == ['Hayley']


def test_mentions_pick_responders_and_run_concurrently(ma):
    from distr.core import agent_groups as ag

    ids = ma['ids']
    group = ag.create_group([ids['Hayley'], ids['Uncle Roger'], ids['Ada']])
    # 5 s was too tight under full-suite load (each agent builds its prompt and
    # transcript first), and a broken barrier only became an error *reply*, which
    # this test never checked, so serial runs could pass and loaded runs flaked.
    barrier = threading.Barrier(2, timeout=30)

    import sys, traceback, faulthandler
    def generate(agent, messages, emit):
        try:
            barrier.wait(timeout=4)
        except threading.BrokenBarrierError:
            print("=== BROKEN", agent.name, flush=True)
            faulthandler.dump_traceback(all_threads=True)
            raise
        return f"{agent.name} ok"
    replies = ag.run_turn(group['id'], '@Ada and @Uncle Roger, thoughts?', generate=generate)
    assert sorted(r['agent_name'] for r in replies) == ['Ada', 'Uncle Roger']
    assert not any(r['error'] for r in replies), [r['content'] for r in replies]


def test_orchestrator_answers_first_and_delegates(ma):
    from distr.core import agent_groups as ag

    ids = ma['ids']
    ag.update_agent(ids['Ada'], is_orchestrator=True)
    group = ag.create_group([ids['Hayley'], ids['Uncle Roger'], ids['Ada']])
    gen = fake_generate({'Ada': ['@Uncle Roger please check the rice.'], 'Uncle Roger': ['Rice is fine.']})
    replies = ag.run_turn(group['id'], 'Plan dinner', generate=gen)
    assert [r['agent_name'] for r in replies] == ['Ada', 'Uncle Roger']
    messages = ag.group_messages(group['id'])['messages']
    assert [m.get('agent_name') or 'Paul' for m in messages] == ['Paul', 'Ada', 'Uncle Roger']


def test_agent_ping_pong_is_capped(ma):
    from distr.core import agent_groups as ag

    ids = ma['ids']
    group = ag.create_group([ids['Hayley'], ids['Ada']])
    gen = fake_generate({'Hayley': ['@Ada you?'] * 10, 'Ada': ['@Hayley you?'] * 10})
    events = []
    replies = ag.run_turn(group['id'], '@Hayley start', emit=events.append, generate=gen)
    assert len(replies) <= ag.MAX_DEPTH
    assert all(sum(1 for r in replies if r['agent_id'] == a) <= ag.MAX_PER_AGENT for a in (ids['Hayley'], ids['Ada']))
    assert events[-1]['type'] == 'end'
    assert any(e['type'] == 'capped' for e in events)


def test_model_error_is_reported_not_replaced(ma):
    from distr.core import agent_groups as ag

    group = ag.create_group([ma['ids']['Hayley']])

    def broken(agent, messages, emit):
        raise RuntimeError('provider down')
    [reply] = ag.run_turn(group['id'], 'hi', generate=broken)
    assert reply['error'] and 'provider down' in reply['content']


def test_group_stream_endpoint(ma, monkeypatch):
    from distr.core import agent_groups as ag

    monkeypatch.setattr(ag, '_stream_agent', fake_generate({'Hayley': ['Hi Paul']}))
    group = ag.create_group([ma['ids']['Hayley'], ma['ids']['Ada']])
    with ma['client'].stream('POST', f"/api/groups/{group['id']}/messages", json={'message': 'hello'}) as resp:
        events = [json.loads(line) for line in resp.iter_lines() if line]
    assert [e['type'] for e in events] == ['message', 'start', 'token', 'done', 'end']
    assert events[3]['content'] == 'Hi Paul'
    assert ma['client'].get(f"/api/groups/{group['id']}").json()['messages'][-1]['agent_name'] == 'Hayley'


def test_agent_to_agent_direct_thread(ma):
    from distr.core import agent_groups as ag

    ids = ma['ids']
    gen = fake_generate({'Uncle Roger': ['Never wash rice with a sieve.']})
    result = ag.message_agent(ids['Hayley'], 'uncle roger', 'How do I wash rice?', generate=gen)
    assert result['agent_name'] == 'Uncle Roger' and result['reply'].startswith('Never')
    again = ag.message_agent(ids['Hayley'], ids['Uncle Roger'], 'Thanks', generate=fake_generate({}))
    assert again['chat_id'] == result['chat_id']  # same direct thread
    messages = ag.group_messages(result['chat_id'])['messages']
    assert [m['agent_name'] for m in messages] == ['Hayley', 'Uncle Roger', 'Hayley', 'Uncle Roger']
    with pytest.raises(ag.GroupError):
        ag.message_agent(ids['Hayley'], 'Hayley', 'self?')


def test_message_agent_tool_sends_from_the_current_agent(ma, monkeypatch):
    from distr.core import agent_groups as ag
    from distr.core.agent.tools.system.agent_workspace_tools import MessageAgentTool

    monkeypatch.setattr(ag, '_stream_agent', fake_generate({'Ada': ['On it.']}))
    monkeypatch.setattr(ag, 'current_agent_id', lambda strict=False: ma['ids']['Hayley'])
    assert MessageAgentTool()._run(agent='Ada', message='Can you help?') == 'Ada replied: On it.'


# ---- per-agent memory -----------------------------------------------------

def test_private_memory_is_isolated_and_shared_stays_readable(ma):
    from distr.core.chat_agents import agent_instructions
    from distr.core.memory.files import append_agent_memory, append_markdown_section
    from distr.core.orchestrator_memory import build_memory_context, extract_and_record_user_memories_from_text, record_user_memory

    ids = ma['ids']
    append_agent_memory(ids['Uncle Roger'], 'Roger likes MSG')
    assert 'Roger likes MSG' in agent_instructions(ids['Uncle Roger:chat'])
    assert 'Roger likes MSG' not in agent_instructions(ids['Hayley:chat'])
    record_user_memory('Paul prefers metric units', category='user_preference', source_type='chat', source_id='x')
    extract_and_record_user_memories_from_text('Remember that I prefer tea over coffee.', source_type='chat',
                                              source_id='y', source_chat_id=ids['Uncle Roger:chat'])
    roger, hayley = build_memory_context(agent_id=ids['Uncle Roger']), build_memory_context(agent_id=ids['Hayley'])
    assert 'metric' in roger and 'metric' in hayley
    if 'tea' in roger:  # extraction is heuristic; when it fires, it must stay private
        assert 'tea' not in hayley


def test_memory_api_lists_and_clears_only_that_agents_memory(ma):
    from distr.core.memory.files import agent_memory_sections, append_agent_memory
    from distr.core.orchestrator_memory import list_user_memories, record_user_memory

    client, ids = ma['client'], ma['ids']
    append_agent_memory(ids['Ada'], 'one')
    append_agent_memory(ids['Ada'], 'two')
    append_agent_memory(ids['Hayley'], 'hayley note')
    record_user_memory('ada learned', category='user_preference', source_type='chat', source_id='a',
                       scope='agent', scope_id=ids['Ada'])
    record_user_memory('shared fact', category='user_preference', source_type='chat', source_id='b')
    memories = client.get(f"/api/agents/{ids['Ada']}/memories").json()['memories']
    assert [m['content'] for m in memories] == ['one', 'two', 'ada learned']
    assert client.delete(f"/api/agents/{ids['Ada']}/memories", params={'memory_id': 'note:0'}).status_code == 200
    assert agent_memory_sections(ids['Ada']) == ['two']
    assert client.delete(f"/api/agents/{ids['Ada']}/memories").status_code == 200
    assert client.get(f"/api/agents/{ids['Ada']}/memories").json()['memories'] == []
    assert agent_memory_sections(ids['Hayley']) == ['hayley note']
    assert [m['content'] for m in list_user_memories()] == ['shared fact']


def test_memory_add_tool_writes_private_memory_for_current_agent(ma, monkeypatch):
    from distr.core import agent_groups as ag
    from distr.core.agent.tools.system import memory_tools
    from distr.core.memory.files import agent_memory_sections

    monkeypatch.setattr(ag, 'current_agent_id', lambda: ma['ids']['Ada'])
    assert memory_tools.MemoryAddTool()._run(section='Pref', content='Ada likes lists') == 'Saved to your private memory.'
    assert 'Ada likes lists' in agent_memory_sections(ma['ids']['Ada'])[0]


# ---- capabilities ---------------------------------------------------------

def test_default_capabilities_and_enforcement(ma, monkeypatch):
    from distr.core import agent_groups as ag
    from distr.core.agent_capabilities import ALL, LIMITED, capabilities_for, set_capabilities, tool_block_reason
    from distr.core.agent.tools.system.agent_workspace_tools import snippets_op, development_overview
    from distr.core.db import Snippet

    ids = ma['ids']
    ag.update_agent(ids['Ada'], is_orchestrator=True)
    assert capabilities_for(ids['Hayley']) == ALL      # primary
    assert capabilities_for(ids['Ada']) == ALL         # orchestrator
    assert capabilities_for(ids['Uncle Roger']) == LIMITED
    assert tool_block_reason('create_action', ids['Uncle Roger']).startswith('Not permitted')
    assert tool_block_reason('play_action', ids['Uncle Roger']) == ''
    assert tool_block_reason('create_action', ids['Hayley']) == ''
    assert snippets_op('create', agent_id=ids['Uncle Roger'], text='x').startswith('Not permitted')
    assert json.loads(snippets_op('list', agent_id=ids['Uncle Roger'])) == []
    assert 'projects' in json.loads(development_overview(agent_id=ids['Uncle Roger']))
    with ma['sessions']() as session:
        session.add(Snippet(title='hi', description='hello'))
    sid = json.loads(snippets_op('list', agent_id=ids['Hayley']))[0]['id']
    assert snippets_op('update', agent_id=ids['Uncle Roger'], snippet_id=sid, text='y').startswith('Not permitted')
    assert snippets_op('update', agent_id=ids['Hayley'], snippet_id=sid, text='hey') == f'Updated snippet {sid}.'
    # Deletes need Paul's confirmation even for full-access agents
    assert snippets_op('delete', agent_id=ids['Hayley'], snippet_id=sid).startswith('Not deleted')
    assert snippets_op('delete', agent_id=ids['Uncle Roger'], snippet_id=sid, confirm=lambda s: True).startswith('Not permitted')
    assert snippets_op('delete', agent_id=ids['Hayley'], snippet_id=sid, confirm=lambda s: True) == f'Deleted snippet {sid}.'
    # Overrides
    set_capabilities(ids['Uncle Roger'], ['read', 'create', 'run'])
    assert capabilities_for(ids['Uncle Roger']) == frozenset({'read', 'create', 'run'})
    set_capabilities(ids['Uncle Roger'], None)
    assert capabilities_for(ids['Uncle Roger']) == LIMITED


def test_intent_guard_blocks_create_action_for_limited_agent(ma, monkeypatch):
    from distr.core import agent_groups as ag
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService as S

    monkeypatch.setattr(ag, 'current_agent_id', lambda strict=False: ma['ids']['Uncle Roger'])
    assert S._tool_intent_block_reason('create_action', 'create a new action called login').startswith('Not permitted')
    assert S._tool_intent_block_reason('play_action', 'run the login action') == ''
    monkeypatch.setattr(ag, 'current_agent_id', lambda strict=False: ma['ids']['Hayley'])
    assert S._tool_intent_block_reason('create_action', 'create a new action called login') == ''
    # yesterday's guard is intact for the primary
    assert 'play_action' in S._tool_intent_block_reason('create_action', 'run my saved action login')


def test_calendar_permissions(ma, monkeypatch):
    from distr.core.agent.tools.system.agent_workspace_tools import calendar_op

    ids = ma['ids']
    created = calendar_op('create', agent_id=ids['Hayley'], title='Focus', start='2026-11-16T09:00', end='2026-11-16T10:00')
    assert created.startswith('Created calendar block')
    block_id = int(created.split()[3].rstrip(':'))
    listed = json.loads(calendar_op('list', agent_id=ids['Uncle Roger'], start='2026-11-16T00:00', end='2026-11-17T00:00'))
    assert [b['title'] for b in listed['blocks']] == ['Focus']
    assert calendar_op('update', agent_id=ids['Uncle Roger'], block_id=block_id, title='x').startswith('Not permitted')
    assert calendar_op('delete', agent_id=ids['Hayley'], block_id=block_id).startswith('Not deleted')
    assert calendar_op('delete', agent_id=ids['Hayley'], block_id=block_id, confirm=lambda s: True).startswith('Deleted')


# ---- browser --------------------------------------------------------------

class FakeBrowser:
    def __init__(self, element):
        self.element, self.calls = element, []

    def __call__(self, req, headed):
        self.calls.append((req['cmd'], headed))
        if req['cmd'] == 'describe':
            return {'ok': True, 'element': self.element}
        if req['cmd'] == 'ping':
            return {'ok': True, 'url': 'https://example.com'}
        return {'ok': True, 'url': 'https://example.com', 'title': 'Example'}


def test_chat_browser_gating(ma, monkeypatch):
    from distr.core.agent.tools.system.agent_workspace_tools import chat_browser
    from distr.core.workflow import dev_browser_tools as dbt

    ids = ma['ids']
    real_check_url = dbt.check_url
    monkeypatch.setattr(dbt, 'check_url', lambda url: None)
    _cards(monkeypatch)  # default card: never answered => no
    plain = FakeBrowser({'text': 'Next', 'is_submit': False})
    # anyone can open (headless by default) without approval
    assert chat_browser('open', agent_id=ids['Uncle Roger'], url='https://example.com', transport=plain)['ok']
    assert ('open', False) in plain.calls
    # limited agent: click needs Paul's approval
    denied = chat_browser('click', agent_id=ids['Uncle Roger'], selector='text=Next', transport=plain)
    assert not denied['ok'] and 'denied' in denied['summary']
    asked = []
    ok = chat_browser('click', agent_id=ids['Uncle Roger'], selector='text=Next', transport=plain,
                      approve=lambda tool, act, args, summary, details: asked.append(summary) or True)
    assert ok['ok'] and len(asked) == 1
    # primary clicks plain buttons freely, but a purchase-like submit still asks
    assert chat_browser('click', agent_id=ids['Hayley'], selector='text=Next', transport=plain)['ok']
    buy = FakeBrowser({'text': 'Place order', 'is_submit': True})
    assert not chat_browser('click', agent_id=ids['Hayley'], selector='#buy', transport=buy)['ok']
    # never types into password fields, even with approval
    pw = FakeBrowser({'is_password': True})
    res = chat_browser('type', agent_id=ids['Hayley'], selector='#pw', text='x', transport=pw, approve=lambda *a: True)
    assert not res['ok'] and 'password' in res['summary']
    # the Decisions app itself is refused
    monkeypatch.setattr(dbt, 'check_url', real_check_url)
    blocked = chat_browser('open', agent_id=ids['Hayley'], url='http://127.0.0.1:8765/chat', transport=plain)
    assert not blocked['ok'] and 'Decisions app' in blocked['summary']


# ---- fail closed ----------------------------------------------------------

def _broken_lookup(strict=False):
    if strict:
        raise RuntimeError('db down')
    return None


def _cards(monkeypatch, answer=None):
    """Replace the in-chat Approve/Deny card. answer(summary) -> Decision; default: never answered."""
    from distr.core import chat_approvals
    asked = []

    def fake(tool, action, args, summary, details=None, **_):
        asked.append(summary)
        if answer is None:
            return chat_approvals.Decision(False, f"No answer within 15 minutes, treated as denied: {summary}.")
        return answer(summary)

    monkeypatch.setattr(chat_approvals, 'request_in_chat', fake)
    return asked


def test_confirmation_fails_closed(monkeypatch):
    from distr.core import chat_approvals
    from distr.core.workflow import dev_approvals
    from distr.core.agent_capabilities import confirm_destructive, request_confirmation, safe_confirm

    # no Chat thread to show the card in => no
    monkeypatch.setattr(chat_approvals, 'current_chat_id', lambda: None)
    assert request_confirmation('t', 'm') is False
    monkeypatch.setattr(chat_approvals, 'current_chat_id', lambda: 7)
    # app unreachable => no
    monkeypatch.setattr(dev_approvals, 'request_approval', lambda *a, **k: {'error': 'Decisions app not reachable'})
    assert request_confirmation('t', 'm') is False
    # request raises => no
    def boom(*a, **k):
        raise RuntimeError('db down')
    monkeypatch.setattr(dev_approvals, 'request_approval', boom)
    assert request_confirmation('t', 'm') is False
    # card shown but no answer / denied => no
    monkeypatch.setattr(dev_approvals, 'request_approval', lambda *a, **k: {'request_id': 'r1'})
    for st in ({'status': 'pending'}, {'status': 'expired'}, {'status': 'denied'}, {'status': 'error'}):
        monkeypatch.setattr(dev_approvals, 'wait', lambda rid, t, st=st: st)
        assert confirm_destructive('Delete x?', timeout_s=0.1) is False
    # approved but the one-shot approval cannot be consumed => no
    monkeypatch.setattr(dev_approvals, 'wait', lambda rid, t: {'status': 'approved', 'approval_id': 'a1'})
    monkeypatch.setattr(dev_approvals, 'consume', lambda *a, **k: False)
    assert confirm_destructive('Delete x?', timeout_s=0.1) is False
    # approved and consumed => yes
    monkeypatch.setattr(dev_approvals, 'consume', lambda *a, **k: True)
    assert confirm_destructive('Delete x?', timeout_s=0.1) is True
    def raises(_s):
        raise RuntimeError('ui gone')
    assert safe_confirm(raises)('x') is False and safe_confirm(None)('x') is False


def test_unresolvable_agent_is_refused(ma, monkeypatch):
    from distr.core import agent_groups as ag
    from distr.core.agent_capabilities import tool_block_reason
    from distr.core.agent.tools.system.agent_workspace_tools import chat_browser, snippets_op

    monkeypatch.setattr(ag, 'current_agent_id', _broken_lookup)
    assert tool_block_reason('create_action').startswith('Not done')
    assert tool_block_reason('play_action').startswith('Not done')
    assert snippets_op('create', text='x').startswith('Not')
    _cards(monkeypatch, answer=lambda s: __import__('distr.core.chat_approvals', fromlist=['Decision']).Decision(True, 'ok'))
    res = chat_browser('click', selector='text=Next', approve=lambda *a: True,
                       transport=FakeBrowser({'text': 'Next', 'is_submit': False}))
    assert not res['ok']


def test_chat_browser_confirm_errors_mean_no(ma, monkeypatch):
    from distr.core.agent.tools.system.agent_workspace_tools import ChatBrowserTool, chat_browser
    from distr.core.workflow import dev_browser_tools as dbt

    monkeypatch.setattr(dbt, 'check_url', lambda url: None)
    plain = FakeBrowser({'text': 'Next', 'is_submit': False})
    def raises(*_a):
        raise RuntimeError('ui gone')
    res = chat_browser('click', agent_id=ma['ids']['Uncle Roger'], selector='text=Next', transport=plain, approve=raises)
    assert not res['ok']
    # The tool with no Chat thread to ask in refuses limited-agent clicks.
    from distr.core import chat_approvals
    monkeypatch.setattr(chat_approvals, 'current_chat_id', lambda: None)
    assert ChatBrowserTool()._confirm_action('Click Next?') is False


def test_playwright_tool_fails_closed_and_follows_capabilities(ma, monkeypatch):
    from distr.core import agent_groups as ag
    from distr.core.agent.tools.integrations.playwright_tool import PlaywrightTool

    ids = ma['ids']
    read = 'page.goto("https://example.com"); print(page.title())'
    click = 'page.goto("https://example.com"); page.click("text=More")'
    # primary: reading runs, clicking needs approval while the safety setting is on
    monkeypatch.setattr(ag, 'current_agent_id', lambda strict=False: ids['Hayley'])
    tool = PlaywrightTool()
    from distr.core.chat_approvals import Decision
    verdict = {'answer': None}
    asked = _cards(monkeypatch, answer=lambda s: verdict['answer'](s) if verdict['answer'] else Decision(False, 'No answer.'))
    assert tool._safety_refusal(read, 'x') == ''
    assert 'not approved' in tool._safety_refusal(click, 'x')              # unanswered card => no
    assert asked and 'Hayley wants to' in asked[-1]
    ma['save']({'initiative_ask_sensitive': False})  # 'Ask before sensitive actions' off
    assert tool._safety_refusal(click, 'x') == ''                           # Paul's setting honoured for full agents
    # limited agent: clicking always asks, even with the setting off
    monkeypatch.setattr(ag, 'current_agent_id', lambda strict=False: ids['Uncle Roger'])
    assert tool._safety_refusal(read, 'x') == ''
    assert 'not approved' in tool._safety_refusal(click, 'x')
    verdict['answer'] = lambda s: Decision(True, 'Approved by Paul.')
    assert tool._safety_refusal(click, 'x') == ''                           # Paul clicked Approve
    def broken(s):
        raise RuntimeError('card failed')
    verdict['answer'] = broken
    assert 'not approved' in tool._safety_refusal(click, 'x')               # broken card => refused
    verdict['answer'] = None
    # unresolvable agent => refused entirely
    monkeypatch.setattr(ag, 'current_agent_id', _broken_lookup)
    assert 'not permitted' in tool._safety_refusal(read, 'x')
    # password fields: never, whoever asks
    monkeypatch.setattr(ag, 'current_agent_id', lambda strict=False: ids['Hayley'])
    assert 'password' in tool._safety_refusal('page.fill("input[type=password]", "x")', 'x')
    # _run returns the refusal without spawning a browser
    monkeypatch.setattr(ag, 'current_agent_id', lambda strict=False: ids['Uncle Roger'])
    assert 'not approved' in tool._run(code=click)


def test_chat_browser_does_not_flip_dev_setting(ma):
    from distr.core.settings import load_settings_from_db

    assert not load_settings_from_db().get('dev_browser_enabled')


# ---- tool intents ---------------------------------------------------------

def test_new_tool_intents():
    from distr.core.agent.tool_intents import forced_tool_names_for_text as forced

    assert 'calendar_blocks' in forced("what's on my calendar tomorrow")
    assert 'snippets' in forced('show my snippets')
    assert 'development_overview' in forced("what's the development status")
    assert 'message_agent' in forced('ask the other agent to check')
    assert 'web_browser' in forced('open https://example.com and read it')
    assert 'web_browser' in forced('check the weather on news24.com')
    assert 'message_agent' not in forced('email paul@example.com')
