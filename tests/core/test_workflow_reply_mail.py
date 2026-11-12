"""Real workflow mail preparation and transports, with provider IO stubbed."""
import base64
import json
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock

import pytest

from distr.core.db import Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.orchestrator import OrchestratorEvent
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.workflow import reply_delivery as delivery, dev_approvals
from tests.core.test_reply_delivery import _ticket
from tests.core.test_workflow_reply_claim import store

@pytest.fixture(autouse=True)
def _ask_first_off(monkeypatch):
    """These tests cover the transport/ledger with 'Ask before messaging clients' switched off.
    Ask-first holding is covered in tests/initiative/test_initiative_fixes_20261112.py."""
    monkeypatch.setattr('distr.core.initiative.outcome_flow.ask_before_client_messages', lambda settings=None: False)


def gmail(monkeypatch, *, post_error=None):
    from distr.core.agent.services.integrations import google_workspace
    state = {'account': 'paul@example.com', 'alias_is_ours': True, 'post_error': post_error, 'submitted': []}
    def request(method, url, **kwargs):
        if method == 'GET':
            return {'sendAs': [{'sendAsEmail': state['account'], 'isPrimary': True},
                               {'sendAsEmail': 'orders@example.com', 'treatAsAlias': state['alias_is_ours'],
                                'verificationStatus': 'accepted'}]}
        if kwargs.get('before_send'):
            kwargs['before_send']()
        state['submitted'].append(kwargs['json'])
        if state['post_error']:
            raise state['post_error']
        return {'id': 'confirmed-provider-id', 'threadId': 'original-thread'}
    connector = Mock()
    connector._make_request.side_effect = request
    connector.get_email.return_value = {'id': 'original-message', 'threadId': 'original-thread', 'labels': ['INBOX'],
        'from': 'Client <client@example.com>', 'reply_to': 'Support <support@example.com>',
        'to': 'paul@example.com, orders@example.com', 'cc': 'Reviewer <reviewer@example.com>',
        'subject': 'Original billing request',
        'message_id_header': '<original@example.com>', 'references': '<older@example.com>'}
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)
    return connector, state


def mail_ticket(store, **kwargs):
    return _ticket(store, source_provider='gmail', source_thread_id='original-thread',
                   source_external_id='original-message', source_contact='client@example.com', **kwargs)


def posts(connector):
    return [call for call in connector._make_request.call_args_list if call.args[0] == 'POST']


def test_builtin_gmail_preserves_original_thread_reply_all_headers_and_account(store, monkeypatch):
    connector, _ = gmail(monkeypatch)
    ticket = mail_ticket(store)
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
    assert result['delivered'] is True
    assert len(posts(connector)) == 1
    sent = posts(connector)[0].kwargs['json']
    assert sent['threadId'] == 'original-thread'
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(sent['raw']))
    assert str(message['From']) == 'paul@example.com'
    assert str(message['To']) == 'support@example.com'
    assert str(message['Cc']) == 'reviewer@example.com'
    assert message['Subject'] == 'Re: Original billing request'
    assert message['In-Reply-To'] == '<original@example.com>'
    assert message['References'] == '<older@example.com> <original@example.com>'
    assert 'Fixed and checked.' in message.get_content()
    connector.send_email.assert_not_called()
    with store() as db:
        row = db.query(OrchestratorEvent).one()
        payload = json.loads(row.payload)
        assert row.status == 'sent'
        assert payload['envelope']['resolved_target']['from'] == 'paul@example.com'
        assert payload['detail']['provider_receipt']['id'] == 'confirmed-provider-id'
    # Contact display corrections cannot duplicate the same exact source reply.
    with store() as db:
        db.get(KanbanTicket, ticket).source_contact = 'corrected-label@example.com'
        db.commit()
    assert delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')['duplicate']
    assert len(posts(connector)) == 1


@pytest.mark.parametrize('mutation', ['missing-message', 'other-thread', 'sent-message'])
def test_unverified_original_email_never_posts(store, monkeypatch, mutation):
    connector, _ = gmail(monkeypatch)
    ticket = mail_ticket(store)
    if mutation == 'missing-message':
        with store() as db:
            db.get(KanbanTicket, ticket).source_external_id = None
            db.commit()
    elif mutation == 'other-thread':
        connector.get_email.return_value['threadId'] = 'another-thread'
    else:
        connector.get_email.return_value['labels'] = ['SENT']
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.', force=True)
    assert result['preflight_failed'] and not result['delivered']
    assert posts(connector) == []


