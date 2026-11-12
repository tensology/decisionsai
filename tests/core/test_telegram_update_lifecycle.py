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
def store(monkeypatch, tmp_path):
    from distr.core.integrations.telegram import receipt_writes

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    monkeypatch.setattr(updates, 'get_session', session)
    monkeypatch.setattr(receipt_writes, '_journal_path', lambda: tmp_path / 'telegram-receipts', raising=False)
    sender = DummyTelegramSender()
    monkeypatch.setattr(updates, '_manager', lambda: sender)
    with factory() as db:
        db.add(Chat(id=1, title='Scoped work', params='{}'))
        db.commit()
    return factory, sender


def receipt(factory):
    with factory() as db:
        return json.loads(db.get(Chat, 1).params)[updates._KEY]


@pytest.mark.parametrize('first_state,recovery', [
    ('unsent', 'sync'), ('unsent', 'enqueue'), ('unconfirmed', 'sync'), ('unconfirmed', 'receipt'),
])
def test_backlog_without_current_delivery_recovers_oldest_completion(store, first_state, recovery):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'older', 'summary': 'Older result'}, 'done')
    if first_state == 'unconfirmed':
        sender._process_message_queue()
        old_token = sender.websocket_attempts[-1]['client_message_id']
        updates.record_delivery(old_token, error='The provider outcome is unknown.')
    with factory.begin() as db:
        chat = db.get(Chat, 1)
        params = json.loads(chat.params)
        state = params[updates._KEY]
        older = state['pending']
        if first_state == 'unsent':
            older.pop('awaiting')
            sender._message_queue.get_nowait()
        state.update(pending=None, backlog=[older])
        chat.params = json.dumps(params)

    if recovery == 'sync':
        updates.flush_execution_updates()
        recovered = receipt(factory)
        assert recovered['pending']['key'] == 'older:done' and 'backlog' not in recovered
    elif recovery == 'receipt':
        updates.accept_relay_receipt(old_token, telegram_message_id=90, telegram_chat_id=12345)
        assert receipt(factory)['sent'] == 'older:done'
        assert len(sender.websocket_attempts) == 1
    updates.queue_execution_update(TICKET, 1, {'id': 'newer', 'summary': 'Newer result'}, 'done')
    if recovery != 'receipt':
        sender._process_message_queue()
        assert len(sender.websocket_attempts) == 1
        old_token = sender.websocket_attempts[0]['client_message_id']
        assert updates.record_delivery(old_token, telegram_message_id=90, telegram_chat_id=12345)
    assert 'Older result' in sender.websocket_attempts[0]['text']
    assert not updates.record_delivery(old_token, telegram_message_id=90, telegram_chat_id=12345)
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    assert 'Newer result' in sender.websocket_attempts[-1]['text']


@pytest.mark.parametrize('duplicate_active', [False, True])
def test_backlog_recovery_never_replays_a_delivered_completion(store, duplicate_active):
    factory, sender = store
    with factory.begin() as db:
        duplicate = {'key': 'older:done', 'text': 'Already delivered before latest'}
        db.get(Chat, 1).params = json.dumps({updates._KEY: {
            'recipient_id': 12345, 'sent': 'latest:done', 'delivered_keys': ['older:done', 'latest:done'],
            'pending': duplicate if duplicate_active else None, 'backlog': [
                duplicate,
                {'key': 'latest:done', 'text': 'Already delivered latest'},
                {'key': 'next:done', 'text': 'Next retained result'},
            ],
        }})
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert [message['text'] for message in sender.websocket_attempts] == ['Next retained result']
    assert 'backlog' not in receipt(factory)


def test_confirmed_duplicate_alone_remains_sent_and_never_enters_transport(store):
    factory, sender = store
    params = {updates._KEY: {
        'recipient_id': 12345, 'sent': 'job:done',
        'pending': {'key': 'job:done', 'text': 'Already delivered'},
    }}
    with factory.begin() as db:
        db.get(Chat, 1).params = json.dumps(params)
    status = updates.public_proposal_update_status(params)
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    assert receipt(factory)['pending'] is None
    assert status['state'] == 'sent'


