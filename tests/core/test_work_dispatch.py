from __future__ import annotations

import contextlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow, DevelopmentWorkItem
from distr.core.workflow.work_dispatch import dispatch_work_item


@contextlib.contextmanager
def _session(factory):
    db = factory()
    try:
        yield db
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("run_result, starts_timer", [
    ({"run_id": 99, "status": "running"}, True),
    ({"run_id": 99, "status": "waiting", "waiting_kind": "run_briefing"}, True),
    ({"status": "already_dispatched", "error": "Queue already advanced."}, False),
    ({"run_id": 99, "success": False, "status": "failed"}, False),
])
def test_dispatch_prepares_thread_before_starting_run(monkeypatch, run_result, starts_timer):
    calls = []

    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch._resolve_scope",
        lambda **kwargs: {
            "title": "Open-ended work",
            "project_id": None,
            "board_id": None,
            "board_provider": None,
            "board_key": None,
            "board_ticket_key": None,
            "board_ticket_title": None,
            "board_ticket_lane": None,
        },
    )

    def ensure(**kwargs):
        calls.append(("thread", kwargs))
        return 17

    def start(workflow_id, **kwargs):
        calls.append(("run", {"workflow_id": workflow_id, **kwargs}))
        return dict(run_result)

    monkeypatch.setattr("distr.core.workflow.development_threads.ensure_development_thread", ensure)
    monkeypatch.setattr("distr.core.workflow.service.start_workflow_run", start)
    monkeypatch.setattr(
        "distr.core.workflow.development_control.resume_thread_time",
        lambda chat_id, **_kwargs: calls.append(("time", {"chat_id": chat_id})) or {"chat_id": chat_id},
    )

    result = dispatch_work_item(
        workflow_id=44,
        context="Implement the change",
        source_type="initiative",
        source_ref="proposal-9",
        dispatch_async=True,
    )

    assert [kind for kind, _ in calls] == ["thread", "run"] + (["time"] if starts_timer else [])
    assert calls[1][1]["run_metadata"]["chat_id"] == 17
    assert calls[1][1]["run_metadata"]["work_item_identity"] == "source:initiative:proposal-9"
    orchestration = calls[1][1]["run_metadata"]["orchestration"]
    assert orchestration["owner_chat_id"] == 17
    assert orchestration["agent_role"] == "workflow_orchestrator"
    assert orchestration["execution_kind"] == "deterministic_orchestrator_subagent"
    assert result["development_url"] == "/development/threads/17/"


def test_dispatch_never_starts_run_when_thread_preparation_fails(monkeypatch):
    monkeypatch.setattr(
        "distr.core.workflow.work_dispatch._resolve_scope",
        lambda **kwargs: {
            "title": "Broken preparation",
            "project_id": None,
            "board_id": None,
            "board_provider": None,
            "board_key": None,
            "board_ticket_key": None,
            "board_ticket_title": None,
            "board_ticket_lane": None,
        },
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_threads.ensure_development_thread",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )
    starts = []
    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: starts.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="thread unavailable"):
        dispatch_work_item(workflow_id=44, context="Do not run")
    assert starts == []