def test_account_switch_after_review_stops_before_post_and_keeps_exact_body(store, monkeypatch):
    connector, state = gmail(monkeypatch)
    ticket = mail_ticket(store, title='Add schema migration')
    def gate(*args, **kwargs):
        assert kwargs['details']['delivery']['resolved_target']['from'] == 'paul@example.com'
        assert kwargs['details']['delivery']['resolved_target']['cc'] == ['reviewer@example.com']
        state['account'] = 'other@example.com'
        return None
    monkeypatch.setattr(dev_approvals, 'gate', gate)
    result = delivery.deliver_ticket_reply(ticket, 'Migration checked.')
    assert result['preflight_failed'] and not result['delivered']
    assert posts(connector) == []
    with store() as db:
        row = db.query(OrchestratorEvent).one()
        assert row.status == 'not_sent'
        assert json.loads(row.payload)['body'] == 'Hi,\n\nMigration checked.\n\nThanks'


def test_builtin_gmail_timeout_never_replays(store, monkeypatch):
    connector, _ = gmail(monkeypatch, post_error=TimeoutError('Lost reply ACK'))
    ticket = mail_ticket(store)
    assert delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')['unconfirmed']
    assert delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')['duplicate']
    assert len(posts(connector)) == 1


@pytest.mark.parametrize('move_ticket', [False, True])
def test_builtin_mailshot_uses_verified_primary_original_reply_and_ledger_key(store, monkeypatch, move_ticket):
    from distr.core import tensology_client
    client = Mock()
    ticket = _ticket(store, source_provider='mailshot', source_thread_id='original-thread',
                     source_external_id='original-message', source_contact='client@example.com')
    def get(path):
        if path == 'mail/senders':
            return [{'email': 'paul@example.com', 'primary': True, 'can_send': True}]
        if move_ticket:
            with store.begin() as db:
                if db.query(OrchestratorEvent).filter_by(status='sending').count():
                    db.get(KanbanTicket, ticket).source_thread_id = 'another-conversation'
        return {'email': {'id': 'original-message', 'thread_id': 'original-thread', 'type': 'received',
            'from': 'Client <client@example.com>', 'to': 'paul@example.com', 'cc': 'reviewer@example.com',
            'subject': 'Original request', 'messageId': '<original@example.com>'}}
    client.get.side_effect = get
    client.post.return_value = {'success': True, 'id': 'mailshot-confirmed'}
    monkeypatch.setattr(tensology_client, 'configured_tensology_client', lambda **kwargs: client)
    result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.')
    if move_ticket:
        assert result['preflight_failed'] and not result['unconfirmed']
        client.post.assert_not_called()
        with store() as db:
            assert db.query(OrchestratorEvent).one().status == 'not_sent'
        return
    assert result['delivered']
    call = client.post.call_args
    assert call.args[0] == 'mail/send'
    assert call.args[1]['to'] == ['client@example.com'] and call.args[1]['cc'] == ['reviewer@example.com']
    assert call.args[1]['subject'] == 'Re: Original request'
    assert call.args[1]['from'] == 'paul@example.com'
    assert call.args[1]['inReplyTo'] == '<original@example.com>'
    assert call.kwargs['approved'] is True and call.kwargs['idempotency_key'].startswith('ticket-reply:')


def reviewed_workflow(store, ticket, body):
    with store() as db:
        root = Chat(title='Completed workflow', project_id=None, params='{}')
        db.add(root)
        workflow = AutoWorkflow(name='Reply fixture')
        db.add(workflow); db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, chat_id=root.id, ticket_id=ticket, status='completed')
        db.add(run); db.flush()
        row = db.get(KanbanTicket, ticket)
        target = delivery.resolve_reply_target(row)
        envelope = delivery._delivery_envelope(db, row, target, '')
        details = {'ticket_id': ticket, 'channel': target.channel, 'body': body, 'delivery': envelope,
                   'workflow': {'run_id': run.id, 'workflow_id': workflow.id, 'chat_id': root.id, 'turn_id': root.id}}
        db.commit()
    return details


