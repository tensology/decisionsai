"""Exercise the workflow closing-reply public boundary without real transports."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.kanban import KanbanTicket
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.workflow import reply_delivery as delivery, dev_approvals
from tests.core.test_reply_delivery import _ticket


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "replies.sqlite"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    monkeypatch.setattr('distr.core.db.get_session', factory)
    yield factory
    engine.dispose()


def test_concurrent_delivery_claims_before_transport(store, monkeypatch):
    ticket = _ticket(store)
    entered, release = Event(), Event()
    calls = []
    def send(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5)
        return {'success': True, 'message_id': 'provider-one'}
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(delivery.deliver_ticket_reply, ticket, 'Fixed and checked.')
        try:
            assert entered.wait(5)
            second = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
            assert second.get('duplicate') is True
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(5)['delivered'] is True


def test_packet_reply_preserves_paragraphs_links_and_final_deployment_caveat():
    body = 'Hi,\n\nThe billing fix is checked.\nhttps://example.com/result\n\nIt has not been deployed.'
    packet = f'status: done\n\nreply_text: {body}\n\nnotes: Internal audit only.'
    assert delivery.extract_reply_text(packet) == body


def test_empty_reply_does_not_send_internal_packet_fields():
    assert delivery.extract_reply_text('reply_text:\n\nnotes: Internal-only customer context.') == ''


def test_fenced_reply_preserves_labels_and_excludes_packet_notes():
    body = 'Hi,\n\nStatus: Ready for review.\n\nNext steps: Check the preview.\n\nThanks'
    packet = f'reply_text: ```text\n{body}\n```\nnotes: Internal audit only.'
    assert delivery.extract_reply_text(packet) == body


def test_unfinished_fenced_reply_is_not_deliverable():
    packet = 'reply_text: An older draft.\nreply_text: ```text\nHi,\n\nnotes: Internal audit only.'
    assert delivery.extract_reply_text(packet) == ''


def test_prepared_mail_keeps_existing_comma_greeting_once():
    from distr.core.workflow.reply_channel import shape_reply
    assert shape_reply('Hi,\n\nThe fix is checked.\n\nThanks', 'email') == 'Hi,\n\nThe fix is checked.\n\nThanks'


def test_pending_approval_cannot_authorize_changed_recipient(store, monkeypatch):
    ticket = _ticket(store, title='Add a schema migration', source_provider='gmail',
                     source_thread_id='thread-one', source_contact='client-a@example.com')
    calls, requests = [], []
    monkeypatch.setitem(delivery.ADAPTERS, 'email', lambda **kwargs: calls.append(kwargs) or {'success': True})
    monkeypatch.setattr(dev_approvals, 'INLINE_WAIT_S', 0)
    def transport(method, path, body):
        if path.endswith('/consume'):
            return 200, {'ok': bool(requests and body['args_hash'] == requests[0]['args_hash'])}
        if path.endswith('/request'):
            requests.append(body)
            return 200, {'request_id': f'request-{len(requests)}'}
        return 200, {'status': 'pending'}
    monkeypatch.setattr(dev_approvals, 'transport', transport)
    assert delivery.deliver_ticket_reply(ticket, 'Migration checked.')['pending_approval']
    with store() as db:
        db.get(KanbanTicket, ticket).source_contact = 'client-b@example.com'
        db.commit()
    second = delivery.deliver_ticket_reply(ticket, 'Migration checked.', approval_id='approval-for-a')
    assert second.get('pending_approval') is True
    assert calls == []
    assert requests[0]['args_hash'] != requests[-1]['args_hash']


def test_scope_change_while_approval_waits_stops_before_transport(store, monkeypatch):
    ticket = _ticket(store, title='Add a schema migration')
    calls = []
    def gate(*args, **kwargs):
        with store() as db:
            db.get(KanbanTicket, ticket).source_thread_id = 'different-conversation'
            db.commit()
        return None
    monkeypatch.setattr(dev_approvals, 'gate', gate)
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', lambda **kwargs: calls.append(kwargs) or {'success': True})
    result = delivery.deliver_ticket_reply(ticket, 'Migration checked.')
    assert result['delivered'] is False and result.get('scope_changed') is True
    assert calls == []


def test_dry_run_cannot_be_recorded_as_delivered(store, monkeypatch):
    ticket = _ticket(store)
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', lambda **kwargs: {'success': True, 'dry_run': True})
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
    assert result['delivered'] is False
    with store() as db:
        assert db.query(OrchestratorEvent).one().status != 'sent'


def test_delivery_ledger_must_be_durable_before_sending(store, monkeypatch):
    ticket = _ticket(store)
    calls = []
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', lambda **kwargs: calls.append(kwargs) or {'success': True})
    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('INSERT INTO ORCHESTRATOR_EVENTS'):
            raise RuntimeError('Isolated ledger unavailable')
    event.listen(store.kw['bind'], 'before_cursor_execute', fail_insert)
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
    assert result['delivered'] is False
    assert calls == []


def test_post_timeout_is_durable_and_not_retried(store, monkeypatch):
    ticket = _ticket(store)
    calls = []
    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError('Provider response lost')
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', timeout)
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
    assert result.get('unconfirmed') is True
    assert delivery.deliver_ticket_reply(ticket, 'Fixed and checked.').get('duplicate') is True
    assert len(calls) == 1
    with store() as db:
        receipt = db.query(OrchestratorEvent).one()
        assert receipt.status == 'unconfirmed'
        assert json.loads(receipt.payload)['body'] == 'Fixed and checked.'


def test_definite_no_send_can_claim_a_fresh_review_after_project_change(store, monkeypatch):
    ticket_id = _ticket(store, linked_project_id=9)
    results = iter([{'success': True, 'dry_run': True}, {'success': True, 'message_id': 'new-send'}])
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', lambda **kwargs: next(results))
    body = 'Fixed and checked.'
    assert not delivery.deliver_ticket_reply(ticket_id, body, force=True)['delivered']
    with store() as db:
        db.get(KanbanTicket, ticket_id).linked_project_id = 10
        db.commit()
    assert delivery.deliver_ticket_reply(ticket_id, body, force=True)['delivered']
    duplicate = delivery.deliver_ticket_reply(ticket_id, body, force=True)
    assert duplicate['duplicate'] and duplicate['receipt_matches'] and duplicate['delivery_status'] == 'sent'
    with store() as db:
        receipt = db.query(OrchestratorEvent).one()
        assert receipt.project_id == 10 and receipt.status == 'sent'
        assert json.loads(receipt.payload)['previous_unsent'][0]['status'] == 'not_sent'


@pytest.mark.parametrize('decision', ['denied', 'expired'])
def test_denied_or_expired_approval_is_not_presented_as_pending(store, monkeypatch, decision):
    ticket = _ticket(store, title='Add schema migration')
    monkeypatch.setattr(dev_approvals, 'gate', lambda *args, **kwargs: {
        'needs_approval': False, 'approval_status': decision, 'summary': f'Request {decision}.'})
    result = delivery.deliver_ticket_reply(ticket, 'Migration checked.')
    assert not result['delivered'] and result['pending_approval'] is False
    assert result['approval_status'] == decision


def test_finalization_failure_retains_claim_and_prevents_second_send(store, monkeypatch):
    ticket = _ticket(store)
    calls = []
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', lambda **kwargs: calls.append(kwargs) or {'success': True})
    def fail_update(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('UPDATE ORCHESTRATOR_EVENTS'):
            raise RuntimeError('Outcome write interrupted')
    event.listen(store.kw['bind'], 'before_cursor_execute', fail_update)
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
    assert not result['delivered'] and result['unconfirmed']
    assert delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')['duplicate']
    assert len(calls) == 1
    with store() as db:
        assert db.query(OrchestratorEvent).one().status == 'sending'


@pytest.mark.parametrize('expected', ['sent', 'not_sent', 'unconfirmed'])
def test_workflow_reply_retries_a_transient_receipt_lock_without_resending(store, monkeypatch, expected):
    from sqlite3 import OperationalError as SQLiteOperationalError
    from sqlalchemy.exc import OperationalError

    ticket = _ticket(store)
    calls = []
    def send(**kwargs):
        calls.append(kwargs)
        if expected == 'unconfirmed':
            raise TimeoutError('Provider did not respond.')
        return {'success': True, 'dry_run': True} if expected == 'not_sent' else {'success': True, 'message_id': 'one'}

    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', send)
    failed = []
    def reject_first_outcome(_connection, _cursor, statement, parameters, *_):
        if not failed and statement.lstrip().upper().startswith('UPDATE ORCHESTRATOR_EVENTS'):
            failed.append(statement)
            raise OperationalError(statement, parameters, SQLiteOperationalError('database is locked'))

    event.listen(store.kw['bind'], 'before_cursor_execute', reject_first_outcome)
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
    assert bool(failed) and len(calls) == 1
    assert result['delivered'] == (expected == 'sent')
    assert result['unconfirmed'] == (expected == 'unconfirmed')
    with store() as db:
        assert db.query(OrchestratorEvent).one().status == expected


def test_definite_dry_run_can_be_retried_but_never_counts_as_sent(store, monkeypatch):
    ticket = _ticket(store)
    responses = iter([{'success': True, 'dry_run': True}, {'success': True, 'message_id': 1}])
    calls = []
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', lambda **kwargs: calls.append(kwargs) or next(responses))
    assert not delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')['delivered']
    assert delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')['delivered']
    assert delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')['duplicate']
    assert len(calls) == 2
    with store() as db:
        saved = json.loads(db.query(OrchestratorEvent).one().payload)
        assert saved['previous_unsent'][0]['status'] == 'not_sent'
