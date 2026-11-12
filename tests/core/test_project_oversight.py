import contextlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentWorkItem
from distr.core.project_oversight import build_project_oversight


@pytest.fixture
def store(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    @contextlib.contextmanager
    def session():
        with factory() as db: yield db
    monkeypatch.setattr('distr.core.db.get_session', session)
    with factory() as db:
        db.add_all([Project(id=1, name='Busy project'), Project(id=2, name='Waiting project'), Project(id=3, name='No threads')])
        db.commit()
    return factory


def add(factory, project, status, *, age=0, params=None, work_project=None, archived=False):
    with factory() as db:
        chat=Chat(title=f'Project {project} work', project_id=project, is_archived=archived,
                  modified_date=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(hours=age),
                  params=json.dumps(params if params is not None else {'development': {'execution': {'status': status}}}))
        db.add(chat); db.flush()
        db.add(DevelopmentWorkItem(chat_id=chat.id, identity_key=f'chat:{chat.id}', project_id=work_project or project))
        db.commit()
        return chat.id


def test_quiet_blocked_project_is_not_starved_by_busy_project(store):
    for _ in range(8): add(store,1,'running')
    waiting = add(store,2,'waiting',age=8)
    result=build_project_oversight(limit=6)
    groups={p['project_id']:p for p in result['projects']}
    assert groups[2]['threads'][0]['chat_id']==waiting
    assert groups[2]['threads'][0]['attention'][0]['kind']=='needs_input'
    assert groups[1]['truncated'] and result['truncated']
    assert groups[3]['threads']==[]
    assert result['project_count']==3 and result['read_only']


def test_scope_mismatch_and_archived_threads_are_not_borrowed(store):
    add(store,2,'waiting',work_project=1)
    add(store,1,'failed',archived=True)
    result=build_project_oversight()
    assert all(not p['threads'] for p in result['projects'])
    assert result['skipped_scope_mismatches']==1


def test_malformed_one_thread_does_not_hide_other_projects(store):
    add(store,1,'',params={'development': ['invalid'], 'telegram_proposal_update': 'invalid'})
    add(store,2,'waiting')
    result=build_project_oversight()
    assert len([p for p in result['projects'] if p['threads']])==2
    first=next(p for p in result['projects'] if p['project_id']==1)['threads'][0]
    assert first['attention'][0]['kind']=='invalid_state'


def test_stale_is_an_observation_not_a_stall_claim_and_unknown_send_needs_reconciliation(store):
    add(store,1,'running',age=2,params={'development': {'execution': {'status':'running'}},
                                     'telegram_proposal_update': {'pending': {'key': 'job:done', 'text': 'Result', 'awaiting': {'token': 'receipt', 'end': 6, 'state': 'unconfirmed'}}}})
    result=build_project_oversight()
    thread=next(p for p in result['projects'] if p['project_id']==1)['threads'][0]
    assert [a['kind'] for a in thread['attention']]==['stale_observation','delivery_unconfirmed']
    assert thread['status']=='running' and 'does not prove' in thread['attention'][0]['recommendation']
    assert thread['url'].startswith('/development/threads/')


@pytest.mark.parametrize('location', ['pending', 'backlog'])
def test_recipient_blocked_delivery_is_attention_first_read_only_and_not_execution_failure(store, location):
    from sqlalchemy import event
    error = 'Telegram recipient changed. Review this retained delivery before sending to another owner.'
    delivery = {'key': 'job:done', 'text': 'x' * 4000 + 'Private completed result', 'offset': 3500, 'delivery_error': error}
    params = {'development': {'execution': {'status': 'completed'}}, 'telegram_proposal_update': {'recipient_id': 42}}
    params['telegram_proposal_update'][location] = delivery if location == 'pending' else [delivery]
    blocked = add(store, 1, 'completed', age=8, params=params)
    for _ in range(8):
        add(store, 1, 'completed')
    with store() as db:
        before = db.get(Chat, blocked).params
    statements = []
    engine = store.kw['bind']
    listener = lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement)
    event.listen(engine, 'before_cursor_execute', listener)
    try:
        snapshot = build_project_oversight(limit=1)
    finally:
        event.remove(engine, 'before_cursor_execute', listener)
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    assert len(statements) == 4
    thread = threads(snapshot)[blocked]
    assert thread['status'] == 'completed'
    assert thread['telegram_delivery'] == {'status': 'blocked', 'reason': error}
    assert thread['attention'][0]['kind'] == 'delivery_blocked'
    assert 'do not resend automatically' in thread['attention'][0]['recommendation']
    from distr.core.project_oversight import project_oversight_prompt_view
    prompt = project_oversight_prompt_view(snapshot)
    assert prompt['attention_threads'][0]['telegram_delivery']['status'] == 'blocked'
    with store() as db:
        assert db.get(Chat, blocked).params == before