def test_already_queued_copy_checks_confirmed_history_at_transport(store):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'job', 'summary': 'Original result'}, 'done')
    assert sender._message_queue.qsize() == 1
    with factory.begin() as db:
        chat = db.get(Chat, 1)
        params = json.loads(chat.params)
        params[updates._KEY]['sent'] = 'job:done'
        chat.params = json.dumps(params)
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    updates.flush_execution_updates()
    assert receipt(factory)['pending'] is None


def test_replaced_approval_result_remains_visible_before_recovery_queues_it():
    params = {'incoming_proposal_handoff': {
        'telegram_approval': {'chat_id': 12345, 'job_id': 'retry'},
        'telegram_previous_approvals': [
            {'chat_id': 12345, 'job_id': 'original', 'outcome': {'stage': 'failed', 'text': 'Private result'}},
        ],
    }}
    assert updates.public_proposal_update_status(params)['state'] == 'queued'
    params[updates._KEY] = {'recipient_id': 12345, 'sent': 'original:failed'}
    assert updates.public_proposal_update_status(params)['state'] == 'sent'


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


@pytest.mark.parametrize('returns_unknown', [False, True])
def test_ambiguous_transport_is_unconfirmed_without_resending(store, returns_unknown):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'exception'}, 'done')
    calls = []
    def unknown(_message):
        calls.append(_message)
        if returns_unknown:
            return None
        raise RuntimeError('Socket failed after possible acceptance')
    sender._send_websocket_message = unknown
    sender._process_message_queue()
    assert receipt(factory)['pending']['awaiting']['state'] == 'unconfirmed'
    sender._process_message_queue()
    assert receipt(factory)['pending']['offset'] == 0
    assert len(calls) == 1


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


def test_disconnected_telegram_times_out_without_releasing_the_exact_attempt(store, monkeypatch):
    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'offline'}, 'testing')
    sender._process_message_queue()
    original = receipt(factory)['pending']['awaiting']
    assert original['state'] == 'sending'
    updates.queue_execution_update(TICKET, 1, {'id': 'offline', 'summary': 'Final result'}, 'done')
    monkeypatch.setattr(updates, '_manager', lambda: None)
    monkeypatch.setattr(updates.time, 'time', lambda: original['at'] + updates.ACK_TIMEOUT_SECONDS - 1)
    updates.flush_execution_updates()
    assert receipt(factory)['pending']['awaiting'] == original
    assert updates.public_proposal_update_status({updates._KEY: receipt(factory)})['state'] == 'sending'

    monkeypatch.setattr(updates.time, 'time', lambda: original['at'] + updates.ACK_TIMEOUT_SECONDS + 1)
    assert updates.public_proposal_update_status({updates._KEY: receipt(factory)})['state'] == 'unconfirmed'
    updates.flush_execution_updates()
    expired = receipt(factory)
    assert expired['pending']['awaiting']['state'] == 'unconfirmed'
    assert expired['pending']['awaiting']['token'] == original['token']
    assert expired['pending']['offset'] == 0
    assert len(sender.websocket_attempts) == 1

    assert updates.record_delivery(original['token'], telegram_message_id=153, telegram_chat_id=12345)
    assert not updates.record_delivery(original['token'], telegram_message_id=153, telegram_chat_id=12345)
    monkeypatch.setattr(updates, '_manager', lambda: sender)
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    assert 'Final result' in sender.websocket_attempts[-1]['text']
    assert sender.websocket_attempts[-1]['client_message_id'] != original['token']


