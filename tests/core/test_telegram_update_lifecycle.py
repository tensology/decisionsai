import contextlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from distr.core.db import Base, Chat
from distr.core.incoming import telegram_updates as updates
from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender


TICKET = {'source_metadata': {'approval_source': 'telegram'}, 'reference': 'CHECK-1'}


@pytest.fixture
def store(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    monkeypatch.setattr(updates, 'get_session', session)
    sender = DummyTelegramSender()
    monkeypatch.setattr(updates, '_manager', lambda: sender)
    with factory() as db:
        db.add(Chat(id=1, title='Scoped work', params='{}'))
        db.commit()
    return factory, sender


def receipt(factory):
    with factory() as db:
        return json.loads(db.get(Chat, 1).params)[updates._KEY]


@pytest.mark.parametrize('state', ['queued', 'sending', 'unconfirmed'])
def test_intermediate_update_does_not_erase_outstanding_receipt_or_late_ack(store, state):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'job'}, 'development')
    token = receipt(factory)['pending']['awaiting']['token']
    if state != 'queued':
        sender._process_message_queue()
    if state == 'unconfirmed':
        updates.record_delivery(token, error='Ambiguous transport result')
    updates.queue_execution_update(TICKET, 1, {'id': 'job'}, 'testing')
    current = receipt(factory)
    assert current['pending']['key'] == 'job:development'
    assert current['pending']['awaiting']['token'] == token
    if state == 'queued':
        assert not updates.record_delivery(token, telegram_message_id=111, telegram_chat_id=12345)
        assert not updates.record_delivery(token, error='Late error for an unsent chunk')
        assert receipt(factory) == current
        sender._process_message_queue()
    assert updates.record_delivery(token, telegram_message_id=111, telegram_chat_id=12345)
    assert receipt(factory)['pending']['key'] == 'job:testing'


@pytest.mark.parametrize('replayed_stage', ['done', 'testing'])
@pytest.mark.parametrize('legacy_receipt', [False, True])
def test_later_job_receipt_does_not_reopen_a_delivered_job(store, replayed_stage, legacy_receipt):
    factory, sender = store
    for index, job in enumerate(('older', 'newer'), start=1):
        updates.queue_execution_update(TICKET, 1, {'id': job, 'summary': f'{job} result'}, 'done')
        sender._process_message_queue()
        token = sender.websocket_attempts[-1]['client_message_id']
        assert updates.record_delivery(token, telegram_message_id=index, telegram_chat_id=12345)
        assert receipt(factory)['sent'] == f'{job}:done'
        if legacy_receipt and job == 'older':
            with factory.begin() as db:
                chat = db.get(Chat, 1)
                params = json.loads(chat.params)
                params[updates._KEY].pop('delivered_keys')
                chat.params = json.dumps(params)

    updates.queue_execution_update(TICKET, 1, {'id': 'older'}, replayed_stage)
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    assert receipt(factory)['pending'] is None

    updates.queue_execution_update(TICKET, 1, {'id': 'third'}, 'testing')
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 3
    assert receipt(factory)['pending']['key'] == 'third:testing'


def test_relay_rate_limit_retries_the_same_proposal_chunk_only_after_cooldown(store, monkeypatch):
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    factory, sender = store
    clock = [1000.0]
    monkeypatch.setattr(updates, 'time', SimpleNamespace(time=lambda: clock[0]))
    manager = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    updates.queue_execution_update(TICKET, 1, {'id': 'job'}, 'testing')
    assert not updates.defer_rate_limited_delivery(receipt(factory)['pending']['awaiting']['token'])
    sender._process_message_queue()
    original = sender.websocket_attempts[-1]
    token = original['client_message_id']
    TelegramWebSocketManager._on_message(manager, json.dumps({
        'type': 'error', 'message': 'Rate limited',
        'reason': 'relay_rate_limited', 'client_message_id': token,
    }))
    waiting = receipt(factory)['pending']
    assert waiting['offset'] == 0 and 'awaiting' not in waiting
    assert waiting['relay_retry_at'] == 1060.0
    assert not updates.record_delivery(token, telegram_message_id=110, telegram_chat_id=12345)

    updates.queue_execution_update(TICKET, 1, {'id': 'job', 'summary': 'Final result'}, 'done')
    clock[0] = 1059.0
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    assert receipt(factory)['pending']['key'] == 'job:testing'
    assert receipt(factory)['backlog'][0]['key'] == 'job:done'

    clock[0] = 1060.0
    updates.flush_execution_updates()
    sender._process_message_queue()
    retried = sender.websocket_attempts[-1]
    assert len(sender.websocket_attempts) == 2
    assert retried['text'] == original['text'] and retried['chat_id'] == original['chat_id']
    assert retried['client_message_id'] != token
    assert not updates.defer_rate_limited_delivery(token)
    assert updates.record_delivery(retried['client_message_id'], telegram_message_id=111, telegram_chat_id=12345)
    assert receipt(factory)['pending']['key'] == 'job:done'