def test_approved_helper_sends_exact_reviewed_body_without_shape_or_second_send(store, monkeypatch):
    ticket = _ticket(store)
    details = reviewed_workflow(store, ticket, 'My exact reviewed wording.\nKeep both lines.')
    transport = Mock(return_value={'success': True})
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', transport)
    assert delivery.execute_approved_workflow_reply(details)['delivered']
    assert delivery.execute_approved_workflow_reply(details)['duplicate']
    transport.assert_called_once_with(target='12025550142', body=details['body'], subject='')


def test_approved_workflow_requires_review_again_when_own_alias_becomes_external(store, monkeypatch):
    connector, state = gmail(monkeypatch)
    ticket = mail_ticket(store)
    details = reviewed_workflow(store, ticket, 'Exact reviewed wording.')
    with store() as db:
        row = db.get(KanbanTicket, ticket)
        target = delivery.resolve_reply_target(row)
        prepared = delivery._prepare_mail_target(target, 'original-message', delivery.ADAPTERS['email'], '')
        details['delivery'] = delivery._delivery_envelope(db, row, target, prepared['subject'], prepared)
    assert prepared['to'] == ['support@example.com']

    state['alias_is_ours'] = False
    result = delivery.execute_approved_workflow_reply(details)
    assert result['scope_changed'] and not result['delivered']
    assert posts(connector) == []


@pytest.mark.parametrize('mutation', ['run-status', 'run-ticket', 'root-project', 'root-turn', 'destination', 'board', 'body-channel'])
def test_approved_helper_refuses_changed_canonical_ownership(store, monkeypatch, mutation):
    ticket = _ticket(store)
    details = reviewed_workflow(store, ticket, 'Exact review.')
    with store() as db:
        if mutation == 'run-status': db.get(AutoWorkflowRun, details['workflow']['run_id']).status = 'cancelled'
        elif mutation == 'run-ticket': db.get(AutoWorkflowRun, details['workflow']['run_id']).ticket_id = ticket + 999
        elif mutation == 'root-project': db.get(Chat, details['workflow']['chat_id']).project_id = 999
        elif mutation == 'root-turn': details['workflow']['turn_id'] = 999999
        elif mutation == 'destination': db.get(KanbanTicket, ticket).source_thread_id = 'other-conversation'
        elif mutation == 'board':
            other = KanbanBoard(name='Another board'); db.add(other); db.flush()
            lane = KanbanLane(board_id=other.id, name='Backlog', position=0); db.add(lane); db.flush()
            db.get(KanbanTicket, ticket).lane_id = lane.id
        else: details['channel'] = 'email'
        db.commit()
    transport = Mock()
    monkeypatch.setitem(delivery.ADAPTERS, 'whatsapp', transport)
    assert delivery.execute_approved_workflow_reply(details)['scope_changed']
    transport.assert_not_called()


@pytest.mark.parametrize('managed', [False, True])
def test_rebind_during_email_lookup_cannot_cross_atomic_delivery_claim(store, monkeypatch, managed):
    connector, _ = gmail(monkeypatch)
    ticket = mail_ticket(store)
    details = reviewed_workflow(store, ticket, 'Exact reviewed message.')
    if managed:
        with store() as db:
            row = db.get(KanbanTicket, ticket)
            target = delivery.resolve_reply_target(row)
        prepared = delivery._prepare_mail_target(target, 'original-message', delivery.ADAPTERS['email'], '')
        with store() as db:
            details['delivery'] = delivery._delivery_envelope(db, db.get(KanbanTicket, ticket), target, prepared['subject'], prepared)
    original = connector.get_email.return_value
    def rebind(*args):
        with store() as db:
            db.get(Chat, details['workflow']['chat_id']).project_id = 10
            db.commit()
        return original
    connector.get_email.side_effect = rebind
    if managed:
        result = delivery.execute_approved_workflow_reply(details)
    else:
        result = delivery.deliver_ticket_reply(ticket, 'Fixed and checked.', approval_context=details['workflow'])
    assert result['scope_changed'] and not result['delivered']
    assert posts(connector) == []
    with store() as db:
        assert db.query(OrchestratorEvent).count() == 0


