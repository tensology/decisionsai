import contextlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core import db as database
from distr.core import tensology_client as tc
from distr.core.db import Base
from distr.core.db.time import utc_now_naive
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.workflow import dispatcher


def finished_review_data():
    return {'project_id': 2, 'waiting_kind': 'verification_review', 'terminal_receipt': {
        'status': 'completed', 'success': True, 'has_completion_evidence': True,
        'result_packet': {'audit': {'final_verdict': 'pass'}},
    }}


@pytest.fixture
def store(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    monkeypatch.setattr(database, 'get_session', session)
    monkeypatch.setattr(dispatcher, 'get_session', session)
    monkeypatch.setattr(tc, '_ticket_push_paused', lambda: False)
    monkeypatch.setattr('distr.core.incoming.telegram_updates.flush_execution_updates', lambda: None)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kwargs: None)
    monkeypatch.setattr('distr.core.tensology_notify.notify_tensology_ticket_incoming', lambda **kwargs: None)
    with tc._TENSOLOGY_TICKETS_LOCK:
        tc._TENSOLOGY_TICKETS.clear()
    with factory() as db:
        workflow = AutoWorkflow(name='Sequential project work')
        db.add(workflow)
        db.flush()
        db.add(AutoWorkflowStep(workflow_id=workflow.id, position=0, action_type='send_to_project_cli', name='Execute', instruction=''))
        board = KanbanBoard(name='Acceptance board')
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name='QA')
        db.add(lane)
        db.flush()
        db.add_all([KanbanTicket(id=1, lane_id=lane.id, title='Finished work'), KanbanTicket(id=2, lane_id=lane.id, title='Next work')])
        run = AutoWorkflowRun(workflow_id=workflow.id, board_id=board.id, ticket_id=1, status='waiting', completed_at=utc_now_naive(), run_data=json.dumps(finished_review_data()))
        db.add(run)
        db.commit()
        ids = workflow.id, run.id
    yield factory, ids
    with tc._TENSOLOGY_TICKETS_LOCK:
        tc._TENSOLOGY_TICKETS.clear()
    engine.dispose()


@pytest.mark.parametrize('missing', [None, 'success', 'has_completion_evidence', 'audit', 'completed_at'])
def test_only_verified_finished_review_releases_concurrency_without_deciding_review(store, missing):
    factory, (workflow_id, run_id) = store
    with factory() as db:
        run = db.get(AutoWorkflowRun, run_id)
        data = finished_review_data()
        if missing in {'success', 'has_completion_evidence'}:
            data['terminal_receipt'].pop(missing)
        elif missing == 'audit':
            data['terminal_receipt']['result_packet'] = {}
        elif missing == 'completed_at':
            run.completed_at = None
        run.run_data = json.dumps(data)
        db.commit()
    result = dispatcher.start_workflow_run(workflow_id, board_id=1, ticket_id=2, run_metadata={'project_id': 2})
    if missing is None:
        # It reaches validation of the next ticket, rather than being rejected
        # by the previous ticket's human review. No worker is launched.
        assert 'has no instruction' in result['error']
        assert tc._tensology_project_slot_free(2)
    else:
        assert 'already running a ticket for this project' in result['error']
        assert not tc._tensology_project_slot_free(2)
    with factory() as db:
        run = db.get(AutoWorkflowRun, run_id)
        assert run.status == 'waiting'
        assert json.loads(run.run_data)['waiting_kind'] == 'verification_review'