def test_restart_looks_up_the_exact_receipt_without_resending_the_chunk(store, monkeypatch):
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'lookup'}, 'testing')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    retired_owner = receipt(factory)['pending']['awaiting']['owner']
    monkeypatch.setattr(updates, '_PROCESS', 'replacement-process')
    monkeypatch.setattr(updates, 'owner_is_gone', lambda owner: owner == retired_owner)
    replacement = DummyTelegramSender()
    replacement._mark_socket_heartbeat_seen = lambda: None
    replacement.request_proposal_receipt = lambda token: TelegramWebSocketManager.request_proposal_receipt(replacement, token)
    monkeypatch.setattr(updates, '_manager', lambda: replacement)

    updates.queue_execution_update(TICKET, 1, {'id': 'lookup', 'summary': 'Final result'}, 'done')
    assert replacement.websocket_attempts == []
    TelegramWebSocketManager._on_message(replacement, json.dumps({
        'type': 'connection', 'status': 'connected', 'proposal_receipt_recovery': True,
    }))
    replacement.telegram_user_id = 67890
    updates.flush_execution_updates()
    assert replacement.websocket_attempts == []
    replacement.telegram_user_id = 12345
    updates.flush_execution_updates()
    updates.flush_execution_updates()
    query = {'type': 'proposal_receipt_lookup', 'token': token}
    assert replacement.websocket_attempts == [query]

    TelegramWebSocketManager._on_message(replacement, json.dumps({
        'type': 'proposal_receipt_status', 'token': token, 'status': 'unconfirmed',
    }))
    assert receipt(factory)['pending']['awaiting']['state'] == 'unconfirmed'
    assert receipt(factory)['pending']['offset'] == 0
    TelegramWebSocketManager._on_message(replacement, json.dumps({
        'type': 'message_sent', 'client_message_id': token, 'telegram_message_id': 152, 'chat_id': 12345,
    }))
    replacement._process_message_queue()
    assert replacement.websocket_attempts[0] == query and len(replacement.websocket_attempts) == 2
    assert 'Final result' in replacement.websocket_attempts[1]['text']
    assert replacement.websocket_attempts[1]['client_message_id'] != token


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


def test_offline_result_waits_for_approval_chat_instead_of_binding_on_reconnect(store, monkeypatch):
    factory, sender = store
    monkeypatch.setattr(updates, '_manager', lambda: None)
    monkeypatch.setattr(updates, '_configured_recipient', lambda: None)
    result = {'id': 'offline', 'summary': 'Private result'}
    updates.queue_execution_update(TICKET, 1, result, 'done')
    sender.telegram_user_id = 67890
    monkeypatch.setattr(updates, '_manager', lambda: sender)
    updates.queue_execution_update(TICKET, 1, result, 'done')
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    assert receipt(factory).get('recipient_id') is None
    assert 'could not be verified' in receipt(factory)['pending']['delivery_error']

    verified = {**TICKET, 'source_metadata': {**TICKET['source_metadata'], 'approval_telegram_chat_id': 12345}}
    updates.queue_execution_update(verified, 1, result, 'done')
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    assert receipt(factory)['recipient_id'] == 12345
    sender.telegram_user_id = 12345
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert [message['chat_id'] for message in sender.websocket_attempts] == [12345]
    token = sender.websocket_attempts[0]['client_message_id']
    assert updates.record_delivery(token, telegram_message_id=51, telegram_chat_id=12345)
    updates.queue_execution_update(verified, 1, result, 'done')
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1


def test_approval_owner_conflict_holds_an_already_queued_notification(store):
    factory, sender = store
    result = {'id': 'conflict', 'summary': 'Private result'}
    updates.queue_execution_update(TICKET, 1, result, 'done')
    verified = {**TICKET, 'source_metadata': {**TICKET['source_metadata'], 'approval_telegram_chat_id': 67890}}
    updates.queue_execution_update(verified, 1, result, 'done')
    sender._process_message_queue()
    assert sender.websocket_attempts == []
    assert receipt(factory)['recipient_id'] == 12345
    assert 'different recipients' in receipt(factory)['pending']['delivery_error']


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