@pytest.mark.parametrize('location', ['pending', 'backlog'])
def test_expired_telegram_send_surfaces_without_a_sync_or_receipt_write(store, location):
    from distr.core.incoming.telegram_updates import ACK_TIMEOUT_SECONDS
    from distr.core.project_oversight import project_oversight_prompt_view

    now = datetime.now(timezone.utc)

    def sending(age, *, state='sending', chat_age=0):
        delivery = {'key': 'job:done', 'text': 'Result', 'awaiting': {
            'token': 'receipt', 'end': 6, 'state': state, 'at': now.timestamp() - age,
        }}
        receipt = {location: delivery if location == 'pending' else [delivery]}
        return add(store, 1, 'completed', age=chat_age, params={
            'development': {'execution': {'status': 'completed'}}, 'telegram_proposal_update': receipt,
        })

    expired = sending(ACK_TIMEOUT_SECONDS + 1, chat_age=8)
    fresh = sending(ACK_TIMEOUT_SECONDS)
    queued = sending(ACK_TIMEOUT_SECONDS + 1, state='queued')
    waiting = add(store, 2, 'waiting')
    with store() as db:
        before = db.get(Chat, expired).params
    snapshot = build_project_oversight(limit=2, now=now)
    focused = threads(snapshot)
    assert set(focused) == {expired, waiting}
    assert focused[expired]['status'] == 'completed'
    assert focused[expired]['telegram_delivery']['status'] == 'unconfirmed'
    assert focused[expired]['attention'][0]['kind'] == 'delivery_unconfirmed'
    assert focused[waiting]['attention'][0]['kind'] == 'needs_input'
    prompt = project_oversight_prompt_view(snapshot)
    assert prompt['attention_threads'][0]['chat_id'] == expired
    assert 'did not confirm this send in time' in prompt['attention_threads'][0]['attention'][0]['recommendation']
    all_threads = threads(build_project_oversight(now=now))
    assert 'telegram_delivery' not in all_threads[fresh]
    assert 'telegram_delivery' not in all_threads[queued]
    with store() as db:
        assert db.get(Chat, expired).params == before


def test_backlog_unknown_is_not_starved(store):
    unknown = add(store, 1, 'completed', age=8, params={'telegram_proposal_update': {'backlog': [
        {'key': 'job:done', 'text': 'Result', 'awaiting': {'token': 'receipt', 'end': 6, 'state': 'unconfirmed'}}]}})
    for _ in range(5):
        add(store, 1, 'completed')
    assert threads(build_project_oversight(limit=1))[unknown]['telegram_delivery']['status'] == 'unconfirmed'


@pytest.mark.parametrize('receipt', [
    None, False, '', [], {'backlog': {}}, {'backlog': [None]}, {'pending': []},
    {'recipient_id': '42'}, {'recipient_id': 0},
    {'sent': 42}, {'sent': []}, {'delivered_keys': None},
    {'delivered_keys': 'job:done'}, {'delivered_keys': ['job:done', None]},
    {'pending': {'key': 'job:done', 'text': 'Result', 'offset': -1}},
    {'backlog': [{'key': 'job:done', 'text': 'Result', 'offset': 7}]},
    {'pending': {'key': 'job:done'}},
    *[{location: delivery if location == 'pending' else [delivery]}
      for location in ('pending', 'backlog')
      for attempt in ({'at': 'invalid'}, {'at': 10**400}, {'end': 0}, {'end': 7}, {'token': None}, {'state': 'invalid'})
      for delivery in [{'key': 'job:done', 'text': 'Result',
                        'awaiting': {'token': 'receipt', 'end': 6, 'state': 'sending', **attempt}}]],
])
def test_malformed_telegram_receipt_surfaces_ahead_of_newer_work(store, receipt):
    affected = add(store, 2, 'completed', age=8, params={
        'development': {'execution': {'status': 'completed'}},
        'telegram_proposal_update': receipt,
    })
    healthy = add(store, 1, 'running')
    for _ in range(5):
        add(store, 2, 'completed')
    with store() as db:
        before = db.get(Chat, affected).params

    from distr.core.project_oversight import project_oversight_prompt_view
    snapshot = build_project_oversight(limit=1)
    selected = threads(snapshot)[affected]
    assert selected['status'] == 'completed'
    assert selected['telegram_delivery']['status'] == 'invalid'
    assert selected['attention'][0]['kind'] == 'delivery_invalid'
    prompt_delivery = project_oversight_prompt_view(snapshot)['attention_threads'][0]
    assert prompt_delivery['chat_id'] == affected
    assert prompt_delivery['telegram_delivery']['status'] == 'invalid'
    assert healthy in threads(build_project_oversight(limit=2))
    with store() as db:
        assert db.get(Chat, affected).params == before