@pytest.mark.parametrize('fresh_state', ['approved', 'revoked', 'claimed', 'moved'])
def test_busy_approved_ticket_retries_once_when_slot_is_free_and_honors_pause(store, monkeypatch, fresh_state):
    factory, (_, run_id) = store
    with factory() as db:
        run = db.get(AutoWorkflowRun, run_id)
        run.status = 'running'
        run.completed_at = None
        db.commit()
    clock = [100.0]
    monkeypatch.setattr(tc, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    ticket = {'id': 1163, 'project_id': 175, 'reference': 'ACCEPTANCE-1163', 'project_name': 'Merrypak', 'execution_status': 'awaiting_decisions', 'sent_to_decisions_at': '2026-10-30T20:04:40'}
    project = {'id': 2, 'name': 'Merrypak'}
    client = MagicMock()
    client.post.return_value = {**ticket, 'decisions_thread_id': '42', 'execution_status': 'development'}
    fresh = {**ticket}
    if fresh_state == 'claimed':
        fresh['decisions_thread_id'] = '99'
    elif fresh_state == 'moved':
        fresh['project_id'] = 168
    client.get.return_value = [] if fresh_state == 'revoked' else [fresh]
    configured = MagicMock(return_value=client)
    monkeypatch.setattr(tc, 'configured_tensology_client', configured)
    monkeypatch.setattr(tc, '_resolve_tensology_project', lambda value: project)
    monkeypatch.setattr(tc, '_existing_tensology_thread', lambda value: int(value['decisions_thread_id']) if value.get('decisions_thread_id') else None)
    monkeypatch.setattr(tc, '_localize_tensology_attachments', lambda _, value, __: value)
    monkeypatch.setattr(tc, '_sync_tensology_ticket_state', lambda *args: False)
    create = MagicMock(side_effect=[tc.TensologyApiError('This workflow is already running a ticket for this project', status=409), (42, {})])
    monkeypatch.setattr(tc, '_create_tensology_thread', create)
    assert not tc._process_tensology_ticket(client, ticket)
    assert tc._TENSOLOGY_TICKETS[1163]['_deferred_project_id'] == 2
    clock[0] += 4
    assert tc.sync_tensology_execution_states(force=True) == 0
    configured.assert_not_called()
    client.post.assert_not_called()
    assert create.call_count == 1
    with factory() as db:
        run = db.get(AutoWorkflowRun, run_id)
        run.status = 'waiting'
        run.completed_at = utc_now_naive()
        run.run_data = json.dumps(finished_review_data())
        db.commit()
    monkeypatch.setattr(tc, '_ticket_push_paused', lambda: True)
    assert tc.sync_tensology_execution_states(force=True) == 0
    configured.assert_not_called()
    monkeypatch.setattr(tc, '_ticket_push_paused', lambda: False)
    result = tc.sync_tensology_execution_states(force=True)
    if fresh_state != 'approved':
        assert result == 0
        assert create.call_count == 1
        client.post.assert_not_called()
        if fresh_state in {'revoked', 'moved'}:
            assert 1163 not in tc._TENSOLOGY_TICKETS
        else:
            assert tc._TENSOLOGY_TICKETS[1163]['decisions_thread_id'] == '99'
        return
    assert result == 1
    assert create.call_count == 2
    assert client.post.call_count == 1
    assert client.get.call_count == 1
    assert tc._TENSOLOGY_TICKETS[1163]['decisions_thread_id'] == '42'
    assert tc.sync_tensology_execution_states(force=True) == 0
    assert create.call_count == 2
    assert client.post.call_count == 1


def test_deferred_retries_attempt_at_most_one_ticket_per_existing_tick(store, monkeypatch):
    tickets = [{'id': value, 'project_id': 175, 'project_name': 'Merrypak',
                'execution_status': 'awaiting_decisions', 'sent_to_decisions_at': '2026-10-30',
                '_deferred_project_id': 2} for value in (81, 82)]
    tc._remember_tensology_tickets(tickets)
    client = MagicMock()
    client.get.side_effect = lambda _, query: [{k: v for k, v in tickets[int(query['id']) - 81].items() if not k.startswith('_')}]
    monkeypatch.setattr(tc, 'configured_tensology_client', lambda **kwargs: client)
    monkeypatch.setattr(tc, '_resolve_tensology_project', lambda ticket: {'id': 2})
    monkeypatch.setattr(tc, '_existing_tensology_thread', lambda ticket: int(ticket['decisions_thread_id']) if ticket.get('decisions_thread_id') else None)
    monkeypatch.setattr(tc, '_sync_tensology_ticket_state', lambda *args: False)
    def claim(_, ticket):
        tc._remember_tensology_tickets([{**ticket, 'decisions_thread_id': str(ticket['id']), 'execution_status': 'development'}])
        return True
    process = MagicMock(side_effect=claim)
    monkeypatch.setattr(tc, '_process_tensology_ticket', process)
    assert tc.sync_tensology_execution_states(force=True) == 1
    assert process.call_count == client.get.call_count == 1
    assert tc.sync_tensology_execution_states(force=True) == 1
    assert process.call_count == client.get.call_count == 2
    assert tc.sync_tensology_execution_states(force=True) == 0
    assert process.call_count == client.get.call_count == 2


def test_deferred_fetch_failure_uses_backoff_instead_of_network_loop(store, monkeypatch):
    tc._remember_tensology_tickets([{'id': 81, 'project_id': 175, 'execution_status': 'awaiting_decisions', '_deferred_project_id': 2}])
    clock = [100.0]
    monkeypatch.setattr(tc, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    client = MagicMock()
    client.get.side_effect = OSError('network unavailable')
    monkeypatch.setattr(tc, 'configured_tensology_client', lambda **kwargs: client)
    process = MagicMock()
    monkeypatch.setattr(tc, '_process_tensology_ticket', process)
    assert tc.sync_tensology_execution_states(force=True) == 0
    clock[0] = 104
    assert tc.sync_tensology_execution_states(force=True) == 0
    assert client.get.call_count == 1
    clock[0] = 131
    assert tc.sync_tensology_execution_states(force=True) == 0
    assert client.get.call_count == 2
    process.assert_not_called()