@pytest.mark.parametrize('recovery', ['sender', 'sync', 'restart'])
@pytest.mark.parametrize('relay_error', [False, True])
def test_database_failure_after_definite_socket_refusal_retries_the_release(store, monkeypatch, recovery, relay_error):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from distr.core.integrations.telegram import receipt_writes

    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'release-retry'}, 'done')
    refused = []

    def refuse(message):
        refused.append(message)
        if relay_error:
            updates.accept_relay_receipt(message['client_message_id'], error='Relay outcome unknown')
        return False

    def reject_release(_connection, _cursor, statement, parameters, *_):
        if refused and statement.lstrip().upper().startswith('UPDATE'):
            raise OperationalError(statement, parameters, RuntimeError('database is locked'))

    engine = factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', reject_release)
    try:
        with monkeypatch.context() as disconnected:
            disconnected.setattr(sender, '_send_websocket_message', refuse)
            sender._process_message_queue()
    finally:
        event.remove(engine, 'before_cursor_execute', reject_release)
    assert not sender.websocket_attempts
    awaiting = receipt(factory)['pending']['awaiting']
    assert awaiting['state'] == 'sending'
    journal = sorted(receipt_writes._journal_path().glob('*.json'))
    assert len(journal) == (2 if relay_error else 1)
    entry = journal[-1]
    saved = entry.read_text()
    if relay_error:
        assert json.loads(journal[0].read_text()) == ['proposal-unconfirmed', awaiting['token']]
    assert json.loads(saved) == [
        'proposal-socket-refused', awaiting['token'], awaiting['queue_id'], awaiting['attempt_id'],
    ]

    if recovery == 'restart':
        receipt_writes._pending.clear()
        sender = DummyTelegramSender()
        monkeypatch.setattr(updates, '_PROCESS', 'restarted-desktop')
        monkeypatch.setattr(updates, 'owner_is_gone', lambda _owner: True)
        # A scan can classify the old write as unconfirmed before SQLite
        # accepts the deferred journal write.
        assert updates._prepare_chunk(1, sender.telegram_user_id, updates._queue_identity(sender),
                                      updates.current_owner()) is None
        assert receipt(factory)['pending']['awaiting']['local_timeout'] is True
        with monkeypatch.context() as offline:
            offline.setattr(updates, '_manager', lambda: None)
            updates.flush_execution_updates()
        monkeypatch.setattr(updates, '_manager', lambda: sender)
        updates.flush_execution_updates()
    elif recovery == 'sync':
        updates.flush_execution_updates()
    sender._process_message_queue()
    if relay_error:
        assert not sender.websocket_attempts
        pending = receipt(factory)['pending']['awaiting']
        assert pending['state'] == 'unconfirmed'
        assert pending['error'] == ('Telegram did not return a delivery identity.' if recovery == 'restart'
                                    else 'Relay outcome unknown')
        assert not list(receipt_writes._journal_path().glob('*.json'))
        return
    assert len(sender.websocket_attempts) == 1
    packet = sender.websocket_attempts[0]
    token = packet['client_message_id']
    assert (token != refused[0]['client_message_id']) == (recovery == 'restart')
    assert packet['text'] == refused[0]['text'] and packet['chat_id'] == refused[0]['chat_id']
    assert '_proposal_release_pending' not in packet
    # Replaying a retained journal file cannot release a subsequent write,
    # even when the subsequent write uses the same queue token.
    sending = receipt(factory)['pending']['awaiting']
    assert sending['attempt_id'] != awaiting['attempt_id']
    entry.write_text(saved)
    updates.flush_execution_updates()
    assert receipt(factory)['pending']['awaiting'] == sending
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    assert updates.record_delivery(token, telegram_message_id=113, telegram_chat_id=12345)
    assert receipt(factory)['sent'] == 'release-retry:done'
    assert not list(receipt_writes._journal_path().glob('*.json'))


