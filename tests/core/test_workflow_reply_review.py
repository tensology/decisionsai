"""Durable workflow closing reviews execute only on an explicit scoped decision."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat, DevApprovalRequest
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.workflow import dev_approvals, reply_delivery
from distr.gui.web.routes import dev_approvals as routes


@pytest.fixture
def review(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'review.sqlite'),
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr('distr.core.db.get_session', factory)
    monkeypatch.setattr(routes, '_require_token', lambda request: None)
    monkeypatch.setattr(routes, '_post_card', lambda row: 'fixture-card')
    monkeypatch.setattr(routes, '_update_card', Mock())
    sender = Mock(return_value={'delivered': True, 'delivery_status': 'sent'})
    monkeypatch.setattr(reply_delivery, 'execute_approved_workflow_reply', sender)
    with factory() as db:
        chat = Chat(id=17, title='Source request', project_id=9)
        db.add(chat); db.flush()
        turn = Chat(id=19, parent_id=chat.id, title='Workflow turn')
        board = KanbanBoard(name='Review'); db.add_all([turn, board]); db.flush()
        lane = KanbanLane(board_id=board.id, name='Ready', position=0)
        db.add(lane); db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title='Original request', linked_project_id=9,
                              source_provider='whatsapp', source_thread_id='27634103646')
        workflow = AutoWorkflow(name='Work', workflow_type='manual', status='active')
        db.add_all([ticket, workflow]); db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, chat_id=chat.id, ticket_id=ticket.id,
                              status='completed', run_data=json.dumps({'project_id': 9, 'terminal_receipt': {}}))
        db.add(run); db.flush()
        details = {'ticket_id': ticket.id, 'channel': 'whatsapp', 'body': 'Exact reviewed\nclosing reply.',
                   'delivery': {'project_id': 9, 'recipient': '27634103646', 'thread_id': '27634103646'},
                   'workflow': {'run_id': run.id, 'workflow_id': workflow.id,
                                'chat_id': chat.id, 'turn_id': turn.id}}
        db.commit()
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        yield client, factory, details, sender
    engine.dispose()


CLICK = {'origin': 'http://127.0.0.1:8765', 'sec-fetch-site': 'same-origin',
         'sec-fetch-mode': 'cors', 'X-Decisions-User-Action': 'click'}


def request_review(review, details=None):
    client, _, canonical, _ = review
    details = details or canonical
    return client.post('/api/dev-approvals/request', json={
        'tool': 'reply_delivery', 'action': details['channel'],
        'args_hash': dev_approvals.args_hash(details),
        'chat_id': details['workflow']['chat_id'], 'turn_id': details['workflow']['turn_id'],
        'summary': 'Send the displayed closing reply?', 'details': details})


def decide(review, rid, action='approve', headers=CLICK):
    return review[0].post(f'/api/dev-approvals/{rid}/{action}', headers=headers, json={})


def test_managed_review_dedupes_survives_new_boot_and_sends_exact_body_once(review, monkeypatch):
    first = request_review(review).json()
    assert first['status'] == 'pending' and first['details'] == review[2]
    assert request_review(review).json()['request_id'] == first['request_id']
    monkeypatch.setattr(routes, '_BOOT_KEY', b'new simulated app boot key')
    result = decide(review, first['request_id'])
    assert result.status_code == 200 and result.json()['status'] == 'sent'
    review[3].assert_called_once_with(review[2])
    assert decide(review, first['request_id']).json()['status'] == 'sent'
    assert request_review(review).json()['status'] == 'sent'
    assert 'approval_id' not in review[0].get(f'/api/dev-approvals/{first["request_id"]}').json()
    with review[1]() as db:
        assert db.query(DevApprovalRequest).count() == 1
        data = json.loads(db.get(AutoWorkflowRun, review[2]['workflow']['run_id']).run_data)
        assert data['terminal_receipt']['reply_delivery']['delivered'] is True


@pytest.mark.parametrize('restart', [False, True])
@pytest.mark.parametrize('same_run', [True, False])
def test_refresh_reconciles_only_matching_durable_delivery_receipt(review, monkeypatch, same_run, restart):
    from distr.core.db.orchestrator import OrchestratorEvent

    client, factory, canonical, sender = review
    envelope = {**canonical['delivery'], 'channel': 'whatsapp', 'board_id': 1}
    details = {**canonical, 'delivery': envelope}
    rid = request_review(review, details).json()['request_id']
    with factory.begin() as db:
        row = db.query(DevApprovalRequest).filter_by(request_id=rid).one()
        row.status = 'sending'
        row.approval_id_hash = 'before-restart'
        row.signature = routes._sign(row, row.approval_id_hash)
        key = reply_delivery.reply_key(details['ticket_id'], 'whatsapp', envelope['recipient'], details['body'])
        workflow = details['workflow']
        if not same_run:
            workflow = {**workflow, 'run_id': workflow['run_id'] + 1}
        db.add(OrchestratorEvent(event_uid=key, source='reply_delivery', event_type=reply_delivery.REPLY_EVENT_TYPE,
            status='sent', ticket_id=details['ticket_id'], project_id=envelope['project_id'], board_id=envelope['board_id'],
            payload=json.dumps({'workflow': workflow, 'envelope': envelope, 'body': details['body'],
                                'channel': 'whatsapp', 'target': envelope['recipient'], 'detail': {'message_id': 'one'}})))
    if restart:
        monkeypatch.setattr(routes, '_BOOT_KEY', b'restarted after delivery')
    result = client.get(f'/api/dev-approvals/{rid}')
    assert result.status_code == 200
    assert result.json()['status'] == ('sent' if same_run else 'unconfirmed' if restart else 'sending')
    sender.assert_not_called()
    with factory() as db:
        run = db.get(AutoWorkflowRun, details['workflow']['run_id'])
        terminal = json.loads(run.run_data)['terminal_receipt']
        if not same_run and not restart:
            assert 'reply_delivery' not in terminal
            return
        receipt = terminal['reply_delivery']
        assert receipt['delivered'] is same_run
        if same_run:
            assert receipt['result']['message_id'] == 'one'


def test_refresh_can_finish_while_original_approval_request_is_in_flight(review):
    from distr.core.db.orchestrator import OrchestratorEvent

    client, factory, canonical, sender = review
    envelope = {**canonical['delivery'], 'channel': 'whatsapp', 'board_id': 1}
    details = {**canonical, 'delivery': envelope}
    rid = request_review(review, details).json()['request_id']
    saved, release = threading.Event(), threading.Event()

    def execute(_details):
        key = reply_delivery.reply_key(details['ticket_id'], 'whatsapp', envelope['recipient'], details['body'])
        with factory.begin() as db:
            db.add(OrchestratorEvent(event_uid=key, source='reply_delivery', event_type=reply_delivery.REPLY_EVENT_TYPE,
                status='sent', ticket_id=details['ticket_id'], project_id=envelope['project_id'], board_id=envelope['board_id'],
                payload=json.dumps({'workflow': details['workflow'], 'envelope': envelope, 'body': details['body'],
                                    'channel': 'whatsapp', 'target': envelope['recipient'], 'detail': {'message_id': 'one'}})))
        saved.set()
        assert release.wait(5)
        return {'delivered': True, 'result': {'message_id': 'one'}}

    sender.side_effect = execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(decide, review, rid)
        try:
            assert saved.wait(5)
            refreshed = client.get(f'/api/dev-approvals/{rid}')
            assert refreshed.json()['status'] == 'sent'
        finally:
            release.set()
        original = pending.result(timeout=5)
    assert original.status_code == 200
    assert original.json() == refreshed.json()
    sender.assert_called_once_with(details)


def test_approval_projection_recovers_from_a_transient_receipt_lock(review):
    from sqlite3 import OperationalError as SQLiteOperationalError
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    _, factory, details, sender = review
    rid = request_review(review).json()['request_id']
    engine = factory.kw['bind']
    failed = []

    def reject_first_projection(_connection, _cursor, statement, parameters, *_):
        if not failed and statement.lstrip().upper().startswith('UPDATE AUTO_WORKFLOW_RUNS'):
            failed.append(statement)
            raise OperationalError(statement, parameters, SQLiteOperationalError('database is locked'))

    def send(_details):
        event.listen(engine, 'before_cursor_execute', reject_first_projection)
        return {'delivered': True, 'delivery_status': 'sent'}

    sender.side_effect = send
    try:
        response = decide(review, rid)
    finally:
        event.remove(engine, 'before_cursor_execute', reject_first_projection)
    assert failed and response.status_code == 200
    assert response.json()['status'] == 'sent'
    sender.assert_called_once_with(details)
    with factory() as db:
        run = db.get(AutoWorkflowRun, details['workflow']['run_id'])
        assert json.loads(run.run_data)['terminal_receipt']['reply_delivery']['delivered'] is True


@pytest.mark.parametrize('status', ['denied', 'expired'])
def test_denied_expired_never_execute_and_remain_terminal(review, status):
    rid = request_review(review).json()['request_id']
    if status == 'denied':
        assert decide(review, rid, 'deny').json()['status'] == 'denied'
    else:
        with review[1]() as db:
            db.query(DevApprovalRequest).filter_by(request_id=rid).first().expires_at = routes._now() - timedelta(seconds=1)
            db.commit()
    assert decide(review, rid).json()['status'] == status
    assert request_review(review).json()['status'] == status
    review[3].assert_not_called()


@pytest.mark.parametrize('change', ['payload', 'run', 'chat'])
def test_changed_review_or_canonical_scope_refuses_execution(review, change):
    rid = request_review(review).json()['request_id']
    with review[1]() as db:
        row = db.query(DevApprovalRequest).filter_by(request_id=rid).first()
        if change == 'payload':
            details = json.loads(row.details_json); details['body'] = 'Unreviewed replacement'
            row.details_json = json.dumps(details)
        elif change == 'run':
            db.get(AutoWorkflowRun, review[2]['workflow']['run_id']).status = 'failed'
        else:
            row.chat_id = 999
        db.commit()
    assert decide(review, rid).status_code == 409
    review[3].assert_not_called()


def test_no_browser_click_cannot_execute(review):
    rid = request_review(review).json()['request_id']
    assert decide(review, rid, headers={}).status_code == 403
    review[3].assert_not_called()


def test_no_turn_cannot_create_an_invisible_pending_reply_review(review):
    details = {**review[2], 'workflow': {**review[2]['workflow'], 'turn_id': None}}
    assert request_review(review, details).status_code == 409
    with review[1]() as db:
        assert db.query(DevApprovalRequest).count() == 0
    review[3].assert_not_called()


def test_restart_during_managed_send_reports_unknown_without_replaying(review, monkeypatch):
    rid = request_review(review).json()['request_id']
    with review[1]() as db:
        row = db.query(DevApprovalRequest).filter_by(request_id=rid).one()
        row.status = 'sending'
        row.approval_id_hash = 'old-test-approval'
        row.signature = routes._sign(row, row.approval_id_hash)
        db.commit()
    monkeypatch.setattr(routes, '_BOOT_KEY', b'restarted after the send claim')
    status = review[0].get(f'/api/dev-approvals/{rid}').json()
    assert status['status'] == 'unconfirmed'
    assert status['details']['delivery_outcome']['unconfirmed'] is True
    assert decide(review, rid).json()['status'] == 'unconfirmed'
    review[3].assert_not_called()


@pytest.mark.parametrize('outcome,status', [
    ({'delivered': False, 'unconfirmed': True}, 'unconfirmed'),
    ({'delivered': False, 'reason': 'Project changed.'}, 'rejected'),
])
def test_failed_delivery_is_truthful_and_never_replayed(review, outcome, status):
    review[3].return_value = outcome
    rid = request_review(review).json()['request_id']
    assert decide(review, rid).json()['status'] == status
    assert decide(review, rid).json()['status'] == status
    assert request_review(review).json()['status'] == status
    assert review[3].call_count == 1


def test_two_simultaneous_clicks_claim_one_execution_off_event_loop(review):
    rid = request_review(review).json()['request_id']
    entered, release = threading.Event(), threading.Event()
    names = []
    def execute(details):
        names.append(threading.current_thread().name); entered.set()
        assert release.wait(5)
        return {'delivered': True}
    review[3].side_effect = execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(decide, review, rid)
        assert entered.wait(5)
        assert decide(review, rid).json()['status'] == 'sending'
        release.set()
        assert first.result(timeout=5).json()['status'] == 'sent'
    assert review[3].call_count == 1 and not names[0].startswith('asyncio-portal')


def test_full_review_is_not_truncated_at_generic_details_limit(review):
    details = {**review[2], 'body': 'Reviewed body. ' * 2000}
    result = request_review(review, details)
    assert result.status_code == 200 and result.json()['details']['body'] == details['body']


def test_explicit_context_ignores_foreign_process_env_and_managed_gate_never_polls(monkeypatch):
    monkeypatch.setenv('DECISIONS_CHAT_ID', '999'); monkeypatch.setenv('DECISIONS_TURN_ID', '888')
    context = {'chat_id': 17, 'turn_id': 18}
    calls = []
    def transport(method, path, body):
        calls.append((method, path, body))
        return 200, {'request_id': 'req-fixture', 'status': 'pending', 'expires_at': 'later'}
    monkeypatch.setattr(dev_approvals, 'transport', transport)
    outcome = dev_approvals.gate('reply_delivery', 'email', {}, None, 'Review',
        details={'workflow': context}, context=context, inline_wait_s=0)
    assert outcome['needs_approval'] is True
    assert len(calls) == 1 and calls[0][2]['chat_id'] == 17 and calls[0][2]['turn_id'] == 18


def test_dispatcher_retains_run_review_context_and_returns_pending_evidence(review, monkeypatch):
    from distr.core.workflow import dispatcher
    monkeypatch.setattr(dispatcher, 'get_session', review[1])
    delivery = Mock(return_value={'delivered': False, 'pending_approval': True, 'detail': {'request_id': 'req-durable'}})
    monkeypatch.setattr(reply_delivery, 'deliver_ticket_reply', delivery)
    scope = review[2]['workflow']
    first = dispatcher._deliver_run_reply(review[2]['ticket_id'], 'reply_text: Reviewed.', **scope)
    dispatcher._deliver_run_reply(review[2]['ticket_id'], 'reply_text: Reviewed.', **{**scope, 'turn_id': None})
    assert first['approval_request_id'] == 'req-durable'
    assert [call.kwargs['approval_context'] for call in delivery.call_args_list] == [scope, scope]


@pytest.mark.parametrize('first_writer', ['receipt', 'approval'])
def test_terminal_receipt_and_approval_preserve_both_writes(review, monkeypatch, first_writer):
    from sqlalchemy import event
    from distr.core.workflow import dispatcher

    _, factory, details, sender = review
    rid = request_review(review).json()['request_id']
    run = details['workflow']
    pending = {'approval_request_id': rid, 'pending_approval': True}
    sender_entered = threading.Event()
    release_sender = threading.Event()
    lock_held = threading.Event()
    contender_entered = threading.Event()
    release_lock = threading.Event()

    def send(_details):
        sender_entered.set()
        assert release_sender.wait(5)
        return {'delivered': True, 'delivery_status': 'sent'}

    sender.side_effect = send
    monkeypatch.setattr(dispatcher, 'get_session', factory)
    for name in ('_cleanup_run', '_finalize_development_chat_turn',
                 '_maybe_auto_start_next_queued_ticket', 'append_ticket_audit_entry'):
        monkeypatch.setattr(dispatcher, name, Mock())
    monkeypatch.setattr(dispatcher, '_deliver_run_reply', Mock(return_value=pending))
    monkeypatch.setattr('distr.core.orchestrator.list_validation_records', Mock(return_value=[]))
    monkeypatch.setattr('distr.core.kanban.lifecycle.move_ticket_to_delivery_lane', Mock())
    monkeypatch.setattr('distr.core.kanban.ticket_workflow_engagement.record_ticket_workflow_elapsed', Mock())
    monkeypatch.setattr('distr.core.kanban.whatsapp_work_lifecycle.notify_post_completion', Mock())
    monkeypatch.setattr('distr.core.kanban.jira_work_lifecycle.prepare_completed_jira_review', Mock(return_value=None))
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', Mock(return_value={}))
    monkeypatch.setattr('distr.core.workflow_engine.agent_bridge.WorkflowAgentBridge', Mock())
    monkeypatch.setattr('distr.core.workflow.development_harness._start_next_queued_instruction', Mock())

    def writer():
        return 'receipt' if threading.current_thread().name.startswith('receipt-writer') else 'approval'

    def before_begin(conn, cursor, statement, parameters, context, executemany):
        if statement == 'BEGIN IMMEDIATE' and sender_entered.is_set() and writer() != first_writer:
            contender_entered.set()

    def after_begin(conn, cursor, statement, parameters, context, executemany):
        if statement == 'BEGIN IMMEDIATE' and sender_entered.is_set() and writer() == first_writer:
            lock_held.set()
            assert release_lock.wait(5)

    engine = factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', before_begin)
    event.listen(engine, 'after_cursor_execute', after_begin)
    try:
        with ThreadPoolExecutor(max_workers=1) as approval_pool, ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='receipt-writer'
        ) as receipt_pool:
            approval = approval_pool.submit(decide, review, rid)
            try:
                assert sender_entered.wait(5)
                if first_writer == 'approval':
                    release_sender.set()
                    assert lock_held.wait(5)
                receipt = receipt_pool.submit(
                    dispatcher._finalize_terminal_run, run['run_id'], run['workflow_id'], 'completed'
                )
                if first_writer == 'receipt':
                    assert lock_held.wait(5)
                    release_sender.set()
                assert contender_entered.wait(5)
            finally:
                release_sender.set()
                release_lock.set()
            assert approval.result(timeout=5).json()['status'] == 'sent'
            receipt.result(timeout=5)
    finally:
        event.remove(engine, 'before_cursor_execute', before_begin)
        event.remove(engine, 'after_cursor_execute', after_begin)

    sender.assert_called_once_with(details)
    with factory() as db:
        data = json.loads(db.get(AutoWorkflowRun, run['run_id']).run_data)
        assert data['terminal_receipt']['status'] == 'completed'
        assert data['terminal_receipt']['reply_delivery'] == data['reply_delivery']
        assert data['reply_delivery']['approval_request_id'] == rid
        assert data['reply_delivery']['delivered'] is True