def test_approved_gmail_scope_change_during_final_original_lookup_stops_submission(store, monkeypatch):
    connector, state = gmail(monkeypatch)
    ticket = mail_ticket(store)
    details = reviewed_workflow(store, ticket, 'Exact reviewed message.')
    with store() as db:
        row = db.get(KanbanTicket, ticket)
        target = delivery.resolve_reply_target(row)
        prepared = delivery._prepare_mail_target(target, 'original-message', delivery.ADAPTERS['email'], '')
        details['delivery'] = delivery._delivery_envelope(db, row, target, prepared['subject'], prepared)

    def change_source_after_claim(*args):
        with store.begin() as db:
            if db.query(OrchestratorEvent).filter_by(status='sending').count():
                db.get(KanbanTicket, ticket).source_external_id = 'replacement-message'
        return connector.get_email.return_value

    connector.get_email.side_effect = change_source_after_claim
    outcome = delivery.execute_approved_workflow_reply(details)
    assert outcome.get('preflight_failed') and not outcome['delivered']
    assert not outcome['unconfirmed'] and state['submitted'] == []
    with store() as db:
        row = db.query(OrchestratorEvent).one()
        assert row.status == 'not_sent'
        assert json.loads(row.payload)['body'] == details['body']


def test_approved_whatsapp_scope_change_during_relay_auth_stops_submission(store, monkeypatch):
    from distr.core.integrations.whatsapp import relay_client

    ticket = _ticket(store)
    details = reviewed_workflow(store, ticket, 'Exact reviewed message.')
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value={'success': True})))
    monkeypatch.setattr(relay_client.requests, 'post', post)
    monkeypatch.setattr(relay_client, 'whatsapp_send_dry_run_enabled', lambda: False)

    def move_thread():
        with store.begin() as db:
            assert db.query(OrchestratorEvent).filter_by(status='sending').count() == 1
            db.get(Chat, details['workflow']['chat_id']).project_id = 10
        return {}

    monkeypatch.setattr(relay_client, 'relay_request_headers', move_thread)
    outcome = delivery.execute_approved_workflow_reply(details)
    assert outcome.get('preflight_failed') and not outcome['delivered']
    assert not outcome['unconfirmed']
    post.assert_not_called()
    with store.begin() as db:
        row = db.query(OrchestratorEvent).one()
        assert row.status == 'not_sent'
        assert json.loads(row.payload)['body'] == details['body']
        db.get(Chat, details['workflow']['chat_id']).project_id = None
    monkeypatch.setattr(relay_client, 'relay_request_headers', lambda: {})
    assert delivery.execute_approved_workflow_reply(details)['delivered']
    assert delivery.execute_approved_workflow_reply(details)['duplicate']
    post.assert_called_once()


@pytest.mark.parametrize('change', [None, 'issue', 'site', 'missing-site', 'source-key'])
def test_approved_jira_uses_exact_issue_and_rechecks_scope_after_account_lookup(store, monkeypatch, change):
    from distr.core.kanban import jira_intake

    source_key = 'ABC-99' if change == 'source-key' else 'ABC-12'
    source_url = None if change == 'missing-site' else f'https://example.atlassian.net/browse/{source_key}'
    ticket = _ticket(store, source_provider='jira', source_thread_id=None, source_external_id='ABC-12',
                     source_url=source_url)
    details = reviewed_workflow(store, ticket, 'Exact Jira comment.')
    post = Mock(return_value=Mock(status_code=201))
    monkeypatch.setattr('requests.post', post)

    def account():
        if change == 'issue':
            with store.begin() as db:
                assert db.query(OrchestratorEvent).filter_by(status='sending').count() == 1
                db.get(KanbanTicket, ticket).source_external_id = 'ABC-99'
        domain = 'other.atlassian.net' if change == 'site' else 'example.atlassian.net'
        return {'email': 'team@example.test', 'api_token': 'fixture', 'domain': domain}

    monkeypatch.setattr(jira_intake, 'load_jira_account', account)
    outcome = delivery.execute_approved_workflow_reply(details)
    if change == 'missing-site':
        assert outcome['scope_changed'] and 'no saved Jira issue URL' in outcome['reason']
        post.assert_not_called()
    elif change:
        assert outcome.get('preflight_failed') and not outcome['unconfirmed']
        expected_reason = {'site': 'does not match the original issue', 'source-key': 'does not match the saved issue URL',
                           'issue': 'destination changed'}[change]
        assert expected_reason in outcome['reason']
        post.assert_not_called()
    else:
        assert outcome['delivered']
        assert delivery.execute_approved_workflow_reply(details)['duplicate']
        post.assert_called_once()
        assert post.call_args.args[0].endswith('/ABC-12/comment')
        assert post.call_args.kwargs['json']['body']['content'][0]['content'][0]['text'] == details['body']