@pytest.mark.parametrize('response_type', ['ack', 'rate_limit'])
@pytest.mark.parametrize('restart', [False, True])
def test_transient_write_failure_retains_exact_relay_receipt(store, monkeypatch, response_type, restart):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from distr.core.integrations.telegram import receipt_writes

    factory, sender = store
    clock = [1000.0]
    monkeypatch.setattr(updates, 'time', SimpleNamespace(time=lambda: clock[0]))
    summary = 'Exact earlier result'
    if response_type == 'rate_limit':
        summary += 'x' * 3500
    updates.queue_execution_update(TICKET, 1, {'id': 'earlier', 'summary': summary}, 'done')
    sender._process_message_queue()
    if response_type == 'rate_limit':
        confirmed = sender.websocket_attempts[0]['client_message_id']
        assert updates.record_delivery(confirmed, telegram_message_id=110, telegram_chat_id=12345)
        sender._process_message_queue()
    offset = receipt(factory)['pending']['offset']
    original = sender.websocket_attempts[-1]
    attempts_before_recovery = len(sender.websocket_attempts)
    token = original['client_message_id']
    updates.queue_execution_update(TICKET, 1, {'id': 'later', 'summary': 'Exact later result'}, 'done')
    message = ({'type': 'message_sent', 'telegram_message_id': 111, 'chat_id': 12345}
               if response_type == 'ack' else
               {'type': 'error', 'reason': 'relay_rate_limited', 'message': 'Rate limited'})
    manager = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    engine = factory.kw['bind']

    def reject_write(_connection, _cursor, statement, parameters, *_):
        if statement.lstrip().upper().startswith('UPDATE'):
            raise OperationalError(statement, parameters, RuntimeError('database is locked'))

    event.listen(engine, 'before_cursor_execute', reject_write)
    try:
        TelegramWebSocketManager._on_message(manager, json.dumps({**message, 'client_message_id': token}))
        assert receipt(factory)['pending']['awaiting']['token'] == token
        updates.flush_execution_updates()
        assert receipt(factory)['pending']['awaiting']['token'] == token
    finally:
        event.remove(engine, 'before_cursor_execute', reject_write)
    assert len(sender.websocket_attempts) == attempts_before_recovery

    if restart:
        journal, = receipt_writes._journal_path().glob('*.json')
        if response_type == 'rate_limit':
            assert json.loads(journal.read_text()) == ['proposal-rate-limit', token, 1000.0]
        receipt_writes._pending.clear()
        monkeypatch.setattr(updates, '_PROCESS', 'restarted-desktop')
    clock[0] = 1010.0
    with monkeypatch.context() as offline:
        offline.setattr(updates, '_manager', lambda: None)
        updates.flush_execution_updates()
    assert len(sender.websocket_attempts) == attempts_before_recovery
    updates.flush_execution_updates()
    if response_type == 'rate_limit':
        pending = receipt(factory)['pending']
        assert pending['relay_retry_at'] == 1060.0 and 'awaiting' not in pending
        assert pending['offset'] == offset > 0
        sender._process_message_queue()
        assert len(sender.websocket_attempts) == attempts_before_recovery
        clock[0] = 1060.0
        updates.flush_execution_updates()
        sender._process_message_queue()
        retried = sender.websocket_attempts[-1]
        assert retried['client_message_id'] != token
        assert retried['text'] == original['text'] and retried['chat_id'] == original['chat_id']
        if restart:
            # A committed entry left behind during cleanup must never affect the new attempt.
            journal.write_text(json.dumps(['proposal-rate-limit', token, 1000.0]))
            updates.flush_execution_updates()
            assert receipt(factory)['pending']['awaiting']['token'] == retried['client_message_id']
        assert updates.record_delivery(retried['client_message_id'], telegram_message_id=112, telegram_chat_id=12345)
    assert receipt(factory)['sent'] == 'earlier:done'
    assert receipt(factory)['pending']['key'] == 'later:done'
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == attempts_before_recovery + (1 if response_type == 'ack' else 2)
    assert 'Exact later result' in sender.websocket_attempts[-1]['text']
    assert not list(receipt_writes._journal_path().glob('*.json'))