def test_replayed_intermediate_is_ignored_after_a_later_stage_is_delivered(store):
    factory, sender = store
    for index, stage in enumerate(('development', 'testing'), start=1):
        updates.queue_execution_update(TICKET, 1, {'id': 'job'}, stage)
        sender._process_message_queue()
        assert updates.record_delivery(sender.websocket_attempts[-1]['client_message_id'], telegram_message_id=index, telegram_chat_id=12345)

    updates.queue_execution_update(TICKET, 1, {'id': 'job'}, 'development')
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    assert receipt(factory)['pending'] is None


def test_final_result_waits_for_inflight_intermediate_instead_of_losing_its_receipt(store):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'job'}, 'testing')
    sender._process_message_queue()
    token = receipt(factory)['pending']['awaiting']['token']
    updates.queue_execution_update(TICKET, 1, {'id': 'job', 'summary': 'Exact final result'}, 'done')
    assert receipt(factory)['pending']['key'] == 'job:testing'
    assert updates.record_delivery(token, telegram_message_id=112, telegram_chat_id=12345)
    assert receipt(factory)['pending']['key'] == 'job:done'


def test_utf16_chunks_are_bounded_and_reassemble_exact_result_with_ack_continuity(store):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'emoji', 'summary': '😀' * 5000 + ' FINAL TAIL'}, 'done')
    expected = receipt(factory)['pending']['text']
    chunks = []
    while receipt(factory).get('pending'):
        sender._process_message_queue()
        message = sender.websocket_attempts[-1]
        assert len(message['text'].encode('utf-16-le')) // 2 <= 3500
        chunks.append(message['text'])
        assert updates.record_delivery(message['client_message_id'], telegram_message_id=200 + len(chunks), telegram_chat_id=12345)
    assert ''.join(chunks) == expected


@pytest.mark.parametrize('legacy', [False, True])
def test_owner_change_before_transport_recovers_only_for_original_owner(store, legacy):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'owner', 'summary': 'Private project result'}, 'done')
    queued = receipt(factory)['pending']['awaiting']
    sender.telegram_user_id = 67890
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    blocked = receipt(factory)['pending']
    assert 'awaiting' not in blocked
    assert 'recipient changed' in blocked['delivery_error']
    if legacy:
        with factory.begin() as db:
            chat = db.get(Chat, 1)
            params = json.loads(chat.params)
            params[updates._KEY]['pending']['awaiting'] = {
                **queued, 'state': 'unconfirmed',
                'error': 'Telegram recipient changed before transport. Review this retained delivery before retrying.',
            }
            chat.params = json.dumps(params)
        assert not updates.record_delivery(queued['token'], telegram_message_id=111, telegram_chat_id=12345)
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert sender.websocket_attempts == []

    sender.telegram_user_id = 12345
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    delivered = sender.websocket_attempts[0]
    assert delivered['chat_id'] == 12345
    assert delivered['text'] == blocked['text']
    assert updates.record_delivery(delivered['client_message_id'], telegram_message_id=112, telegram_chat_id=12345)
    assert receipt(factory)['pending'] is None


def test_owner_change_between_chunks_does_not_redirect_remaining_private_result(store):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'owner', 'summary': 'x' * 8000}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[-1]['client_message_id']
    sender.telegram_user_id = 67890
    assert updates.record_delivery(token, telegram_message_id=313, telegram_chat_id=12345)
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    blocked = receipt(factory)['pending']
    assert blocked['offset'] > 0
    assert 'recipient changed' in blocked['delivery_error']

    sender.telegram_user_id = 12345
    updates.flush_execution_updates()
    resumed = receipt(factory)['pending']
    assert 'delivery_error' not in resumed
    assert resumed['offset'] == blocked['offset']
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    assert sender.websocket_attempts[-1]['chat_id'] == 12345
    assert sender.websocket_attempts[-1]['text'] == resumed['text'][resumed['offset']:resumed['awaiting']['end']]


