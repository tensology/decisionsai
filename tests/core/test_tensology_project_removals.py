import json
from datetime import datetime
from unittest.mock import Mock, create_autospec
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat, ChatTurnEvent
from distr.core.db.projects import Project, ProjectRemoval, ProjectFile, ProjectContextItem
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketAuditEntry, ProjectExecutionSession
from distr.core.db.schedule_blocks import ScheduleBlock
from distr.core.db.workflow import DevelopmentWorkItem, DevelopmentTimeEntry
from distr.core import tensology_project_removals as removals
from distr.core.tensology_client import TensologyApiError, TensologyClient


@pytest.fixture
def isolated(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(removals, '_last_attempt', float('-inf'))
    monkeypatch.setattr('distr.core.project_cli_backends.live_sessions.any_live_session_running', lambda _: False)
    monkeypatch.setattr('distr.core.terminals.thread_terminal.project_terminal_sessions', lambda _: [])
    with sessions() as db:
        db.add(Project(id=1, name='Client', folder_location='/work/client', kanban_board_id=10))
        db.add(KanbanBoard(id=10, name='Client', default_project_id=1))
        db.flush()
        db.add(KanbanLane(id=20, board_id=10, name='Backlog'))
        db.flush()
        db.add(KanbanTicket(id=30, lane_id=20, title='Feature', linked_project_id=1))
        db.commit()
    yield sessions
    engine.dispose()


def job(**overrides):
    return {'id': str(uuid4()), 'decisions_project_id': 1, 'source_name': 'Client', 'local_folder_path': '/work/client/', **overrides}


def test_removal_preserves_history_and_files_with_real_foreign_keys(isolated, tmp_path):
    path = tmp_path / 'repository.txt'
    path.write_text('keep')
    with isolated() as db:
        db.add(Chat(id=100, project_id=1, title='IDE thread'))
        db.add(ProjectFile(project_id=1, filename='file', file_path=str(path)))
        db.add(ProjectContextItem(project_id=1, title='Context', content='keep'))
        db.flush()
        db.add(DevelopmentWorkItem(id=101, chat_id=100, identity_key='thread', project_id=1, local_ticket_id=30))
        db.add(ProjectExecutionSession(id=102, project_id=1, ticket_id=30, status='completed'))
        db.add(KanbanTicketAuditEntry(ticket_id=30, summary='Verified feature'))
        db.flush()
        db.add(DevelopmentTimeEntry(work_item_id=101, chat_id=100, started_at=datetime(2026,11,1,10), ended_at=datetime(2026,11,1,11), seconds=3600))
        db.commit()
        receipt = removals.remove_project(db, 1)
        db.commit()
        assert db.get(Project, 1).deleted_at is not None
        assert db.get(Chat, 100).title == 'IDE thread'
        assert db.get(DevelopmentWorkItem, 101).local_ticket_id is None
        assert db.get(ProjectExecutionSession, 102).ticket_id is None
        assert db.query(DevelopmentTimeEntry).one().seconds == 3600
        assert db.query(ProjectContextItem).one().content == 'keep'
        assert db.query(ProjectFile).one().file_path == str(path)
        assert not db.query(KanbanBoard).count()
        assert not db.query(KanbanTicket).count()
        assert json.loads(receipt.audit_snapshot)['ticket_audits'][0]['summary'] == 'Verified feature'
        from distr.core.kanban.lifecycle import ensure_project_boards
        from distr.core.tensology_project_sync import collect_projects
        assert ensure_project_boards(db) == {}
        assert collect_projects(isolated, pinned_keys=[]) == []
        from distr.core.reports.month import load_projects
        assert load_projects(isolated)[1]['name'] == 'Client'
        from distr.core.kanban.scope import set_active_project, active_project
        assert set_active_project(db, 1) == (None, None)
        assert active_project(db) is None
    assert path.read_text() == 'keep'


@pytest.mark.parametrize('kind', ['execution', 'timer', 'work_timer', 'live'])
def test_busy_defers_without_ack_and_keeps_scope(isolated, monkeypatch, kind):
    with isolated() as db:
        if kind == 'execution':
            db.add(ProjectExecutionSession(project_id=1, status='running'))
        elif kind == 'timer':
            db.add(ScheduleBlock(project_id=1, start_at=datetime.now(), end_at=datetime.now(), is_timer_running=True))
        elif kind == 'work_timer':
            db.add(Chat(id=100, project_id=1))
            db.flush()
            db.add(DevelopmentWorkItem(chat_id=100, identity_key='timed', project_id=1, time_started_at=datetime.now()))
        else:
            monkeypatch.setattr('distr.core.project_cli_backends.live_sessions.any_live_session_running', lambda _: True)
        db.commit()
    client = Mock()
    client.get.return_value = {'removals': [job()]}
    monkeypatch.setattr(removals, 'configured_tensology_client', lambda **_: client)
    result = removals.process_project_removals(session_factory=isolated)
    assert result['removals'][0]['status'] == 'deferred'
    client.post.assert_not_called()
    with isolated() as db:
        assert db.get(Project, 1).deleted_at is None
        assert db.get(KanbanTicket, 30) is not None


@pytest.mark.parametrize('field,value', [('source_name','Other'), ('local_folder_path','/other'), ('decisions_project_id',99)])
def test_stale_identity_is_blocked_without_deletion(isolated, monkeypatch, field, value):
    client = Mock()
    client.get.return_value = {'removals': [job(**{field:value})]}
    monkeypatch.setattr(removals, 'configured_tensology_client', lambda **_: client)
    result = removals.process_project_removals(session_factory=isolated)
    assert result['removals'][0]['status'] == 'blocked'
    assert client.post.call_args.args[1]['status'] == 'blocked'
    with isolated() as db:
        assert db.get(Project, 1).deleted_at is None
        assert db.get(KanbanTicket, 30) is not None


def test_committed_removal_replays_after_offline_ack(isolated, monkeypatch):
    client = create_autospec(TensologyClient, instance=True)
    request = job()
    client.get.return_value = {'removals':[request]}
    client.post.side_effect = TensologyApiError('offline')
    monkeypatch.setattr(removals, 'configured_tensology_client', lambda **_: client)
    assert removals.process_project_removals(session_factory=isolated)['removals'][0]['status'] == 'retry_ack'
    with isolated() as db:
        original = db.get(Project, 1).deleted_at
        assert db.get(ProjectRemoval, request['id']).acknowledged_at is None
    client.post.side_effect = None
    assert removals.process_project_removals(force=True,session_factory=isolated)['removals'][0]['status'] == 'completed'
    assert client.post.call_args.kwargs['idempotency_key'] == f"project-removal-{request['id']}-completed"
    with isolated() as db:
        assert db.query(ProjectRemoval).count() == 1
        assert db.get(Project, 1).deleted_at == original
        assert db.get(ProjectRemoval,request['id']).acknowledged_at is not None


def test_not_configured_and_offline_are_retryable_and_poll_is_bounded(isolated, monkeypatch):
    configured = Mock(side_effect=TensologyApiError('disabled',code='not_configured'))
    monkeypatch.setattr(removals, 'configured_tensology_client', configured)
    assert 'error' in removals.process_project_removals(session_factory=isolated)
    assert removals.process_project_removals(session_factory=isolated) == {'throttled': True}
    assert configured.call_count == 1
    client=Mock()
    client.get.side_effect=TensologyApiError('offline')
    configured.side_effect=None
    configured.return_value=client
    assert 'error' in removals.process_project_removals(force=True,session_factory=isolated)
    with isolated() as db:
        assert db.get(Project,1).deleted_at is None


def test_additive_old_schema_migration_is_idempotent():
    from distr.core.db.migrations import ensure_project_removal_schema
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)'))
        conn.execute(text("INSERT INTO projects VALUES (1, 'Historical')"))
    ensure_project_removal_schema(engine)
    ensure_project_removal_schema(engine)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT name,deleted_at FROM projects')).one() == ('Historical',None)
    engine.dispose()