def test_proposal_acknowledgment_survives_restart_during_database_lock(store, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager

    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'confirmed', 'summary': 'Already delivered'}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    updates.queue_execution_update(TICKET, 1, {'id': 'next', 'summary': 'Still pending'}, 'done')

    def reject_write(_connection, _cursor, statement, parameters, *_):
        if statement.lstrip().upper().startswith('UPDATE'):
            raise OperationalError(statement, parameters, RuntimeError('database is locked'))

    engine = factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', reject_write)
    try:
        receiver = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
        TelegramWebSocketManager._on_message(receiver, json.dumps({
            'type': 'message_sent', 'client_message_id': token, 'telegram_message_id': 177, 'chat_id': 12345,
        }))
        assert receipt(factory)['pending']['awaiting']['token'] == token
        entry, = receipt_writes._journal_path().glob('*.json')
        assert json.loads(entry.read_text()) == [token, 177, 12345]
    finally:
        event.remove(engine, 'before_cursor_execute', reject_write)

    receipt_writes._pending.clear()
    monkeypatch.setattr(updates, '_PROCESS', 'new-desktop-process')
    with monkeypatch.context() as offline:
        offline.setattr(updates, '_manager', lambda: None)
        updates.flush_execution_updates()
    assert receipt(factory)['sent'] == 'confirmed:done'
    assert receipt(factory)['pending']['key'] == 'next:done'
    assert not list(receipt_writes._journal_path().glob('*.json'))
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    assert sender.websocket_attempts[-1]['client_message_id'] != token
    assert 'Still pending' in sender.websocket_attempts[-1]['text']


def test_restart_after_receipt_commit_does_not_apply_ack_to_the_next_chunk(store, monkeypatch):
    from distr.core.integrations.telegram import receipt_writes

    factory, sender = store
    updates.queue_execution_update(TICKET, 1, {'id': 'confirmed'}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    updates.queue_execution_update(TICKET, 1, {'id': 'next'}, 'done')
    with monkeypatch.context() as interrupted:
        interrupted.setattr(receipt_writes, '_discard_journal_entry', lambda _path: None)
        updates.accept_relay_receipt(token, telegram_message_id=178, telegram_chat_id=12345)
    saved = receipt(factory)
    assert saved['sent'] == 'confirmed:done'
    assert saved['pending']['key'] == 'next:done'
    entry, = receipt_writes._journal_path().glob('*.json')
    assert entry.exists()

    receipt_writes._pending.clear()
    updates.flush_execution_updates()
    assert receipt(factory) == saved
    assert not entry.exists()
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    assert sender.websocket_attempts[-1]['client_message_id'] == saved['pending']['awaiting']['token']


def test_receipt_can_commit_if_the_journal_directory_is_unavailable(store, monkeypatch):
    from distr.core.integrations.telegram import receipt_writes

    factory, sender = store
    folder = receipt_writes._journal_path()
    folder.write_text('Not a directory')
    updates.queue_execution_update(TICKET, 1, {'id': 'confirmed'}, 'done')
    sender._process_message_queue()
    token = sender.websocket_attempts[0]['client_message_id']
    updates.accept_relay_receipt(token, telegram_message_id=179, telegram_chat_id=12345)
    assert receipt(factory)['sent'] == 'confirmed:done'
    assert receipt(factory)['pending'] is None
    assert len(sender.websocket_attempts) == 1


def test_database_failure_after_enqueue_refusal_releases_the_orphaned_claim(store, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    factory, sender = store
    engine = factory.kw['bind']
    rejected = []

    def refuse(text, *, interaction_token):
        rejected.append((text, interaction_token))
        return False

    def reject_release(_connection, _cursor, statement, parameters, *_):
        if rejected and statement.lstrip().upper().startswith('UPDATE'):
            raise OperationalError(statement, parameters, RuntimeError('database is locked'))

    event.listen(engine, 'before_cursor_execute', reject_release)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sender, 'send_to_telegram', refuse)
            updates.queue_execution_update(TICKET, 1, {'id': 'job', 'summary': 'Exact result'}, 'done')
            updates.flush_execution_updates()
    finally:
        event.remove(engine, 'before_cursor_execute', reject_release)
    assert len(rejected) == 1
    assert not sender.websocket_attempts
    assert receipt(factory)['pending']['awaiting']['token'] == rejected[0][1]
    updates.flush_execution_updates()
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 1
    packet = sender.websocket_attempts[0]
    assert packet['text'] == rejected[0][0]
    assert packet['client_message_id'] != rejected[0][1]
    assert updates.record_delivery(packet['client_message_id'], telegram_message_id=113, telegram_chat_id=12345)
    assert receipt(factory)['pending'] is None


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