def test_selected_thread_reports_invalid_telegram_attempt_fields(store):
    affected = add(store, 1, 'completed', params={
        'development': {'execution': {'status': 'completed'}},
        'telegram_proposal_update': {'pending': {
            'key': 'job:done', 'text': 'Result',
            'awaiting': {'token': 'receipt', 'end': 6, 'state': 'unconfirmed', 'at': 'invalid'},
        }},
    })
    selected = threads(build_project_oversight())[affected]
    assert selected['status'] == 'completed'
    assert selected['telegram_delivery']['status'] == 'invalid'


def test_actual_sender_owner_change_is_visible_without_resending_or_changing_receipt(store, monkeypatch):
    from distr.core.db import get_session
    from distr.core.incoming import telegram_updates as updates
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender
    cid = add(store, 1, 'completed')
    sender = DummyTelegramSender()
    monkeypatch.setattr(updates, 'get_session', get_session)
    monkeypatch.setattr(updates, '_manager', lambda: sender)
    updates.queue_execution_update({'source_metadata': {'approval_source': 'telegram'}}, cid,
                                   {'id': 'observability', 'summary': 'x' * 8000}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    sender.telegram_user_id = 67890
    updates.record_delivery(token, telegram_message_id=77, telegram_chat_id=12345)
    with store() as db:
        before = db.get(Chat, cid).params
    assert 'delivery_error' in before
    observed = threads(build_project_oversight())[cid]
    assert observed['telegram_delivery']['status'] == 'blocked'
    assert observed['status'] == 'completed'
    assert observed['attention'][0]['kind'] == 'delivery_blocked'
    with store() as db:
        assert db.get(Chat, cid).params == before
    assert len(sender.websocket_attempts) == 1


def threads(result):
    return {t['chat_id']: t for group in result['projects'] for t in group['threads']}


def test_latest_actual_workflow_is_used_only_when_projection_is_empty(store):
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.initiative.context import ContextBundle
    from distr.core.initiative.planners import _user_payload
    empty = add(store, 1, '', params={})
    projected = add(store, 2, 'running')
    with store() as db:
        db.query(DevelopmentWorkItem).filter_by(chat_id=empty).one().workflow_id = 11
        db.add_all([AutoWorkflowRun(chat_id=empty, workflow_id=9, status='completed'),
                    AutoWorkflowRun(chat_id=empty, workflow_id=10, status='waiting', run_data=json.dumps({'question': 'Approve this scope?'})),
                    AutoWorkflowRun(chat_id=projected, workflow_id=11, status='failed')])
        db.commit()
    snapshot = build_project_oversight()
    observed = threads(snapshot)
    assert observed[empty]['status'] == 'waiting'
    assert observed[empty]['workflow_id'] == 10
    assert observed[empty]['status_source'] == 'latest_workflow_run'
    assert observed[empty]['attention'][0]['question'] == 'Approve this scope?'
    assert observed[projected]['status'] == 'running'
    assert observed[projected]['status_source'] == 'thread_projection'
    payload = json.loads(_user_payload(ContextBundle(project_oversight=snapshot), 'Review project attention', {}))
    view = payload['project_oversight']
    context = {thread['chat_id']: thread for thread in view['attention_threads']}
    assert context[empty]['workflow_id'] == 10
    assert context[empty]['assigned_workflow_id'] == 11
    assert context[empty]['job_id'] == observed[empty]['job_id']
    assert 'workflow_id' not in context[projected]
    assert view['read_only'] and view['recommendation_only']


def test_latest_workflow_ignores_runs_outside_thread_project_or_ticket(store):
    from distr.core.db.workflow import AutoWorkflowRun

    chat_id = add(store, 1, '', params={})
    with store() as db:
        db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one().local_ticket_id = 50
        db.add_all([
            AutoWorkflowRun(chat_id=chat_id, workflow_id=9, ticket_id=50, status='waiting',
                            run_data=json.dumps({'project_id': '1', 'question': 'Review this plan?'})),
            AutoWorkflowRun(chat_id=chat_id, workflow_id=10, ticket_id=50, status='failed',
                            run_data=json.dumps({'project_id': 2, 'question': 'Other project'})),
            AutoWorkflowRun(chat_id=chat_id, workflow_id=11, ticket_id=51, status='failed',
                            run_data=json.dumps({'project_id': 1, 'question': 'Other ticket'})),
            AutoWorkflowRun(chat_id=chat_id, workflow_id=12, status='failed',
                            run_data=json.dumps({'project_id': 1})),
        ])
        db.commit()

    observed = threads(build_project_oversight(limit=1))[chat_id]
    assert observed['workflow_id'] == 9
    assert observed['status'] == 'waiting'
    assert observed['attention'][0]['question'] == 'Review this plan?'


def test_open_ended_thread_does_not_inherit_a_ticket_run(store):
    from distr.core.db.workflow import AutoWorkflowRun

    chat_id = add(store, 1, '', params={})
    with store() as db:
        db.add(AutoWorkflowRun(chat_id=chat_id, workflow_id=11, ticket_id=51, status='failed'))
        db.commit()
    observed = threads(build_project_oversight())[chat_id]
    assert observed['status'] == 'idle'
    assert observed['status_source'] == 'no_execution_evidence'


@pytest.mark.parametrize('session_status, expected', [('failed', 'failed'), ('cancelled', 'cancelled'), ('completed', 'running')])
def test_execution_session_terminal_failure_is_read_only_and_success_never_premature(store, session_status, expected):
    from distr.core.db.kanban import ProjectExecutionSession
    with store() as db:
        session = ProjectExecutionSession(project_id=1, status=session_status, error='Provider failure')
        db.add(session); db.commit(); sid = session.id
    params = {'development': {'execution': {'status': 'running', 'execution_session_id': sid, 'summary': 'Old progress'}}}
    cid = add(store, 1, '', params=params)
    thread = threads(build_project_oversight())[cid]
    assert thread['status'] == expected
    assert thread['status_source'] == ('thread_projection' if session_status == 'completed' else 'execution_session')
    if expected == 'failed':
        assert thread['summary'] == 'Provider failure'
    with store() as db:
        assert json.loads(db.get(Chat, cid).params) == params


def test_session_from_another_project_does_not_borrow_failure_state(store):
    from distr.core.db.kanban import ProjectExecutionSession
    with store() as db:
        session = ProjectExecutionSession(project_id=2, status='failed', error='Foreign secret summary')
        db.add(session); db.commit(); sid = session.id
    cid = add(store, 1, '', params={'development': {'execution': {'status': 'running', 'execution_session_id': sid}}})
    thread = threads(build_project_oversight())[cid]
    assert thread['status'] == 'running' and thread['summary'] == ''
    assert thread['attention'][0]['kind'] == 'invalid_state'


@pytest.mark.parametrize('scope', ['different_ticket', 'different_chat', 'different_turn', 'malformed_turn',
                                   'missing', 'malformed_packet'])
def test_oversight_does_not_borrow_a_same_project_failure(store, scope):
    from distr.core.db.kanban import ProjectExecutionSession
    from distr.core.project_oversight import project_oversight_prompt_view

    chat_id = add(store, 1, 'running')
    other_chat_id = add(store, 1, 'running')
    with store() as db:
        work = db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one()
        work.local_ticket_id = 50
        work.board_key = 'jira:delivery'
        work.ticket_key = 'DEV-50'
        session_id = 9999
        if scope != 'missing':
            session = ProjectExecutionSession(
                project_id=1, ticket_id=51 if scope == 'different_ticket' else 50,
                input_packet='broken' if scope == 'malformed_packet' else json.dumps({
                    'chat_id': other_chat_id if scope == 'different_chat' else chat_id,
                    'turn_id': {'different_turn': 19, 'malformed_turn': '017'}.get(scope, 17),
                }),
                status='failed', error='Failure belongs to a different job',
            )
            db.add(session)
            db.flush()
            session_id = session.id
        db.get(Chat, chat_id).params = json.dumps({
            'development': {'execution': {'status': 'running', 'execution_session_id': session_id,
                                          'turn_id': 17, 'summary': 'Own work in progress'}},
        })
        db.commit()

    snapshot = build_project_oversight(limit=1)
    thread = threads(snapshot)[chat_id]
    assert thread['status'] == 'running'
    assert thread['status_source'] == 'thread_projection'
    assert thread['summary'] == 'Own work in progress'
    assert [item['kind'] for item in thread['attention']] == ['invalid_state']
    prompt = project_oversight_prompt_view(snapshot)['attention_threads'][0]
    assert prompt['local_ticket_id'] == 50
    assert prompt['board_key'] == 'jira:delivery'
    assert prompt['ticket_key'] == 'DEV-50'
    assert 'Failure belongs to a different job' not in json.dumps(snapshot)


def test_oversight_projects_a_failure_from_the_exact_ticket_and_chat(store):
    from distr.core.db.kanban import ProjectExecutionSession

    chat_id = add(store, 1, 'running')
    with store() as db:
        db.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).one().local_ticket_id = 50
        session = ProjectExecutionSession(project_id=1, ticket_id=50, status='failed',
                                          input_packet=json.dumps({'chat_id': str(chat_id)}),
                                          error='Matching provider failure')
        db.add(session)
        db.flush()
        db.get(Chat, chat_id).params = json.dumps({
            'development': {'execution': {'status': 'running', 'execution_session_id': session.id}},
        })
        db.commit()
    thread = threads(build_project_oversight())[chat_id]
    assert thread['status'] == 'failed'
    assert thread['status_source'] == 'execution_session'
    assert thread['summary'] == 'Matching provider failure'
    assert thread['attention'][0]['kind'] == 'failed'


