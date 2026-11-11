from distr.core.incoming.mail_reply import send_incoming_mail_reply
import base64
from email import message_from_bytes
from unittest.mock import Mock
from uuid import uuid4
import pytest


def test_quick_reply_dry_run_gmail_never_sends():
    result = send_incoming_mail_reply(
        channel="gmail",
        to="client@example.com",
        subject="Re: Quote",
        body="Thanks — looking into this.",
        message_id="abc123",
        dry_run=True,
    )
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["channel"] == "gmail"
    assert result["to"] == "client@example.com"
    assert result["subject"] == "Re: Quote"
    assert result["message_id"] == "abc123"


def test_quick_reply_dry_run_mailshake_alias_maps_to_mailshot():
    result = send_incoming_mail_reply(
        channel="mailshake",
        to="ops@example.com",
        subject="Re: Invoice",
        body="Received, thank you.",
        dry_run=True,
    )
    assert result["success"] is True
    assert result["channel"] == "mailshot"


def test_quick_reply_requires_recipient_and_body():
    try:
        send_incoming_mail_reply(channel="gmail", to="", subject="Re:", body="Hi", dry_run=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "recipient" in str(exc).lower()
    try:
        send_incoming_mail_reply(channel="gmail", to="a@b.com", subject="Re:", body="  ", dry_run=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "reply text" in str(exc).lower()


def test_gmail_quick_reply_uses_exact_reviewed_destination_subject_and_rfc_thread(monkeypatch):
    from distr.core.agent.services.integrations import google_workspace
    connector = object.__new__(google_workspace.GoogleWorkspaceConnector)
    connector.is_connected = lambda: True
    connector._ensure_valid_token = lambda: True
    connector.get_email = lambda mid: {'from': 'original@example.com', 'reply_to': 'support@example.com',
        'subject': 'Re: Original', 'threadId': 'thread', 'message_id_header': '<rfc@example.com>',
        'references': '<earlier@example.com>'}
    connector._make_request = Mock(return_value={'id': 'sent-id', 'threadId': 'thread'})
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)
    result = send_incoming_mail_reply(channel='gmail', to='reviewed@example.com', subject='Reviewed subject',
                                     body='Exact text.', message_id='gmail-api-id')
    payload = connector._make_request.call_args.kwargs['json']
    mail = message_from_bytes(base64.urlsafe_b64decode(payload['raw']))
    assert result['success'] is True
    assert mail['To'] == 'reviewed@example.com'
    assert mail['Subject'] == 'Reviewed subject'
    assert mail['In-Reply-To'] == '<rfc@example.com>'
    assert mail['References'] == '<earlier@example.com> <rfc@example.com>'
    assert payload['threadId'] == 'thread'


@pytest.mark.parametrize('missing', ['message', 'threadId', 'message_id_header', 'source_id'])
def test_gmail_quick_reply_preparation_does_not_claim_or_send(monkeypatch, missing):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.agent.services.integrations import google_workspace
    from distr.core.db import IncomingMailReplyReceipt, get_session
    from distr.gui.web.routes.development.incoming import register_routes

    original = {'id': 'source', 'from': 'original@example.com', 'subject': 'Subject',
                'threadId': 'thread', 'message_id_header': '<source@example.com>'}
    incomplete = {key: value for key, value in original.items() if key != missing}
    connector = object.__new__(google_workspace.GoogleWorkspaceConnector)
    connector.is_connected = lambda: True
    connector._ensure_valid_token = lambda: True
    connector.get_email = Mock(return_value=None if missing == 'message' else incomplete)
    connector._make_request = Mock(return_value={'id': 'sent-id', 'threadId': 'thread'})
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    path = '/workflows/studio/incoming/mail/reply'
    key = uuid4().hex
    args = dict(channel='gmail', to='reviewed@example.com', subject='Re: Subject',
                body=f'Exact reviewed wording for {missing}.',
                message_id='' if missing == 'source_id' else 'source', idempotency_key=key)
    response = client.post(path, json=args)
    assert response.status_code == 422
    assert response.json()['send_attempted'] is False
    assert 'Nothing was sent' in response.json()['error']
    connector._make_request.assert_not_called()
    if missing == 'source_id':
        connector.get_email.assert_not_called()
    with get_session() as db:
        assert db.get(IncomingMailReplyReceipt, key) is None

    connector.get_email.return_value = original
    args['message_id'] = 'source'
    response = client.post(path, json=args)
    assert response.status_code == 200
    connector._make_request.assert_called_once()
    assert client.post(path, json=args).json() == response.json()
    connector._make_request.assert_called_once()


def test_gmail_connector_default_honors_reply_to_without_repeated_subject_prefix():
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
    connector = object.__new__(GoogleWorkspaceConnector)
    connector._ensure_valid_token = lambda: True
    connector.get_email = lambda mid: {'from': 'original@example.com', 'reply_to': 'support@example.com',
        'subject': 'Re: Original', 'threadId': 'thread'}
    connector._make_request = Mock(return_value={'id': 'sent-id', 'threadId': 'thread'})
    assert connector.reply_to_email('api-id', 'Text')
    mail = message_from_bytes(base64.urlsafe_b64decode(connector._make_request.call_args.kwargs['json']['raw']))
    assert mail['To'] == 'support@example.com'
    assert mail['Subject'] == 'Re: Original'
    assert mail['In-Reply-To'] is None


def test_gmail_ambiguous_reply_failure_never_falls_back_to_another_send(monkeypatch):
    from distr.core.agent.services.integrations import google_workspace
    connector = Mock()
    connector.prepare_email_reply.return_value = {'raw': 'fixture', 'threadId': 'thread'}
    connector._make_request.return_value = None
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)
    with pytest.raises(RuntimeError, match='Check the original conversation'):
        send_incoming_mail_reply(channel='gmail', to='reviewed@example.com', subject='Subject',
                                 body='Exact text.', message_id='gmail-api-id')
    connector._make_request.assert_called_once()
    connector.send_email.assert_not_called()