def test_development_timer_resumes_only_after_workflow_launch_is_accepted(monkeypatch):
    from unittest.mock import MagicMock

    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher, service

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    monkeypatch.setattr(dispatcher, "get_session", provider)
    monkeypatch.setattr(service, "get_session", provider)
    with provider() as db:
        workflow = AutoWorkflow(name="Empty workflow")
        chat = Chat(title="Review the work")
        db.add_all([workflow, chat])
        db.flush()
        db.add(DevelopmentWorkItem(chat_id=chat.id, identity_key=f"thread:{chat.id}", time_paused=True))

    result = dispatch_work_item(workflow_id=workflow.id, chat_id=chat.id, _session_provider=provider)

    assert result["error"] == "Workflow has no steps"
    assert result["development_url"] == f"/development/threads/{chat.id}/"
    with provider() as db:
        work = db.query(DevelopmentWorkItem).filter_by(chat_id=chat.id).one()
        assert work.time_paused
        assert work.time_started_at is None
        assert work.time_last_activity_at is None
        assert db.query(AutoWorkflowRun).count() == 0

    with provider() as db:
        db.add(AutoWorkflowStep(workflow_id=workflow.id, name="Review", action_type="agent_instruction",
                                instruction="Review the work."))
    timer = MagicMock()
    monkeypatch.setattr(dispatcher.threading, "Timer", timer)
    monkeypatch.setattr(dispatcher, "_initializing_runs", set())
    result = dispatch_work_item(workflow_id=workflow.id, chat_id=chat.id, dispatch_async=True,
                                _session_provider=provider)
    assert result["status"] == "started"
    timer.return_value.start.assert_called_once()
    with provider() as db:
        work = db.query(DevelopmentWorkItem).filter_by(chat_id=chat.id).one()
        assert not work.time_paused
        assert work.time_started_at is not None
        assert work.time_last_activity_at == work.time_started_at
        assert db.get(AutoWorkflowRun, result["run_id"]).chat_id == chat.id
    engine.dispose()


@pytest.mark.parametrize('entry', ['dispatcher', 'development'])
@pytest.mark.parametrize('invalid', ['archived', 'deleted_step', 'foreign_step', 'configured_step'])
def test_stale_workflow_selection_cannot_start_or_prepare_work(monkeypatch, entry, invalid):
    from unittest.mock import MagicMock
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher, service

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    monkeypatch.setattr(dispatcher, 'get_session', provider)
    monkeypatch.setattr(service, 'get_session', provider)
    timer = MagicMock()
    monkeypatch.setattr(dispatcher.threading, 'Timer', timer)
    monkeypatch.setattr(dispatcher, '_initializing_runs', set())
    prepare = MagicMock(return_value=17)
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', prepare)
    with provider() as db:
        workflow = AutoWorkflow(name='Selected workflow', status='archived' if invalid == 'archived' else 'active')
        if invalid == 'configured_step':
            workflow.start_step_position = 99
        other = AutoWorkflow(name='Other workflow', status='active')
        db.add_all([workflow, other])
        db.flush()
        first = AutoWorkflowStep(workflow_id=workflow.id, position=0, name='First action',
                                 action_type='run_command', code='Never run this')
        foreign = AutoWorkflowStep(workflow_id=other.id, position=0, name='Other action',
                                   action_type='run_command', code='Never run this either')
        db.add_all([first, foreign])
        db.flush()
        workflow_id, foreign_id = workflow.id, foreign.id
    start_step = None if invalid in {'archived', 'configured_step'} else foreign_id if invalid == 'foreign_step' else 99999
    if entry == 'dispatcher':
        result = dispatcher.start_workflow_run(workflow_id, start_step_id=start_step, dispatch_async=True)
    else:
        result = dispatch_work_item(workflow_id=workflow_id, start_step_id=start_step, dispatch_async=True,
                                    _session_provider=provider)
    assert result.get('error')
    assert 'archived' in result['error'].lower() if invalid == 'archived' else 'step' in result['error'].lower()
    prepare.assert_not_called()
    timer.return_value.start.assert_not_called()
    with provider() as db:
        assert db.query(AutoWorkflowRun).count() == 0
        assert db.query(Chat).count() == 0
    engine.dispose()


@pytest.mark.parametrize('string_id', [False, True])
def test_selected_current_step_starts_without_repeating_the_first_step(monkeypatch, string_id):
    from unittest.mock import MagicMock
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    monkeypatch.setattr(dispatcher, 'get_session', provider)
    timer = MagicMock()
    monkeypatch.setattr(dispatcher.threading, 'Timer', timer)
    monkeypatch.setattr(dispatcher, '_initializing_runs', set())
    with provider() as db:
        workflow = AutoWorkflow(name='Explicit start', status='draft')
        db.add(workflow)
        db.flush()
        first = AutoWorkflowStep(workflow_id=workflow.id, position=0, name='Earlier action',
                                 action_type='run_command', code='Never run')
        second = AutoWorkflowStep(workflow_id=workflow.id, position=1, name='Selected action',
                                  action_type='run_command', code='Never run')
        db.add_all([first, second])
        db.flush()
        workflow_id, step_id = workflow.id, second.id
    result = dispatcher.start_workflow_run(workflow_id, start_step_id=str(step_id) if string_id else step_id,
                                           dispatch_async=True)
    assert result['status'] == 'started'
    timer.return_value.start.assert_called_once()
    with provider() as db:
        assert db.get(AutoWorkflowRun, result['run_id']).current_step_id == step_id
    engine.dispose()