def test_malformed_session_reference_does_not_cast_into_a_real_session(store):
    from distr.core.db.kanban import ProjectExecutionSession
    with store() as db:
        session = ProjectExecutionSession(project_id=1, status='failed', error='Different session')
        db.add(session); db.commit(); sid = session.id
    cid = add(store, 1, '', params={'development': {'execution': {'status': 'running', 'execution_session_id': f'{sid}broken'}}})
    thread = threads(build_project_oversight())[cid]
    assert thread['status'] == 'running' and thread['summary'] == ''
    assert thread['attention'][0]['kind'] == 'invalid_state'


def test_old_attention_beats_new_completed_rows_with_exact_omitted_counts(store):
    old = add(store, 1, 'blocked', age=72)
    for _ in range(8): add(store, 1, 'completed')
    other = add(store, 2, 'waiting')
    result = build_project_oversight(limit=2)
    assert set(threads(result)) == {old, other}
    busy = next(g for g in result['projects'] if g['project_id'] == 1)
    assert busy['total_threads'] == 9 and busy['omitted_threads'] == 8
    assert result['omitted_threads'] == 8


def test_unused_review_slots_go_to_remaining_attention_before_quiet_work(store):
    blocked = [add(store, 1, 'blocked') for _ in range(4)]
    quiet = [add(store, 2, 'completed') for _ in range(2)]
    sparse = add(store, 3, 'waiting')
    result = build_project_oversight(limit=5)
    reviewed = threads(result)
    assert len(reviewed) == 5
    assert set(reviewed) == {sparse, quiet[-1], *blocked[-3:]}
    assert result['omitted_threads'] == 2