@pytest.mark.parametrize('status', [307, 308, 500])
def test_gmail_send_stops_at_first_response_and_keeps_message_out_of_logs(monkeypatch, caplog, status):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from distr.core.agent.services.integrations import google_workspace

    content = 'Fixture message body'
    encoded = base64.urlsafe_b64encode(content.encode()).decode()
    calls = []

    class GmailFixture(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(status if self.path == '/send' else 200)
            if self.path == '/send' and status in (307, 308):
                self.send_header('Location', '/redirected')
            self.end_headers()
            self.wfile.write(f'{{"id":"synthetic-id","raw":"{encoded}"}}'.encode())

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), GmailFixture) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        request = google_workspace.requests.request
        monkeypatch.setattr(google_workspace.requests, 'request', lambda method, url, **kwargs:
                            request(method, f'http://127.0.0.1:{server.server_port}/send', **kwargs))
        connector = object.__new__(google_workspace.GoogleWorkspaceConnector)
        connector._ensure_valid_token = lambda: True
        connector.access_token = 'fixture-only'
        try:
            result = connector._make_request('POST', 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
                                             json={'raw': encoded}, timeout=2)
        finally:
            server.shutdown()
            worker.join(timeout=2)
    assert calls == ['/send']
    assert result is None
    assert encoded not in caplog.text


@pytest.mark.parametrize('provider_result', [None, {}, {'success': False}, {'error': 'rejected'}, {'success': True, 'error': 'rejected'}])
def test_mailshot_requires_provider_acceptance_and_passes_retry_key(monkeypatch, provider_result):
    from distr.core import tensology_client
    client = Mock()
    client.post.return_value = provider_result
    monkeypatch.setattr(tensology_client, 'configured_tensology_client', lambda **kwargs: client)
    key = uuid4().hex
    with pytest.raises(RuntimeError, match='not confirmed'):
        send_incoming_mail_reply(channel='mailshot', to='client@example.com', subject=f'Subject {key}',
                                 body='Text', idempotency_key=key)
    assert client.post.call_args.kwargs == {'approved': True, 'idempotency_key': key}