@pytest.mark.parametrize('cancel_before_release', [False, True])
def test_launch_after_run_commit_cannot_replace_a_worker_still_initializing(tmp_path, monkeypatch, cancel_before_release):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import MagicMock
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher

    engine = create_engine(f"sqlite:///{tmp_path / 'launch-race.sqlite'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    monkeypatch.setattr(dispatcher, 'get_session', provider)
    timer = MagicMock()
    monkeypatch.setattr(dispatcher.threading, 'Timer', timer)
    monkeypatch.setattr(dispatcher, '_initializing_runs', set())
    monkeypatch.setattr(dispatcher, '_active_runs', {})
    with provider() as db:
        workflow = AutoWorkflow(name='One worker')
        db.add(workflow)
        db.flush()
        db.add(AutoWorkflowStep(workflow_id=workflow.id, position=0, name='Action',
                                action_type='run_command', code='Never execute'))
        workflow_id = workflow.id
    committed, release = threading.Event(), threading.Event()

    def delayed_event(*_args, **_kwargs):
        if threading.current_thread().name.startswith('first-launch'):
            committed.set()
            assert release.wait(5)

    monkeypatch.setattr(dispatcher, 'record_workflow_chat_event', delayed_event)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='first-launch') as pool:
        first = pool.submit(dispatcher.start_workflow_run, workflow_id, dispatch_async=True)
        try:
            assert committed.wait(5)
            second = dispatcher.start_workflow_run(workflow_id, dispatch_async=True)
            if cancel_before_release:
                with provider() as db:
                    run_id = db.query(AutoWorkflowRun).one().id
                assert dispatcher.cancel_run(run_id)
        finally:
            release.set()
        accepted = first.result(timeout=5)
    assert accepted['status'] == 'started'
    assert second.get('error') and 'already in progress' in second['error']
    assert timer.return_value.start.call_count == 1
    with provider() as db:
        runs = db.query(AutoWorkflowRun).all()
        assert len(runs) == 1
        assert runs[0].status == ('cancelled' if cancel_before_release else 'running')
        assert (runs[0].id in dispatcher._initializing_runs) is not cancel_before_release
    engine.dispose()