def test_old_runtime_observation_is_ranked_before_recent_running_threads(store):
    now = datetime.now(timezone.utc)
    fresh = add(store, 1, 'running')
    stale = add(store, 1, 'running', params={'development': {'execution': {
        'status': ' running ', 'updated_at': (now - timedelta(hours=2)).isoformat(),
    }}})
    add(store, 1, 'running', params={'development': {'execution': {
        'status': 'running', 'updated_at': '2',
    }}})
    result = threads(build_project_oversight(limit=1, now=now))
    assert set(result) == {stale}
    assert fresh not in result
    assert result[stale]['status'] == 'running'
    assert result[stale]['attention'][0]['kind'] == 'stale_observation'
    assert 'does not prove' in result[stale]['attention'][0]['recommendation']


@pytest.mark.parametrize('scope', ['matching', 'other_project', 'other_step', 'other_ticket', 'other_workflow',
                                   'other_run', 'older_attempt', 'future', 'not_a_heartbeat'])
def test_workflow_heartbeat_updates_only_the_exact_run_observation(store, scope):
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.db.workflow import AutoWorkflowRun

    now = datetime.now(timezone.utc).replace(microsecond=0)
    quiet = add(store, 1, 'running', age=4)
    observed = add(store, 1, '', params={})
    with store() as db:
        work = db.query(DevelopmentWorkItem).filter_by(chat_id=observed).one()
        work.local_ticket_id = 50
        run = AutoWorkflowRun(chat_id=observed, workflow_id=9, ticket_id=50, current_step_id=100,
                              status='running', started_at=(now - timedelta(hours=3)).replace(tzinfo=None))
        db.add(run)
        db.flush()
        session = ProjectExecutionSession(
            run_id=run.id + 999 if scope == 'other_run' else run.id,
            project_id=2 if scope == 'other_project' else 1,
            ticket_id=51 if scope == 'other_ticket' else 50,
            workflow_id=10 if scope == 'other_workflow' else 9,
            step_id=101 if scope == 'other_step' else 100, status='running',
        )
        db.add(session)
        db.flush()
        event_at = now + timedelta(seconds=10 if scope == 'future' else -10)
        db.add(ProjectExecutionEvent(session_id=session.id,
                                     event_type='log' if scope == 'not_a_heartbeat' else 'heartbeat',
                                     created_at=event_at.replace(tzinfo=None)))
        if scope == 'older_attempt':
            db.add(ProjectExecutionSession(run_id=run.id, project_id=1, ticket_id=50, workflow_id=9,
                                           step_id=100, status='running'))
        db.commit()

    rows = threads(build_project_oversight(now=now))
    selected = threads(build_project_oversight(limit=1, now=now))
    if scope == 'matching':
        assert rows[observed]['observation_age_seconds'] == 10
        assert rows[observed]['observation_source'] == 'worker_heartbeat'
        assert rows[observed]['attention'] == []
        assert set(selected) == {quiet}
    else:
        assert rows[observed]['observation_age_seconds'] == 3 * 3600
        assert rows[observed]['attention'][0]['kind'] == 'stale_observation'
        assert set(selected) == {observed}