def test_transport_exception_is_unconfirmed_without_crashing_or_resending(store):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'exception'}, 'done')
    def unknown(_message):
        raise RuntimeError('Socket failed after possible acceptance')
    sender._send_websocket_message = unknown
    sender._process_message_queue()
    assert receipt(factory)['pending']['awaiting']['state'] == 'unconfirmed'
    sender._process_message_queue()
    assert receipt(factory)['pending']['offset'] == 0


def test_restart_before_transport_requeues_once_and_late_ack_survives_later_stage(store, monkeypatch):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'restart'}, 'development')
    retired_attempt = receipt(factory)['pending']['awaiting']
    monkeypatch.setattr(updates, '_PROCESS', 'replacement-process')
    monkeypatch.setattr(updates, 'owner_is_gone', lambda owner: owner == retired_attempt['owner'])
    replacement = DummyTelegramSender()
    monkeypatch.setattr(updates, '_manager', lambda: replacement)
    updates.flush_execution_updates()
    replacement._process_message_queue()
    assert len(replacement.websocket_attempts) == 1
    token = replacement.websocket_attempts[0]['client_message_id']
    assert token != retired_attempt['token']
    assert not updates.record_delivery(retired_attempt['token'], telegram_message_id=140, telegram_chat_id=12345)
    updates.queue_execution_update(TICKET, 1, {'id': 'restart'}, 'verification')
    updates.flush_execution_updates()
    assert len(replacement.websocket_attempts) == 1
    assert updates.record_delivery(token, telegram_message_id=141, telegram_chat_id=12345)
    replacement._process_message_queue()
    assert len(replacement.websocket_attempts) == 2
    assert 'Checking the result' in replacement.websocket_attempts[-1]['text']


def test_unknown_attempt_after_restart_retains_backlog_until_late_ack(store, monkeypatch):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'unknown'}, 'testing')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    retired_owner = receipt(factory)['pending']['awaiting']['owner']
    monkeypatch.setattr(updates, '_PROCESS', 'new-process')
    monkeypatch.setattr(updates, 'owner_is_gone', lambda owner: owner == retired_owner)
    replacement = DummyTelegramSender()
    monkeypatch.setattr(updates, '_manager', lambda: replacement)
    updates.queue_execution_update(TICKET, 1, {'id': 'unknown', 'summary': 'Protected final'}, 'done')
    replacement._process_message_queue()
    assert replacement.websocket_attempts == []
    assert receipt(factory)['pending']['awaiting']['state'] == 'unconfirmed'
    assert updates.record_delivery(token, telegram_message_id=151, telegram_chat_id=12345)
    replacement._process_message_queue()
    assert len(replacement.websocket_attempts) == 1 and 'Protected final' in replacement.websocket_attempts[0]['text']


def test_offline_enqueue_binds_existing_configured_owner_before_identity_changes(store, monkeypatch):
    factory, sender = store
    monkeypatch.setattr(updates, '_manager', lambda: None)
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'connected_accounts': [{'provider': 'telegram', 'user_id': 12345}]})
    updates.queue_execution_update(TICKET, 1, {'id': 'offline', 'summary': 'Private offline result'}, 'done')
    assert receipt(factory)['recipient_id'] == 12345
    sender.telegram_user_id = 67890
    monkeypatch.setattr(updates, '_manager', lambda: sender)
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    assert 'recipient changed' in receipt(factory)['pending']['delivery_error']


def test_malformed_awaiting_state_cannot_be_assumed_safe_to_resend(store):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'bad-state'}, 'done')
    with factory() as db:
        chat = db.get(Chat, 1)
        params = json.loads(chat.params)
        params[updates._KEY]['pending']['awaiting'].update(state='invalid', process='previous-process')
        chat.params = json.dumps(params)
        db.commit()
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert sender.websocket_attempts == []


def test_manager_replacement_in_same_process_recovers_pretransport_queue_only(store, monkeypatch):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'replacement'}, 'done')
    replacement = DummyTelegramSender()
    monkeypatch.setattr(updates, '_manager', lambda: replacement)
    updates.flush_execution_updates()
    replacement._process_message_queue()
    assert len(replacement.websocket_attempts) == 1
    sender._process_message_queue()
    assert sender.websocket_attempts == []


def test_ack_does_not_promote_null_backlog_entry_or_lose_following_result(store):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'first'}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    updates.queue_execution_update(TICKET, 1, {'id': 'second', 'summary': 'Retained result'}, 'done')
    with factory.begin() as db:
        chat = db.get(Chat, 1)
        params = json.loads(chat.params)
        params[updates._KEY]['backlog'].insert(0, None)
        chat.params = json.dumps(params)
    prior = receipt(factory)

    assert not updates.record_delivery(token, telegram_message_id=111, telegram_chat_id=12345)
    assert receipt(factory) == prior
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1