@pytest.mark.parametrize('settings', [{'tensology_enabled': False, 'tensology_key':'present'}, {'tensology_enabled':True,'tensology_key':''}])
def test_disabled_or_empty_key_never_constructs_network_client(isolated, monkeypatch, settings):
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: settings)
    network = Mock(side_effect=AssertionError('Network client constructed'))
    monkeypatch.setattr('distr.core.tensology_client.TensologyClient', network)
    assert 'error' in removals.process_project_removals(session_factory=isolated)
    network.assert_not_called()


def test_direct_delete_and_active_catalogs_use_retained_identity(monkeypatch):
    # A real HTTP route, isolated shared in-memory DB across TestClient's thread.
    from tests.core.test_projects_reorder_api import _make_factory, _make_client, _session_ctx
    factory = _make_factory()
    monkeypatch.setattr('distr.core.db.get_session', lambda: _session_ctx(factory))
    with factory() as db:
        db.add(Project(id=1, name='Past client', folder_location='/past'))
        db.add(Chat(id=100, project_id=1, title='Preserved history'))
        db.commit()
    client = _make_client(factory)
    assert client.delete('/api/projects/1').status_code == 200
    assert client.delete('/api/projects/1').status_code == 200
    assert client.get('/api/projects').json() == []
    assert client.get('/api/projects/1').status_code == 404
    assert client.post('/api/projects/reorder',json={'order':[1]}).status_code == 400
    with factory() as db:
        assert db.get(Chat,100).title == 'Preserved history'
        assert db.get(Project,1).deleted_at is not None
        assert db.query(ProjectRemoval).count() == 1