@pytest.mark.parametrize('turn_id', [17, 18])
def test_direct_heartbeat_requires_the_current_turn_when_both_ids_are_saved(store, turn_id):
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.project_oversight import project_oversight_prompt_view

    now = datetime.now(timezone.utc).replace(microsecond=0)
    chat_id = add(store, 1, 'running')
    started = now - timedelta(hours=1)
    with store() as db:
        session = ProjectExecutionSession(project_id=1, status='running',
                                          started_at=started.replace(tzinfo=None),
                                          input_packet=json.dumps({'chat_id': chat_id, 'turn_id': str(turn_id)}))
        db.add(session)
        db.flush()
        db.add(ProjectExecutionEvent(session_id=session.id, event_type='heartbeat',
                                     created_at=(now - timedelta(seconds=5)).replace(tzinfo=None)))
        db.get(Chat, chat_id).params = json.dumps({'development': {'execution': {
            'status': 'running', 'turn_id': 17, 'execution_session_id': session.id,
            'started_at': started.isoformat(),
        }}})
        db.commit()
    snapshot = build_project_oversight(now=now)
    observed = threads(snapshot)[chat_id]
    if turn_id == 17:
        assert observed['observation_age_seconds'] == 5
        assert observed['attention'] == []
        assert project_oversight_prompt_view(snapshot)['attention_threads'][0]['observation_source'] == 'worker_heartbeat'
    else:
        assert observed['observation_age_seconds'] == 3600
        assert [item['kind'] for item in observed['attention']] == ['invalid_state', 'stale_observation']


def test_scope_corruption_is_filtered_before_ranking_and_does_not_hide_valid_work(store):
    valid = add(store, 1, 'blocked', age=72)
    for _ in range(8): add(store, 2, 'failed', work_project=1)
    result = build_project_oversight(limit=1)
    assert set(threads(result)) == {valid}
    assert result['skipped_scope_mismatches'] == 8
    assert not result['truncated'] and result['omitted_threads'] == 0


def test_global_budget_keeps_inventory_and_reports_unobserved_projects(store):
    add(store, 1, 'completed')
    add(store, 2, 'blocked')
    add(store, 3, 'failed')
    result = build_project_oversight(limit=2)
    assert len(threads(result)) == 2
    assert len(result['projects']) == 3
    omitted = next(g for g in result['projects'] if g['project_id'] == 1)
    assert omitted['threads'] == [] and omitted['omitted_threads'] == 1 and omitted['truncated']