def test_mailshot_acceptance_keeps_existing_contract(monkeypatch):
    from distr.core import tensology_client
    client = Mock()
    client.post.return_value = {'_id': 'accepted-mail'}
    monkeypatch.setattr(tensology_client, 'configured_tensology_client', lambda **kwargs: client)
    assert send_incoming_mail_reply(channel='mailshot', to='client@example.com', subject='Subject', body='Text')['success']
    assert client.post.call_args.kwargs['idempotency_key']


def test_mailshot_quick_reply_uses_original_rfc_reference_and_supported_provider_fields(monkeypatch):
    from distr.core import tensology_client
    client = Mock()
    client.get.return_value = {'email': {'messageId': '<original@example.com>', 'references': ['<earlier@example.com>']}}
    client.post.return_value = {'messageId': '<sent@example.com>'}
    monkeypatch.setattr(tensology_client, 'configured_tensology_client', lambda **kwargs: client)
    send_incoming_mail_reply(channel='mailshot', to='reviewed@example.com', subject='Edited subject',
                             body='Exact text', message_id='database-id')
    payload = client.post.call_args.args[1]
    assert payload['inReplyTo'] == '<original@example.com>'
    assert payload['references'] == '<earlier@example.com>,<original@example.com>'
    assert payload['to'] == 'reviewed@example.com' and payload['subject'] == 'Edited subject'
    assert 'in_reply_to' not in payload and 'thread_id' not in payload


def test_mailshot_missing_reply_reference_blocks_send(monkeypatch):
    from distr.core import tensology_client
    client = Mock()
    client.get.return_value = {}
    monkeypatch.setattr(tensology_client, 'configured_tensology_client', lambda **kwargs: client)
    with pytest.raises(ValueError, match='reply reference'):
        send_incoming_mail_reply(channel='mailshot', to='client@example.com', subject='Subject', body='Text', message_id='missing')
    client.post.assert_not_called()


def test_quick_reply_same_key_replays_success_without_contacting_provider(monkeypatch):
    from distr.core.agent.services.integrations import google_workspace
    connector = Mock()
    connector.prepare_email_reply.return_value = {'raw': 'fixture', 'threadId': 'thread'}
    connector._make_request.return_value = {'id': 'sent-id', 'threadId': 'thread'}
    constructor = Mock(return_value=connector)
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', constructor)
    args = dict(channel='gmail', to='client@example.com', subject='Subject', body='Exact reviewed text',
                message_id='message', idempotency_key=uuid4().hex)
    first = send_incoming_mail_reply(**args)
    constructor.side_effect = AssertionError('Cached success must not contact provider')
    assert send_incoming_mail_reply(**args) == first
    connector._make_request.assert_called_once()
    with pytest.raises(ValueError, match='different reviewed text'):
        send_incoming_mail_reply(**{**args, 'body': 'Changed text'})


