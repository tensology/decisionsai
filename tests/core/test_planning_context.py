import contextlib
import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem, PlanItem, PlanItemRevision, PlanWorkspace
from distr.core.planning.context import planning_prompt_text, planning_snapshot


@pytest.fixture
def store(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    monkeypatch.setattr('distr.core.db.get_session', session)
    with factory() as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Other')])
        db.add_all([PlanWorkspace(id=1, project_id=1, board_key='decisions:1', board_name='Plan one'),
                    PlanWorkspace(id=2, project_id=2, board_key='decisions:2', board_name='Secret other')])
        db.add_all([PlanItem(id=1, workspace_id=1, title='Readable design', file_path='/project/planning/design.md'),
                    PlanItem(id=2, workspace_id=2, title='Other project secret')])
        db.add_all([PlanItemRevision(item_id=1, revision=1, title='Old'), PlanItemRevision(item_id=1, revision=3, title='Current')])
        db.add_all([Chat(id=1, project_id=1, title='Plan source', params=json.dumps({'plan_workspace_id': 1})),
                    Chat(id=2, project_id=2, title='Mismatched project source', params=json.dumps({'plan_workspace_id': 1})),
                    Chat(id=3, project_id=1, title='Malformed unrelated', params='{broken'),
                    Chat(id=4, project_id=1, title='Legacy archived', is_archived=True, params='{}')])
        db.add(DevelopmentWorkItem(chat_id=4, project_id=1, identity_key='source:plan_workspace:1'))
        db.commit()
    return factory, engine


def test_project_scoped_revisions_chats_and_select_only(store):
    factory, engine = store
    statements = []
    event.listen(engine, 'before_cursor_execute', lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement))
    snapshot = planning_snapshot(1)
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    assert len(statements) <= 9
    assert len(snapshot['workspaces']) == 1
    workspace = snapshot['workspaces'][0]
    assert {key: workspace['items'][0][key] for key in ['id', 'revision', 'title', 'type', 'status', 'path']} == {'id': 1, 'revision': 3, 'title': 'Readable design', 'type': 'document', 'status': 'draft', 'path': '/project/planning/design.md'}
    assert {chat['id'] for chat in workspace['source_chats']} == {1, 4}
    assert next(chat for chat in workspace['source_chats'] if chat['id'] == 4)['archived']
    assert 'Other project' not in json.dumps(snapshot)
    assert snapshot['selected_ui_state'] == snapshot['unsaved_ui_state'] == 'unknown'
    assert snapshot['read_only'] and 'No execution' in snapshot['authority']


def test_explicit_wrong_workspace_missing_project_and_deleted_project_do_not_fall_back(store):
    factory, _ = store
    assert planning_snapshot(1, workspace_id=2)['workspaces'] == []
    assert planning_snapshot(None)['workspaces'] == []
    assert planning_snapshot('malformed')['workspaces'] == []
    from datetime import datetime, timezone
    with factory() as db:
        db.get(Project, 1).deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    assert planning_snapshot(1)['workspaces'] == []


def test_large_inventory_is_bounded_reports_all_omissions_and_preserves_whole_json(store):
    factory, _ = store
    with factory() as db:
        for number in range(10, 30):
            db.add(PlanWorkspace(id=number, project_id=1, board_key=f'decisions:{number}', board_name='文' * 10000))
            for offset in range(12):
                db.add(PlanItem(workspace_id=number, title='文' * 10000, file_path='/long/' + 'p' * 10000))
            for offset in range(5):
                db.add(Chat(project_id=1, title='Source' * 1000, params=json.dumps({'plan_workspace_id': number})))
        db.commit()
    snapshot = planning_snapshot(1)
    assert snapshot['coverage'] == {'workspace_total': 21, 'workspace_omitted': 17, 'item_total': 241, 'item_omitted': 225, 'chat_total': 102, 'chat_omitted': 90}
    assert len(json.dumps(snapshot, ensure_ascii=False)) < 15000
    for budget in [1200, 1500, 1800]:
        text = planning_prompt_text(snapshot, budget)
        assert len(text) <= budget
        view = json.loads(text)
        assert view['coverage'] == snapshot['coverage']
        assert view['prompt_omitted']['items'] > 0
        assert view['prompt_omitted']['workspaces'] >= 0
        assert view['authority'] and view['unsaved_ui_state'] == 'unknown'


def test_shared_developer_prompt_keeps_reference_and_budget(store):
    from distr.core.developer_context import DeveloperProjectContext, DeveloperRuntimeContext, DeveloperWorkContext
    context = DeveloperWorkContext(runtime=DeveloperRuntimeContext(cwd='/project'), active_project=DeveloperProjectContext(id=1, name='One'), planning_snapshot=planning_snapshot(1))
    text = context.to_prompt_text(max_chars=2200)
    assert len(text) <= 2200
    assert 'Readable design' in text and 'revision' in text and 'no' in text.lower()
    assert context.to_dict()['planning_snapshot']['project_id'] == 1
    from distr.core.initiative.context import ContextBundle
    from distr.core.initiative.planners import _system_prompt_for_scope, _user_payload
    payload = json.loads(_user_payload(ContextBundle(developer_context=context.to_dict()), 'Review saved plan', {}))
    assert payload['developer_context']['planning_snapshot']['project_id'] == 1
    assert 'recommendations only' in _system_prompt_for_scope('morning', {})