@pytest.mark.parametrize('raw', ['{broken', '[]', '{"development":{"execution":[1]}}', '{"development":{"execution":{"status":[1]}}}'])
def test_invalid_root_or_execution_is_visible_without_poisoning_other_threads(store, raw):
    cid = add(store, 1, '')
    good = add(store, 2, 'waiting')
    with store() as db:
        db.get(Chat, cid).params = raw
        db.commit()
    result = threads(build_project_oversight())
    assert result[cid]['attention'][0]['kind'] == 'invalid_state'
    assert result[good]['status'] == 'waiting'


def test_queries_are_batched_and_read_only(store):
    from sqlalchemy import event
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.db.workflow import AutoWorkflowRun

    for i in range(12): add(store, 1 if i < 6 else 2, 'waiting')
    chat_id = add(store, 2, '', params={})
    now = datetime.now(timezone.utc)
    with store() as db:
        run = AutoWorkflowRun(chat_id=chat_id, workflow_id=9, status='running',
                              started_at=(now - timedelta(hours=1)).replace(tzinfo=None))
        db.add(run)
        db.flush()
        session = ProjectExecutionSession(run_id=run.id, workflow_id=9, project_id=2, status='running')
        db.add(session)
        db.flush()
        db.add(ProjectExecutionEvent(session_id=session.id, event_type='heartbeat',
                                     created_at=(now - timedelta(seconds=10)).replace(tzinfo=None)))
        db.commit()
    engine = store.kw['bind']
    statements = []
    def capture(_conn, _cursor, statement, *_args): statements.append(statement)
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        result = build_project_oversight(now=now)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert len(threads(result)) == 13
    assert threads(result)[chat_id]['observation_source'] == 'worker_heartbeat'
    assert len(statements) <= 4
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)


def large_snapshot():
    groups = []
    for project_id in range(6):
        rows = [{'chat_id': project_id * 10 + i, 'project_id': project_id, 'url': f'/development/threads/{project_id * 10 + i}/',
                 'title': 'Thread ' + str(i), 'status': 'completed', 'status_source': 'thread_projection',
                 'observed_at': '2026-11-10T18:00:00+00:00', 'scope': 'Scope evidence. ' * 150,
                 'summary': 'Result evidence. ' * 50, 'acceptance_tasks': ['Task acceptance. ' * 15] * 12,
                 'attention': []} for i in range(10)]
        groups.append({'project_id': project_id, 'project_name': f'Project {project_id}', 'total_threads': 20,
                       'omitted_threads': 10, 'attention_count': 0, 'threads': rows})
    groups[-1]['threads'][-1]['attention'] = [{'kind': 'delivery_unconfirmed', 'recommendation': 'Reconcile the source first.'}]
    groups[-1]['attention_count'] = 1
    return {'captured_at': '2026-11-10T18:00:00+00:00', 'project_count': 6, 'projects': groups,
            'coverage': 'Canonical scoped observations, not live heartbeats.', 'omitted_threads': 60, 'truncated': True,
            'skipped_scope_mismatches': 2}


def test_prompt_view_bounds_large_snapshot_preserves_inventory_and_prioritizes_attention():
    from copy import deepcopy
    from distr.core.project_oversight import project_oversight_prompt_view
    snapshot = large_snapshot()
    original = deepcopy(snapshot)
    assert len(json.dumps(snapshot, ensure_ascii=False)) > 200_000
    view = project_oversight_prompt_view(snapshot)
    assert len(json.dumps(view, ensure_ascii=False)) <= 12_000
    assert [(p['project_id'], p['project_name']) for p in view['projects']] == [(p['project_id'], p['project_name']) for p in original['projects']]
    assert view['inventory_omitted_count'] == 0
    assert view['read_model_omitted_threads'] == 60 and view['skipped_scope_mismatches'] == 2
    assert view['coverage'] == original['coverage'] and view['read_model_truncated']
    assert view['attention_threads'][0]['chat_id'] == 59
    assert view['attention_threads'][0]['attention'][0]['kind'] == 'delivery_unconfirmed'
    assert view['thread_details_omitted_count'] == 60 - len(view['attention_threads'])
    assert view['not_all_reviewed'] and view['detail_fields_truncated_count'] > 0
    assert view['read_only'] and view['recommendation_only'] and view['provenance'] == 'project_oversight'
    assert snapshot == original


