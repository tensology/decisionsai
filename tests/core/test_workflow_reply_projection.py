"""Exact reviewed data is projected without weakening general activity redaction."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from distr.core import chat_turns
from distr.core.db import Chat, ChatTurnEvent, DevApprovalRequest
from tests.core.test_workflow_reply_claim import store


def make_review(store, monkeypatch, *, count=1):
    monkeypatch.setattr(chat_turns, 'get_session', store)
    body = 'The exact reviewed message.\n' * 100 + 'FINAL REQUIREMENT MUST BE VISIBLE'
    with store() as db:
        root = Chat(title='Owner', params='{}')
        db.add(root); db.flush()
        turn = Chat(parent_id=root.id, input='Request')
        db.add(turn); db.flush()
        ids = root.id, turn.id
        db.commit()
    for number in range(count):
        rid = f'review-{number}'
        details = {'body': body, 'workflow': {'run_id': 17, 'chat_id': ids[0], 'turn_id': ids[1]},
                   'delivery': {'resolved_target': {'to': ['actual@example.com'], 'cc': ['reviewer@example.com']}}}
        with store() as db:
            row = DevApprovalRequest(request_id=rid, chat_id=ids[0], turn_id=ids[1], tool='reply_delivery',
                action='email', args_hash='scope-hash', summary='Review this exact message',
                status='pending', details_json=json.dumps(details), expires_at=datetime.now() + timedelta(hours=1))
            db.add(row); db.commit()
        view = {'request_id': rid, 'tool': 'reply_delivery', 'status': 'pending', 'details': details}
        payload = chat_turns.create_event(ids[0], 'tool_started', turn_id=ids[1], status='pending',
                                         metadata={'approval_request': view, 'ordinary': {'text': body}}, broadcast=False)
        assert payload['metadata']['approval_request']['details']['body'] == body
    return ids, body


def test_saved_log_remains_bounded_but_authoritative_review_is_full(store, monkeypatch):
    ids, body = make_review(store, monkeypatch)
    payload = chat_turns.get_turns(ids[0])['turns'][0]['events'][0]
    assert payload['metadata']['approval_request']['details']['body'] == body
    assert payload['metadata']['approval_request']['details']['delivery']['resolved_target']['to'] == ['actual@example.com']
    assert len(payload['metadata']['ordinary']['text']) <= 1000
    with store() as db:
        raw = json.loads(db.query(ChatTurnEvent).one().metadata_json)
        assert '[truncated' in raw['approval_request']['details']['body']
        assert raw['approval_request']['details']['delivery']['resolved_target']['to'] == ['[truncated]']


@pytest.mark.parametrize('missing', [True, False])
def test_missing_or_other_chat_review_is_unavailable_not_clickable(store, monkeypatch, missing):
    ids, _ = make_review(store, monkeypatch)
    with store() as db:
        row = db.query(DevApprovalRequest).one()
        if missing: db.delete(row)
        else:
            other = Chat(title='Private unrelated thread')
            db.add(other); db.flush()
            row.chat_id = other.id
            row.details_json = json.dumps({'body': 'Other thread secret', 'workflow': {'run_id': 999}})
        db.commit()
    payload = chat_turns.get_turns(ids[0])['turns'][0]['events'][0]
    assert payload['metadata']['approval_request']['review_unavailable'] is True
    assert 'Other thread secret' not in json.dumps(payload)


def test_many_review_cards_use_one_batched_lookup_and_read_only_projection(store, monkeypatch):
    ids, body = make_review(store, monkeypatch, count=30)
    with store() as db:
        before = [(row.id, row.metadata_json) for row in db.query(ChatTurnEvent).all()]
    statements = []
    listener = lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement)
    event.listen(store.kw['bind'], 'before_cursor_execute', listener)
    try:
        projected = chat_turns.get_turns(ids[0])['turns'][0]['events']
    finally:
        event.remove(store.kw['bind'], 'before_cursor_execute', listener)
    assert len(projected) == 30
    assert all(row['metadata']['approval_request']['details']['body'] == body for row in projected)
    assert sum('FROM dev_approval_requests' in statement for statement in statements) == 1
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    with store() as db:
        assert [(row.id, row.metadata_json) for row in db.query(ChatTurnEvent).all()] == before


def test_thread_refresh_shows_matching_confirmed_receipt_without_writing(store, monkeypatch):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.dev_approvals import args_hash
    from distr.core.workflow.reply_delivery import REPLY_EVENT_TYPE, reply_key

    ids, body = make_review(store, monkeypatch)
    with store.begin() as db:
        row = db.query(DevApprovalRequest).one()
        envelope = {'channel': 'whatsapp', 'recipient': '12025550142', 'project_id': 9, 'board_id': 1}
        workflow = {'run_id': 17, 'workflow_id': 18, 'chat_id': ids[0], 'turn_id': ids[1]}
        details = {'ticket_id': 12, 'channel': 'whatsapp', 'body': body, 'delivery': envelope, 'workflow': workflow}
        row.action = 'whatsapp'
        row.args_hash = args_hash(details)
        row.details_json = json.dumps(details)
        row.status = 'sending'
        db.add(OrchestratorEvent(event_uid=reply_key(12, 'whatsapp', envelope['recipient'], body),
            source='reply_delivery', event_type=REPLY_EVENT_TYPE, status='sent', ticket_id=12,
            project_id=9, board_id=1, payload=json.dumps({'workflow': workflow, 'envelope': envelope,
                'body': body, 'detail': {'message_id': 'confirmed'}})))

    view = chat_turns.get_turns(ids[0])['turns'][0]['events'][0]['metadata']['approval_request']
    assert view['status'] == 'sent'
    assert view['details']['delivery_outcome']['result'] == {'message_id': 'confirmed'}
    with store() as db:
        assert db.query(DevApprovalRequest).one().status == 'sending'