def test_prompt_keeps_other_board_visible_when_first_board_has_large_artifacts(store):
    factory, _ = store
    with factory() as db:
        db.add(PlanWorkspace(id=3, project_id=1, board_key='decisions:3', board_name='Second board'))
        for workspace_id in (1, 3):
            for kind in ('brief', 'frac', 'prd'):
                db.add(PlanItem(
                    workspace_id=workspace_id,
                    item_type=kind,
                    title=f'Board {workspace_id} {kind}',
                    content=f'Saved {kind} for board {workspace_id}. ' * 80,
                    file_path='/project/planning/' + 'nested/' * 20 + f'{kind}.md',
                ))
        db.commit()
    snapshot = planning_snapshot(1)
    for budget in (1500, 1800):
        prompt = planning_prompt_text(snapshot, budget)
        view = json.loads(prompt)
        assert len(prompt) <= budget
        assert {workspace['id'] for workspace in view['workspaces']} == {1, 3}
        assert view['prompt_omitted']['workspaces'] == 0
        if budget == 1800:
            assert all(workspace['items'][0]['type'] == 'brief' for workspace in view['workspaces'])


def test_native_turn_uses_durable_chat_scope_not_supplied_metadata(store, monkeypatch):
    from distr.core.chat import ChatService
    from distr.core.turn_runtime.context import build_development_context
    monkeypatch.setattr(ChatService, 'get_chat_history', lambda chat_id: [])
    monkeypatch.setattr('distr.core.chat_agents.agent_instructions', lambda chat_id: '')
    messages = build_development_context(1, 'Review', {'project_id': 2}, project_folder='/project', autonomy_level='plan')
    assert messages[-1] == {'role': 'user', 'content': 'Review'}
    reference = next(message['content'] for message in messages if 'Read-only persisted planning reference' in str(message['content']))
    assert 'Readable design' in reference and 'Secret other' not in reference
    assert 'No execution' in reference and 'untrusted' in reference


def test_noncanonical_scope_and_invalid_explicit_or_legacy_chat_markers_are_rejected(store):
    factory, _ = store
    for value in [True, 1.5, '01', '1other', ' 1', '١', '1' * 10000, 2**64]:
        assert planning_snapshot(value)['workspaces'] == []
    with factory() as db:
        db.get(Chat, 4).params = json.dumps({'plan_workspace_id': None})
        for index, tag in enumerate([None, '1', '1other', 1.5, True, {}], 10):
            db.add(Chat(id=index, project_id=1, title='Invalid explicit marker', params=json.dumps({'plan_workspace_id': tag})))
            db.add(DevelopmentWorkItem(chat_id=index, project_id=1, identity_key=f'source:plan_workspace:{index}other'))
        db.add(Chat(id=20, project_id=1, title='Invalid legacy suffix', params='{}'))
        db.add(DevelopmentWorkItem(chat_id=20, project_id=1, identity_key='source:plan_workspace:1other'))
        db.add(Chat(id=21, project_id=1, title='Mismatched owner', params=json.dumps({'plan_workspace_id': 1})))
        db.add(DevelopmentWorkItem(chat_id=21, project_id=2, identity_key='chat:21'))
        db.commit()
    assert {chat['id'] for chat in planning_snapshot(1)['workspaces'][0]['source_chats']} == {1}


def test_saved_requirements_outrank_recent_generic_items_and_keep_meaningful_excerpt(store):
    factory, _ = store
    with factory() as db:
        for index, kind in enumerate(['brief', 'frac', 'prd'], 30):
            db.add(PlanItem(id=index, workspace_id=1, item_type=kind, title=kind, status='approved', content=f'Persisted {kind} requirement: preserve edited source text. ' * 100))
            db.add(PlanItemRevision(item_id=index, revision=2, title=kind, source='user'))
        for index in range(50, 90):
            db.add(PlanItem(id=index, workspace_id=1, title='Recent generic document', content='Generic'))
        db.commit()
    snapshot = planning_snapshot(1)
    items = snapshot['workspaces'][0]['items']
    assert [item['type'] for item in items[:3]] == ['brief', 'frac', 'prd']
    assert all(item['excerpt_omitted_chars'] > 0 for item in items[:3])
    prompt = planning_prompt_text(snapshot, 1800)
    assert len(prompt) <= 1800
    view = json.loads(prompt)
    rendered = view['workspaces'][0]['items']
    assert rendered[0]['saved_excerpt'].startswith('Persisted brief requirement: preserve edited source text.')
    assert rendered[0]['status'] == 'approved' and rendered[0]['revision'] == 2 and rendered[0]['source'] == 'user'
    assert rendered[0]['excerpt_omitted_chars'] > items[0]['excerpt_omitted_chars']
    assert {item['type'] for item in rendered} >= {'brief', 'frac', 'prd'}
    assert view['coverage']['item_omitted'] > 0


def test_active_plan_metadata_cannot_borrow_another_projects_board_plan(store):
    from distr.core.developer_context import DeveloperBoardContext, DeveloperContextAssembler, DeveloperProjectContext
    assembler = DeveloperContextAssembler()
    project = DeveloperProjectContext(id=1, name='One')
    mismatched_board = DeveloperBoardContext(id=2, name='Other')
    assert assembler._fetch_active_plan(mismatched_board, project).id == 1
    assert assembler._fetch_active_plan(None, project).id == 1
    assert assembler._fetch_planning_snapshot(project, {'project_id': 2}) == {}
    assert assembler._fetch_planning_snapshot(project, {'project_id': 1.5}) == {}
    assert assembler._fetch_planning_snapshot(project, {'project_id': 1})['project_id'] == 1