def test_completed_chat_turn_does_not_leave_project_permanently_busy(isolated):
    with isolated() as db:
        db.add(Chat(id=100, project_id=1))
        db.flush()
        db.add(ChatTurnEvent(chat_id=100,turn_id=100,sequence=1,event_id='start',event_type='turn_started',status='running'))
        db.add(ChatTurnEvent(chat_id=100,turn_id=100,sequence=2,event_id='end',event_type='turn_completed',status='completed'))
        db.commit()
        removals.remove_project(db,1)
        db.commit()
        assert db.get(Project,1).deleted_at is not None
        assert db.query(ChatTurnEvent).count() == 2


def test_unfinished_chat_turn_and_shared_board_fail_closed(isolated):
    with isolated() as db:
        db.add(Chat(id=100,project_id=1))
        db.flush()
        db.add(ChatTurnEvent(chat_id=100,turn_id=100,sequence=1,event_id='start',event_type='turn_started',status='running'))
        db.commit()
        with pytest.raises(removals.ProjectBusy):
            removals.remove_project(db,1)
        db.add(Project(id=2,name='Other',kanban_board_id=10))
        db.commit()
        with pytest.raises(removals.RemovalBlocked,match='shared'):
            removals.remove_project(db,1)


def test_remote_ticket_cannot_resolve_or_repair_removed_project(isolated, monkeypatch, tmp_path):
    from distr.core import tensology_client
    monkeypatch.setattr('distr.core.db.get_session',isolated)
    with isolated() as db:
        removals.remove_project(db,1)
        db.commit()
    ticket={'decisions_project_id':1,'project_name':'Client'}
    assert tensology_client._resolve_tensology_project(ticket) is None
    monkeypatch.setattr(tensology_client,'_ticket_repository_folder',lambda *_:tmp_path)
    monkeypatch.setattr(tensology_client,'_settings_high_route',lambda:('codex','gpt-5.2'))
    assert tensology_client._create_tensology_project(Mock(),ticket) is None
    with isolated() as db:
        assert db.query(Project).count() == 1
        assert db.get(Project,1).deleted_at is not None


def test_board_only_automation_pauses_and_retains_its_runs(isolated):
    from distr.core.db.automation import Automation, AutomationRun
    with isolated() as db:
        db.add(Automation(id=100,name='Board only',project_id=None,board_id=10,status='active'))
        db.flush()
        db.add(AutomationRun(automation_id=100,status='completed'))
        db.commit()
        removals.remove_project(db,1)
        db.commit()
        assert db.get(Automation,100).status == 'paused'
        assert db.query(AutomationRun).one().status == 'completed'


def test_removal_preserves_exact_retained_chronology_including_legacy_execution_end(isolated):
    from distr.core.db.kanban import ProjectExecutionEvent
    stamp = datetime(2026,10,23,10,12,34,567890)
    end = datetime(2026,10,23,10,42,34,567890)
    with isolated() as db:
        db.add(Chat(id=100,project_id=1,title='Recorded IDE work',created_date=stamp,modified_date=end))
        db.flush()
        db.add(DevelopmentWorkItem(id=101,chat_id=100,identity_key='chronology',project_id=1,
                                  local_ticket_id=30,time_accumulated_seconds=1800,
                                  created_at=stamp,modified_at=end,time_last_activity_at=end))
        # Older completed sessions have no completed_at. Reports must keep using
        # the historical updated_at, never the later project-removal timestamp.
        db.add(ProjectExecutionSession(id=102,project_id=1,ticket_id=30,status='completed',
                                       started_at=stamp,updated_at=end,completed_at=None))
        db.add(ScheduleBlock(id=103,project_id=1,board_id=10,ticket_id=30,
                             start_at=stamp,end_at=end,created_date=stamp,modified_date=end,
                             is_timer_entry=True,is_timer_running=False))
        db.flush()
        db.add(DevelopmentTimeEntry(id=104,work_item_id=101,chat_id=100,
                                    started_at=stamp,ended_at=end,seconds=1800,created_at=end))
        db.add(ProjectExecutionEvent(id=105,session_id=102,event_type='complete',
                                     status='completed',created_at=end))
        db.commit()
        models = [(Chat,100),(DevelopmentWorkItem,101),(ProjectExecutionSession,102),
                  (ScheduleBlock,103),(DevelopmentTimeEntry,104),(ProjectExecutionEvent,105)]
        def snapshot(model, key):
            row=db.get(model,key)
            return {column.name:getattr(row,column.name) for column in model.__table__.columns}
        before = {(model,key):snapshot(model,key) for model,key in models}
        removals.remove_project(db,1)
        db.commit()
        for model,key in models:
            expected = dict(before[model,key])
            if model is DevelopmentWorkItem:
                expected['local_ticket_id'] = None
            if model is ProjectExecutionSession:
                expected['ticket_id'] = None
            assert snapshot(model,key) == expected, model.__name__
        execution = db.get(ProjectExecutionSession,102)
        assert execution.completed_at is None
        assert (execution.updated_at-execution.started_at).total_seconds() == 1800