@pytest.mark.parametrize('change', [None, 'ticket', 'link', 'old-relay'])
def test_approved_telegram_group_rechecks_ticket_and_project_before_relay_write(store, monkeypatch, change):
    from types import SimpleNamespace

    chat_id = '-1001234567'
    ticket = _ticket(store, source_provider='telegram', source_thread_id=chat_id, linked_project_id=7)
    details = reviewed_workflow(store, ticket, 'Exact reviewed Telegram reply.')
    with store.begin() as db:
        db.get(Chat, details['workflow']['chat_id']).project_id = 7
    projects = {7}
    write = Mock(return_value=True)

    def connected():
        if change == 'ticket':
            with store.begin() as db:
                assert db.query(OrchestratorEvent).filter_by(status='sending').count() == 1
                db.get(KanbanTicket, ticket).source_thread_id = '-1009999999'
        elif change == 'link':
            projects.clear()
            projects.add(8)
        return True

    queued = []
    manager = SimpleNamespace(telegram_user_id=12345, _send_websocket_message=write, is_connected=connected,
                              queue_closing_reply=lambda frame: queued.append(frame) or True)
    monkeypatch.setattr('distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app', lambda: manager)
    monkeypatch.setattr('distr.core.incoming.project_links.source_project_index',
                        lambda: {('telegram', chat_id): projects})
    outcome = delivery.execute_approved_workflow_reply(details)
    assert not outcome['delivered'] and outcome['delivery_status'] == 'sending'
    assert len(queued) == 1
    from distr.core.workflow import telegram_reply_receipts as receipts

    assert connected()
    frame = queued[0]
    if change:
        assert not receipts.authorize(frame, owner=12345, relay_verified=change != 'old-relay')
        write.assert_not_called()
    else:
        assert receipts.authorize(frame, owner=12345)
        assert not receipts.authorize(frame, owner=12345)
        assert not receipts.confirm(frame['client_message_id'], telegram_chat_id=-1009999999, telegram_message_id=44)
        assert receipts.confirm(frame['client_message_id'], telegram_chat_id=int(chat_id), telegram_message_id=44)
        assert delivery.execute_approved_workflow_reply(details)['duplicate']
        assert frame['chat_id'] == int(chat_id) and frame['text'] == details['body']
    with store() as db:
        assert db.query(OrchestratorEvent).one().status == ('not_sent' if change else 'sent')


def test_telegram_closing_receipt_survives_a_local_write_outage(store, monkeypatch, tmp_path):
    from sqlalchemy.exc import OperationalError
    from types import SimpleNamespace
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.workflow import telegram_reply_receipts as receipts

    ticket = _ticket(store, source_provider='telegram', source_thread_id='12345')
    details = reviewed_workflow(store, ticket, 'Exact reviewed Telegram reply.')
    queue = Mock(return_value=True)
    manager = SimpleNamespace(telegram_user_id=12345, queue_closing_reply=queue)
    monkeypatch.setattr('distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app', lambda: manager)
    monkeypatch.setattr(receipt_writes, '_journal_path', lambda: tmp_path / 'receipts')
    assert delivery.execute_approved_workflow_reply(details)['delivery_status'] == 'sending'
    frame = queue.call_args.args[0]
    assert receipts.authorize(frame, owner=12345)
    real_confirm = receipts.confirm
    monkeypatch.setattr(receipts, 'confirm', Mock(side_effect=OperationalError('busy', {}, Exception('locked'))))
    assert receipts.accept_receipt(frame['client_message_id'], telegram_chat_id=12345, telegram_message_id=77) is None
    assert list((tmp_path / 'receipts').glob('*.json'))
    receipt_writes._pending.clear()
    monkeypatch.setattr(receipts, 'confirm', real_confirm)
    assert receipt_writes.flush_pending_receipt_writes()
    assert list((tmp_path / 'receipts').glob('*.json')) == []
    with store() as db:
        assert receipts.reviewed_outcome(db, details)['result']['telegram_message_id'] == 77
    queue.assert_called_once()