@pytest.mark.parametrize('recipient_changed', [False, True])
def test_database_failure_before_transport_retains_exact_queued_update(store, recipient_changed):
    from sqlite3 import OperationalError as SQLiteOperationalError
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'claim-retry', 'summary': 'Private exact result'}, 'done')
    saved = receipt(factory)['pending']['awaiting']
    if recipient_changed:
        sender.telegram_user_id = 67890
    engine = factory.kw['bind']

    def reject_write(_connection, _cursor, statement, parameters, *_):
        if statement.lstrip().upper().startswith('UPDATE'):
            raise OperationalError(statement, parameters, SQLiteOperationalError('database is locked'))

    event.listen(engine, 'before_cursor_execute', reject_write)
    try:
        sender._process_message_queue()
    finally:
        event.remove(engine, 'before_cursor_execute', reject_write)
    assert not sender.websocket_attempts
    assert receipt(factory)['pending']['awaiting'] == saved
    sender._process_message_queue()
    if recipient_changed:
        assert not sender.websocket_attempts
        assert 'awaiting' not in receipt(factory)['pending']
        assert 'recipient changed' in receipt(factory)['pending']['delivery_error']
        sender.telegram_user_id = 12345
        updates.flush_execution_updates()
        sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    message = sender.websocket_attempts[0]
    token = message['client_message_id']
    if recipient_changed:
        assert token != saved['token']
        assert not updates.record_delivery(saved['token'], telegram_message_id=111, telegram_chat_id=12345)
    else:
        assert token == saved['token']
    assert message['chat_id'] == 12345
    assert message['text'] == receipt(factory)['pending']['text']
    assert updates.record_delivery(token, telegram_message_id=112, telegram_chat_id=12345)
    assert receipt(factory)['sent'] == 'claim-retry:done'


def test_database_failure_after_definite_socket_refusal_retries_the_release(store, monkeypatch):
    from sqlalchemy.exc import OperationalError

    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'release-retry'}, 'done')
    original_send = sender._send_websocket_message
    original_release = updates.mark_delivery_not_sent
    monkeypatch.setattr(sender, '_send_websocket_message', lambda _message: False)

    def blocked_release(*_args, **_kwargs):
        raise OperationalError('update', {}, RuntimeError('database is locked'))

    monkeypatch.setattr(updates, 'mark_delivery_not_sent', blocked_release)
    sender._process_message_queue()
    assert not sender.websocket_attempts
    assert receipt(factory)['pending']['awaiting']['state'] == 'sending'

    monkeypatch.setattr(updates, 'mark_delivery_not_sent', original_release)
    monkeypatch.setattr(sender, '_send_websocket_message', original_send)
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    token = sender.websocket_attempts[0]['client_message_id']
    assert '_proposal_release_pending' not in sender.websocket_attempts[0]
    assert updates.record_delivery(token, telegram_message_id=113, telegram_chat_id=12345)
    assert receipt(factory)['sent'] == 'release-retry:done'


def test_superseded_queue_cannot_block_replacement_when_old_owner_changes(store, monkeypatch):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'stale-queue'}, 'done')
    replacement = DummyTelegramSender()
    monkeypatch.setattr(updates, '_manager', lambda: replacement)
    updates.flush_execution_updates()
    current = receipt(factory)['pending']['awaiting']

    sender.telegram_user_id = 67890
    sender._process_message_queue()
    assert not sender.websocket_attempts
    assert receipt(factory)['pending']['awaiting'] == current

    replacement._process_message_queue()
    assert len(replacement.websocket_attempts) == 1
    assert replacement.websocket_attempts[0]['client_message_id'] == current['token']


def test_socket_failure_does_not_requeue_after_relay_error(store, monkeypatch):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'relay-error'}, 'done')

    def interrupted_transport(message):
        assert updates.record_delivery(message['client_message_id'], error='Relay outcome unknown')
        return False

    monkeypatch.setattr(sender, '_send_websocket_message', interrupted_transport)
    sender._process_message_queue()
    awaiting = receipt(factory)['pending']['awaiting']
    assert awaiting['state'] == 'unconfirmed'
    assert awaiting['error'] == 'Relay outcome unknown'
    assert sender._outbound_retry_message is None