def test_failed_launch_commit_does_not_leave_an_initializing_marker(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from sqlalchemy.orm import Session
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher

    engine = create_engine(f"sqlite:///{tmp_path / 'failed-commit.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    monkeypatch.setattr(dispatcher, 'get_session', provider)
    timer = MagicMock()
    monkeypatch.setattr(dispatcher.threading, 'Timer', timer)
    monkeypatch.setattr(dispatcher, '_initializing_runs', set())
    with provider() as db:
        workflow = AutoWorkflow(name='Commit failure')
        db.add(workflow)
        db.flush()
        db.add(AutoWorkflowStep(workflow_id=workflow.id, position=0, name='Action',
                                action_type='run_command', code='Never execute'))
        workflow_id = workflow.id

    def fail_commit(_db):
        assert dispatcher._initializing_runs
        raise RuntimeError('Database commit failed')

    with monkeypatch.context() as patch:
        patch.setattr(Session, 'commit', fail_commit)
        with pytest.raises(RuntimeError, match='Database commit failed'):
            dispatcher.start_workflow_run(workflow_id, dispatch_async=True)
    assert not dispatcher._initializing_runs
    timer.return_value.start.assert_not_called()
    with provider() as db:
        assert db.query(AutoWorkflowRun).count() == 0
    engine.dispose()


@pytest.mark.parametrize('owner_gone', [False, True])
@pytest.mark.parametrize('settings', [{}, {'concurrency_scope': 'workflow'},
                                    {'execution_mode': 'parallel', 'max_parallel_tickets': 1}])
def test_explicit_start_respects_another_process_owner(tmp_path, monkeypatch, owner_gone, settings):
    from unittest.mock import MagicMock
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
    from distr.core.workflow import dispatcher
    from distr.core.planning import ownership

    engine = create_engine(f"sqlite:///{tmp_path / 'owner.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    monkeypatch.setattr(dispatcher, 'get_session', provider)
    monkeypatch.setattr(dispatcher.threading, 'Timer', MagicMock())
    monkeypatch.setattr(dispatcher, '_initializing_runs', set())
    monkeypatch.setattr(dispatcher, '_active_runs', {})
    owner = {'host': 'other-process', 'pid': 1234, 'started': 10}
    monkeypatch.setattr(ownership, 'owner_is_gone', lambda value: value == owner and owner_gone)
    with provider() as db:
        workflow = AutoWorkflow(name='Process-owned work', run_settings=json.dumps(settings))
        db.add(workflow)
        db.flush()
        db.add(AutoWorkflowStep(workflow_id=workflow.id, position=0, name='Action',
                                action_type='run_command', code='Never execute'))
        previous = AutoWorkflowRun(workflow_id=workflow.id, status='running',
                                   run_data=json.dumps({'workflow_process_owner': owner}))
        db.add(previous)
        db.flush()
        workflow_id, previous_id = workflow.id, previous.id
    result = dispatcher.start_workflow_run(workflow_id, dispatch_async=True)
    with provider() as db:
        if owner_gone:
            assert result['status'] == 'started'
            assert db.get(AutoWorkflowRun, previous_id).status == 'cancelled'
            assert db.query(AutoWorkflowRun).count() == 2
        else:
            assert result.get('error')
            assert db.get(AutoWorkflowRun, previous_id).status == 'running'
            assert db.query(AutoWorkflowRun).count() == 1
    engine.dispose()


def test_dispatch_preserves_external_ticket_identity_when_local_mirror_is_linked(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = lambda: _session(factory)
    with provider() as db:
        workflow = AutoWorkflow(name="Development", workflow_type="manual")
        board = KanbanBoard(name="Project", source="database")
        db.add_all([workflow, board])
        db.flush()
        lane = KanbanLane(board_id=board.id, name="Backlog")
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(title="Approved", lane_id=lane.id, external_id="E2E-91-001")
        chat = Chat(title="Approved", params=json.dumps({"development": {
            "board_provider": "tensology",
            "board_key": "tensology:91",
            "board_ticket_key": "E2E-91-001",
        }}))
        db.add_all([ticket, chat])
        db.flush()
        db.add(DevelopmentWorkItem(
            chat_id=chat.id,
            identity_key="ticket:tensology:tensology:91:e2e-91-001",
            board_provider="tensology",
            board_key="tensology:91",
            ticket_key="E2E-91-001",
            local_ticket_id=ticket.id,
            workflow_id=workflow.id,
        ))
        workflow_id, ticket_id, chat_id = workflow.id, ticket.id, chat.id

    monkeypatch.setattr(
        "distr.core.workflow.service.start_workflow_run",
        lambda *args, **kwargs: {"run_id": 1, "status": "running"},
    )
    monkeypatch.setattr(
        "distr.core.workflow.development_control.resume_thread_time",
        lambda *args, **kwargs: {},
    )
    result = dispatch_work_item(
        workflow_id=workflow_id,
        chat_id=chat_id,
        ticket_id=ticket_id,
        source_type="studio_thread",
        _session_provider=provider,
    )

    assert result["work_item_identity"] == "ticket:tensology:tensology:91:e2e-91-001"
    with provider() as db:
        item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == chat_id).one()
        assert item.identity_key == result["work_item_identity"]
        assert item.board_provider == "tensology"