def test_oversized_inventory_reports_missing_identities_without_losing_totals():
    from distr.core.project_oversight import project_oversight_prompt_view
    snapshot = {'project_count': 500, 'omitted_threads': 321, 'truncated': True,
                'projects': [{'project_id': i, 'project_name': f'Project {i} ' + '長名' * 500,
                              'threads': [], 'total_threads': 12, 'omitted_threads': 12} for i in range(500)]}
    view = project_oversight_prompt_view(snapshot)
    assert len(json.dumps(view, ensure_ascii=False)) <= 12_000
    assert view['project_count'] == 500 and view['read_model_omitted_threads'] == 321
    assert view['inventory_omitted_count'] == 500 - len(view['projects'])
    assert view['inventory_omitted_count'] > 0 and view['inventory_names_truncated_count'] > 0
    assert view['not_all_reviewed']


def test_inventory_that_just_fits_leaves_room_for_delivery_attention():
    from distr.core.project_oversight import project_oversight_prompt_view

    snapshot = large_snapshot()
    inventory_only = {**snapshot, 'projects': [{**project, 'threads': []} for project in snapshot['projects']]}
    budget = len(json.dumps(project_oversight_prompt_view(inventory_only), ensure_ascii=False)) + 40

    view = project_oversight_prompt_view(snapshot, max_chars=budget)

    assert len(json.dumps(view, ensure_ascii=False)) <= budget
    assert view['attention_threads'][0]['chat_id'] == 59
    assert view['attention_threads'][0]['project_id'] == 5
    assert view['attention_threads'][0]['attention'][0]['kind'] == 'delivery_unconfirmed'
    assert view['inventory_omitted_count'] == 6 - len(view['projects'])
    assert view['thread_details_omitted_count'] == 60 - len(view['attention_threads'])


def test_fallback_brief_shares_equal_priority_slots_across_projects():
    from distr.core.initiative.planners import _fallback_project_attention

    snapshot = large_snapshot()
    for project in snapshot['projects']:
        for thread in project['threads']:
            thread['attention'] = []
    for thread in [*snapshot['projects'][1]['threads'], snapshot['projects'][-1]['threads'][-1]]:
        thread['status'] = 'blocked'
        thread['attention'] = [{'kind': 'needs_input', 'recommendation': 'Review the pending question.'}]

    items, _, _ = _fallback_project_attention(snapshot)

    assert len(items) == 4
    assert '/development/threads/59/' in items[1]
    assert 'Project 5' in items[1]


def test_unavailable_oversight_is_not_misrepresented_as_an_empty_review():
    from distr.core.project_oversight import project_oversight_prompt_view
    view = project_oversight_prompt_view({})
    assert view['available'] is False and view['not_all_reviewed']
    assert 'no projects reviewed' in view['coverage']


def test_planner_payload_uses_bounded_view_without_replacing_context_bundle():
    from distr.core.initiative.context import ContextBundle
    from distr.core.initiative.planners import _user_payload
    bundle = ContextBundle(project_oversight=large_snapshot())
    payload = json.loads(_user_payload(bundle, 'Review project attention', {}))
    assert len(json.dumps(payload['project_oversight'], ensure_ascii=False)) <= 12_000
    assert payload['project_oversight']['attention_threads'][0]['chat_id'] == 59
    assert len(bundle.project_oversight['projects'][-1]['threads']) == 10


def test_initiative_payload_uses_bounded_view_without_model_calls(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from distr.core.initiative.context import ContextBundle
    from distr.core.initiative import service as module
    from distr.core import llm_factory
    captured = []
    fake = ModuleType('distr.core.litellm_utils')
    def completion(**kwargs):
        captured.extend(kwargs['messages'])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"action_type":"none"}'))])
    fake.litellm_completion = completion
    monkeypatch.setitem(sys.modules, 'distr.core.litellm_utils', fake)
    monkeypatch.setitem(sys.modules, 'litellm', SimpleNamespace(AuthenticationError=type('FixtureAuthError', (Exception,), {})))
    monkeypatch.setattr(llm_factory, 'resolve_llm_candidates', lambda settings: [('fixture', 'fixture')])
    monkeypatch.setattr(module, '_litellm_model', lambda *args: 'fixture')
    bundle = ContextBundle(project_oversight=large_snapshot())
    service = object.__new__(module.InitiativeService)
    assert service._call_llm(bundle, {}, 'operate') == '{"action_type":"none"}'
    view = json.loads(captured[1]['content'])['project_oversight']
    assert len(json.dumps(view, ensure_ascii=False)) <= 12_000
    assert view['attention_threads'][0]['chat_id'] == 59
    assert bundle.project_oversight['project_count'] == 6