def test_quick_reply_unconfirmed_key_blocks_resend_and_retains_reviewed_text(monkeypatch):
    from distr.core.agent.services.integrations import google_workspace
    from distr.core.incoming.mail_reply import MailReplyUnconfirmed
    from distr.core.db import IncomingMailReplyReceipt, get_session
    connector = Mock()
    connector.prepare_email_reply.return_value = {'raw': 'fixture', 'threadId': 'thread'}
    connector._make_request.side_effect = TimeoutError('Provider might have accepted')
    constructor = Mock(return_value=connector)
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', constructor)
    args = dict(channel='gmail', to='client@example.com', subject='Subject', body='Exact reviewed text',
                message_id='message', idempotency_key=uuid4().hex)
    with pytest.raises(MailReplyUnconfirmed):
        send_incoming_mail_reply(**args)
    constructor.side_effect = AssertionError('Unknown delivery must not contact provider again')
    with pytest.raises(MailReplyUnconfirmed) as error:
        send_incoming_mail_reply(**args)
    assert error.value.receipt_status == 'unconfirmed'
    with pytest.raises(MailReplyUnconfirmed):
        send_incoming_mail_reply(**{**args, 'idempotency_key': uuid4().hex})
    connector._make_request.assert_called_once()
    with get_session() as db:
        row = db.get(IncomingMailReplyReceipt, args['idempotency_key'])
        assert row.status == 'unconfirmed'
        assert 'Exact reviewed text' in row.payload


def test_quick_reply_concurrent_same_key_makes_only_one_provider_attempt(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from distr.core.agent.services.integrations import google_workspace
    from distr.core.incoming.mail_reply import MailReplyUnconfirmed
    started, release = Event(), Event()
    connector = Mock()
    connector.prepare_email_reply.return_value = {'raw': 'fixture', 'threadId': 'thread'}
    def deliver(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return {'id': 'sent-id', 'threadId': 'thread'}
    connector._make_request.side_effect = deliver
    monkeypatch.setattr(google_workspace, 'GoogleWorkspaceConnector', lambda: connector)
    args = dict(channel='gmail', to='client@example.com', subject='Subject', body='Exact text',
                message_id='message', idempotency_key=uuid4().hex)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(send_incoming_mail_reply, **args)
        assert started.wait(timeout=5)
        try:
            with pytest.raises(MailReplyUnconfirmed) as error:
                send_incoming_mail_reply(**args)
            assert error.value.receipt_status == 'sending'
            with pytest.raises(MailReplyUnconfirmed):
                send_incoming_mail_reply(**{**args, 'idempotency_key': uuid4().hex})
        finally:
            release.set()
        assert first.result(timeout=5)['success'] is True
    connector._make_request.assert_called_once()


def test_quick_reply_http_preserves_uncertain_receipt_status_and_retry_key(monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.incoming import mail_reply
    from distr.gui.web.routes.development.incoming import register_routes
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    send = Mock(side_effect=mail_reply.MailReplyUnconfirmed('sending'))
    monkeypatch.setattr(mail_reply, 'send_incoming_mail_reply', send)
    response = TestClient(app).post('/workflows/studio/incoming/mail/reply', json={
        'channel': 'gmail', 'to': 'client@example.com', 'body': 'Text', 'idempotency_key': 'same-key'})
    assert response.status_code == 409
    assert response.json()['receipt_status'] == 'sending'
    assert send.call_args.kwargs['idempotency_key'] == 'same-key'


@pytest.mark.parametrize('status', ['missing', 'sending', 'unconfirmed', 'sent'])
def test_quick_reply_status_only_reads_the_exact_local_receipt(monkeypatch, status):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db import IncomingMailReplyReceipt, get_session
    from distr.core.incoming import mail_reply
    from distr.gui.web.routes.development.incoming import register_routes

    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    key = uuid4().hex
    if status != 'missing':
        with get_session() as db:
            db.add(IncomingMailReplyReceipt(id=key, fingerprint='fixture', status=status,
                                            payload='{"preview":{"body":"Private fixture text"}}'))
            db.commit()
    send = Mock(side_effect=AssertionError('Receipt lookup must never send'))
    monkeypatch.setattr(mail_reply, 'send_incoming_mail_reply', send)
    response = TestClient(app).get('/workflows/studio/incoming/mail/reply/status', params={'idempotency_key': key})
    assert response.status_code == 200
    assert response.json() == {'receipt_status': status}
    assert response.headers['cache-control'] == 'no-store'
    send.assert_not_called()