def test_definite_socket_refusal_can_retry_same_queued_chunk(store, monkeypatch):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'socket-refused'}, 'done')
    token = receipt(factory)['pending']['awaiting']['token']

    with monkeypatch.context() as patch:
        patch.setattr(sender, '_send_websocket_message', lambda _message: False)
        sender._process_message_queue()

    assert receipt(factory)['pending']['awaiting']['state'] == 'queued'
    assert sender._outbound_retry_message['client_message_id'] == token
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    assert sender.websocket_attempts[0]['client_message_id'] == token
    assert updates.record_delivery(token, telegram_message_id=111, telegram_chat_id=12345)
    assert receipt(factory)['pending'] is None


@pytest.mark.parametrize('write_result', ['zero', 'short', 'full'])
def test_qt_socket_write_counts_keep_correct_proposal_receipt(store, monkeypatch, write_result):
    from types import MethodType, SimpleNamespace
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    factory, sender = store
    writes = []

    def send_text(payload):
        writes.append(payload)
        count = len(payload.encode('utf-8'))
        return {'zero': 0, 'short': count - 1, 'full': count}[write_result]

    sender.socket = SimpleNamespace(sendTextMessage=send_text)
    monkeypatch.setattr(sender, '_send_websocket_message',
                        MethodType(TelegramWebSocketManager._send_websocket_message, sender))
    updates.queue_execution_update(TICKET, 1, {'id': 'qt-write', 'summary': 'Test passed ✅'}, 'done')
    sender._process_message_queue()
    pending = receipt(factory)['pending']
    assert pending['offset'] == 0
    assert pending['awaiting']['state'] == ('sending' if write_result == 'full' else 'unconfirmed')
    if write_result != 'full':
        assert 'WebSocket' in pending['awaiting']['error']

    updates.flush_execution_updates()
    sender._process_message_queue()
    assert len(writes) == 1
    payload = json.loads(writes[0])
    assert payload['text'] == pending['text']
    assert updates.record_delivery(payload['client_message_id'], telegram_message_id=111, telegram_chat_id=12345)
    assert receipt(factory)['pending'] is None


def test_qt_proposal_write_is_not_reported_as_sent_by_worker_signal():
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from unittest.mock import Mock
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    send_text = Mock()
    emit = Mock()
    sender = SimpleNamespace(socket=SimpleNamespace(sendTextMessage=send_text), is_connected=lambda: True,
                             _send_ws_text_signal=SimpleNamespace(emit=emit))
    with ThreadPoolExecutor(max_workers=1) as executor:
        proposal = executor.submit(TelegramWebSocketManager._send_websocket_message, sender,
                                   {'type': 'send_message', 'client_message_id': 'proposal-update:1:fixture:0'})
        assert proposal.result() is False
        emit.assert_not_called()
        control = executor.submit(TelegramWebSocketManager._send_websocket_message, sender,
                                  {'type': 'typing_indicator'})
        assert control.result() is True
    send_text.assert_not_called()
    assert json.loads(emit.call_args.args[0]) == {'type': 'typing_indicator'}


@pytest.mark.parametrize('invalid_id', [True, -1, 0, 1.5, {}, 'not-an-id'])
def test_malformed_provider_ack_cannot_advance_durable_offset(store, invalid_id):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'bad-ack'}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    updates.record_delivery(token, telegram_message_id=invalid_id, telegram_chat_id=12345)
    assert receipt(factory)['pending']['offset'] == 0
    assert receipt(factory)['pending']['awaiting']['state'] == 'unconfirmed'
    assert updates.record_delivery(token, telegram_message_id=444, telegram_chat_id=12345)
    assert receipt(factory)['pending'] is None


@pytest.mark.parametrize('recipient_fields', [{}, {'chat_id': None}, {'chat_id': 67890}])
def test_actual_manager_requires_matching_ack_recipient_and_reconciles_late_ack(store, monkeypatch, recipient_fields):
    from types import SimpleNamespace
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'recipient-ack'}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    monkeypatch.setattr('distr.core.workflow.interactions.record_telegram_delivery', lambda **kwargs: None)
    manager = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    TelegramWebSocketManager._on_message(manager, json.dumps({'type': 'message_sent', 'client_message_id': token,
        'telegram_message_id': 909, **recipient_fields}))
    assert receipt(factory)['pending']['offset'] == 0
    assert receipt(factory)['pending']['awaiting']['state'] == 'unconfirmed'
    assert 'recipient' in receipt(factory)['pending']['awaiting']['error']
    TelegramWebSocketManager._on_message(manager, json.dumps({'type': 'message_sent', 'client_message_id': token,
        'telegram_message_id': 909, 'chat_id': 12345}))
    assert receipt(factory)['pending'] is None
    assert len(sender.websocket_attempts) == 1
