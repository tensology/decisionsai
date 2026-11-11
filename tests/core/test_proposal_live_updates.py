import contextlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from distr.core.db import Base, Chat
from distr.core.incoming import telegram_updates as updates
from distr.core import tensology_notify as sounds
from distr.core import tensology_stream as stream


@pytest.fixture
def store(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            yield db
    monkeypatch.setattr(updates, "get_session", session)
    with factory() as db:
        row = Chat(title="Approved proposal", params="{}")
        db.add(row); db.commit(); cid = row.id
    return factory, cid


def test_telegram_update_survives_missing_manager_and_retries_only_once(store, monkeypatch):
    factory, cid = store
    monkeypatch.setattr(updates, "_manager", lambda: None)
    ticket = {"source_metadata": {"approval_source": "telegram"}, "project_name": "DecisionsAI", "reference": "PROBE-1"}
    state = {"job_id": "one", "status": "completed", "summary": "Marker verified. Final verification passed."}
    updates.queue_execution_update(ticket, cid, state, "done")
    with factory() as db:
        assert json.loads(db.get(Chat, cid).params)[updates._KEY]["pending"]
    send = Mock(side_effect=[False, True])
    monkeypatch.setattr(updates, "_manager", lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    updates.flush_execution_updates()
    updates.flush_execution_updates()
    updates.queue_execution_update(ticket, cid, state, "done")
    assert send.call_count == 2
    assert "Marker verified" in send.call_args.args[0]
    with factory() as db:
        pending = json.loads(db.get(Chat, cid).params)[updates._KEY]["pending"]
        assert pending["offset"] == 0
    assert updates.mark_delivery_attempt(send.call_args.kwargs["interaction_token"])
    updates.record_delivery(send.call_args.kwargs["interaction_token"], telegram_message_id=123, telegram_chat_id=42)
    with factory() as db:
        assert json.loads(db.get(Chat, cid).params)[updates._KEY] == {"sent": "one:done", "pending": None, "recipient_id": 42, "delivered_keys": ["one:done"]}


def test_non_telegram_ticket_stays_quiet(store, monkeypatch):
    _, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, "_manager", lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    updates.queue_execution_update({"source_metadata": {}}, cid, {"summary": "Done"}, "done")
    send.assert_not_called()


def test_only_ack_advances_chunk_and_duplicate_ack_is_ignored(store, monkeypatch):
    factory, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, '_manager', lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    updates.queue_execution_update({'source_metadata': {'approval_source': 'telegram'}}, cid,
                                   {'id': 'ack', 'summary': 'x' * 8000}, 'done')
    first = send.call_args.kwargs['interaction_token']
    updates.flush_execution_updates()
    assert send.call_count == 1
    assert updates.mark_delivery_attempt(first)
    assert not updates.mark_delivery_attempt(first)
    assert updates.record_delivery(first, telegram_message_id=100, telegram_chat_id=42)
    assert send.call_count == 2
    assert not updates.record_delivery(first, telegram_message_id=100, telegram_chat_id=42)
    with factory() as db:
        receipt = json.loads(db.get(Chat, cid).params)[updates._KEY]
        assert receipt['pending']['offset'] == 3500
        assert receipt.get('sent') is None


def test_restart_before_transport_requeues_but_unknown_transport_does_not(store, monkeypatch):
    factory, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, '_manager', lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    updates.queue_execution_update({'source_metadata': {'approval_source': 'telegram'}}, cid, {'id': 'restart'}, 'done')
    monkeypatch.setattr(updates, 'owner_is_gone', lambda _owner: True)
    monkeypatch.setattr(updates, '_PROCESS', 'new-process')
    updates.flush_execution_updates()
    assert send.call_count == 2  # Still only locally queued before restart.
    token = send.call_args.kwargs['interaction_token']
    assert updates.mark_delivery_attempt(token)
    monkeypatch.setattr(updates, '_PROCESS', 'third-process')
    updates.flush_execution_updates()
    assert send.call_count == 2  # Never resend an unknown provider outcome.
    with factory() as db:
        receipt = json.loads(db.get(Chat, cid).params)[updates._KEY]
        assert receipt['pending']['awaiting']['state'] == 'unconfirmed'
    assert updates.record_delivery(token, telegram_message_id=101, telegram_chat_id=42)  # Late acknowledgment reconciles it.
    with factory() as db:
        assert json.loads(db.get(Chat, cid).params)[updates._KEY] == {'sent': 'restart:done', 'pending': None, 'recipient_id': 42, 'delivered_keys': ['restart:done']}


@pytest.mark.parametrize('state', ['queued', 'sending'])
def test_another_process_does_not_take_or_expire_a_live_owners_attempt(store, monkeypatch, state):
    factory, cid = store
    send = Mock(return_value=True)
    manager = SimpleNamespace(telegram_user_id=42, send_to_telegram=send)
    monkeypatch.setattr(updates, '_manager', lambda: manager)
    updates.queue_execution_update({'source_metadata': {'approval_source': 'telegram'}}, cid, {'id': 'live'}, 'done')
    original = send.call_args.kwargs['interaction_token']
    if state == 'sending':
        assert updates.mark_delivery_attempt(original)
    monkeypatch.setattr(updates, '_PROCESS', 'another-process')
    monkeypatch.setattr(updates, 'owner_is_gone', lambda _owner: False)
    updates.flush_execution_updates()
    assert send.call_count == 1
    with factory() as db:
        awaiting = json.loads(db.get(Chat, cid).params)[updates._KEY]['pending']['awaiting']
        assert awaiting['token'] == original and awaiting['state'] == state
    monkeypatch.setattr(updates, 'owner_is_gone', lambda _owner: True)
    updates.flush_execution_updates()
    if state == 'queued':
        assert send.call_count == 2
        replacement = send.call_args.kwargs['interaction_token']
        assert replacement != original and not updates.mark_delivery_attempt(original)
        assert updates.mark_delivery_attempt(replacement)
        assert not updates.record_delivery(original, telegram_message_id=100, telegram_chat_id=42)
    else:
        assert send.call_count == 1
        assert updates.record_delivery(original, telegram_message_id=100, telegram_chat_id=42)


def test_provider_error_retains_exact_result_without_blind_retry(store, monkeypatch):
    factory, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, '_manager', lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    updates.queue_execution_update({'source_metadata': {'approval_source': 'telegram'}}, cid,
                                   {'id': 'rejected', 'summary': 'Keep this result'}, 'done')
    token = send.call_args.kwargs['interaction_token']
    assert updates.mark_delivery_attempt(token)
    updates.record_delivery(token, error='Provider outcome unavailable')
    updates.flush_execution_updates()
    assert send.call_count == 1
    with factory() as db:
        pending = json.loads(db.get(Chat, cid).params)[updates._KEY]['pending']
        assert 'Keep this result' in pending['text']
        assert pending['awaiting']['state'] == 'unconfirmed'


def test_final_result_survives_delayed_stage_and_next_job(store, monkeypatch):
    factory, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, '_manager', lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    ticket = {'source_metadata': {'approval_source': 'telegram'}}
    updates.queue_execution_update(ticket, cid, {'id': 'one', 'summary': 'First exact result'}, 'done')
    token = send.call_args.kwargs['interaction_token']
    assert updates.mark_delivery_attempt(token)
    updates.record_delivery(token, error='Unknown outcome')
    updates.queue_execution_update(ticket, cid, {'id': 'one'}, 'testing')
    updates.queue_execution_update(ticket, cid, {'id': 'two'}, 'development')
    updates.queue_execution_update(ticket, cid, {'id': 'two', 'summary': 'Second exact result'}, 'done')
    with factory() as db:
        receipt = json.loads(db.get(Chat, cid).params)[updates._KEY]
        assert 'First exact result' in receipt['pending']['text']
        assert receipt['pending']['awaiting']['state'] == 'unconfirmed'
        assert [item['key'] for item in receipt['backlog']] == ['two:done']
    assert send.call_count == 1
    updates.record_delivery(token, telegram_message_id=100, telegram_chat_id=42)
    assert send.call_count == 2
    assert 'Second exact result' in send.call_args.args[0]
    assert updates.mark_delivery_attempt(send.call_args.kwargs['interaction_token'])
    updates.record_delivery(send.call_args.kwargs['interaction_token'], telegram_message_id=101, telegram_chat_id=42)
    with factory() as db:
        assert json.loads(db.get(Chat, cid).params)[updates._KEY] == {'sent': 'two:done', 'pending': None, 'recipient_id': 42, 'delivered_keys': ['one:done', 'two:done']}


@pytest.mark.parametrize('invalid', ['invalid', [], '', False, None, {'pending':'invalid'}, {'pending':{'key':'bad','text':'Result','offset':-1}},
                                     {'backlog':['invalid']}, {'backlog':[None]}, {'backlog':{}}, {'backlog':None}, {'pending':{'key':'bad','text':'Result','awaiting':'invalid'}},
                                     *[{'pending':{'key':'bad','text':'Result','awaiting':{'token':'bad','end':6,'state':'sending','at':at}}}
                                       for at in ['yesterday', None, True, float('inf'), float('nan'), 10**400]]])
def test_corrupt_delivery_state_does_not_block_another_thread(store, monkeypatch, invalid):
    factory, cid = store
    with factory() as db:
        db.get(Chat, cid).params = json.dumps({updates._KEY:invalid})
        other=Chat(title='Other proposal',params='{}')
        db.add(other);db.commit();other_id=other.id
    send=Mock(return_value=True)
    monkeypatch.setattr(updates,'_manager',lambda manager=SimpleNamespace(telegram_user_id=42,send_to_telegram=send): manager)
    ticket={'source_metadata':{'approval_source':'telegram'}}
    updates.queue_execution_update(ticket,cid,{'id':'bad'},'done')
    updates.queue_execution_update(ticket,other_id,{'id':'good','summary':'Other exact result'},'done')
    assert send.call_count==1 and 'Other exact result' in send.call_args.args[0]
    with factory() as db:
        assert db.get(Chat,cid).params == json.dumps({updates._KEY: invalid})


def test_final_update_uses_public_work_result_over_workflow_status(store, monkeypatch):
    _, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, "_manager", lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    updates.queue_execution_update(
        {"source_metadata": {"approval_source": "telegram"}}, cid,
        {"id": 173, "output_packet": {"human_summary": "Merrypak directory verified. TELEGRAM_PROPOSAL_E2E_OK", "summary": "Workflow run 173 finished with status: completed."}},
        "done",
    )
    message = send.call_args.args[0]
    assert "TELEGRAM_PROPOSAL_E2E_OK" in message
    assert "Workflow run 173" not in message


def test_failed_update_does_not_reuse_earlier_successful_public_result(store, monkeypatch):
    _, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, "_manager", lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    updates.queue_execution_update(
        {"source_metadata": {"approval_source": "telegram"}}, cid,
        {"id": 174, "output_packet": {"human_summary": "Earlier step succeeded.", "summary": "Final checks failed."}}, "failed",
    )
    assert "Final checks failed" in send.call_args.args[0]
    assert "Earlier step succeeded" not in send.call_args.args[0]


def test_stage_updates_and_long_final_response_are_preserved(store, monkeypatch):
    factory, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, "_manager", lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=send): manager)
    ticket = {"source_metadata": {"approval_source": "telegram"}, "reference": "PROBE-2"}
    updates.queue_execution_update(ticket, cid, {"job_id": "two"}, "testing")
    updates.queue_execution_update(ticket, cid, {"job_id": "two"}, "testing")
    assert send.call_count == 1
    output = "Verified result. " * 500
    updates.queue_execution_update(ticket, cid, {"job_id": "two", "output_packet": {"output": output}}, "done")
    for index in range(5):
        with factory() as db:
            if json.loads(db.get(Chat, cid).params)[updates._KEY]['pending'] is None:
                break
        assert updates.mark_delivery_attempt(send.call_args.kwargs["interaction_token"])
        updates.record_delivery(send.call_args.kwargs["interaction_token"], telegram_message_id=123 + index, telegram_chat_id=42)
    assert output.strip() in "".join(call.args[0] for call in send.call_args_list)
    assert all(len(call.args[0]) <= 3500 for call in send.call_args_list)
    with factory() as db:
        assert json.loads(db.get(Chat, cid).params)[updates._KEY]['pending'] is None


def test_sound_respects_saved_choice_opt_out_silent_and_duplicate(monkeypatch):
    sounds._proposal_seen.clear()
    monkeypatch.setattr(sounds, "_proposal_last_sound", 0)
    monkeypatch.setattr(sounds.time, "monotonic", lambda: 100)
    settings = {"notification_sound": "chime", "incoming_proposal_sound_enabled": True}
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: settings)
    play = Mock()
    monkeypatch.setattr(sounds, "play_incoming_sound", play)
    sounds.notify_proposal_created({"request_id": 1})
    sounds.notify_proposal_created({"request_id": 1})
    assert play.call_count == 1
    assert play.call_args.kwargs["sound_path"].name == "chime.mp3"
    monkeypatch.setattr(sounds, "_proposal_last_sound", 0)
    settings["incoming_proposal_sound_enabled"] = False
    sounds.notify_proposal_created({"request_id": 2})
    settings.update(incoming_proposal_sound_enabled=True, notification_sound="none")
    sounds.notify_proposal_created({"request_id": 3})
    assert play.call_count == 1


def test_decision_invalidates_without_new_proposal_alert(monkeypatch):
    push, sound, event = Mock(), Mock(), Mock()
    monkeypatch.setattr("distr.core.incoming.events.publish_incoming_change", push)
    monkeypatch.setattr(sounds, "notify_proposal_created", sound)
    monkeypatch.setattr("distr.core.orchestration_events.emit_orchestration_event", event)
    stream.handle_work_request_signal({"request_id": 7, "event": "proposal_decided", "status": "dismissed"})
    push.assert_called_once_with(request_id=7, status="dismissed")
    sound.assert_not_called(); event.assert_not_called()


def test_execution_arrival_is_silent_but_preserves_desktop_notice(monkeypatch):
    sound, desktop, ledger = Mock(), Mock(), Mock()
    monkeypatch.setattr(sounds, 'play_incoming_sound', sound)
    monkeypatch.setattr(sounds, 'show_desktop_notification', desktop)
    monkeypatch.setattr('distr.core.orchestration_events.emit_user_notification', ledger)
    sounds.notify_tensology_ticket_incoming(ticket={'id': 7, 'title': 'Approved work'}, chat_id=17)
    sound.assert_not_called()
    desktop.assert_called_once()
    ledger.assert_called_once()


def test_delivery_receipt_preserves_execution_updated_during_enqueue(store, monkeypatch):
    factory, cid = store
    monkeypatch.setattr(updates, "_manager", lambda: None)
    updates.queue_execution_update(
        {"source_metadata": {"approval_source": "telegram"}}, cid,
        {"job_id": "concurrent"}, "testing",
    )
    def enqueue(_text, **_kwargs):
        with factory() as db:
            chat = db.get(Chat, cid)
            params = json.loads(chat.params)
            params["development"] = {"execution": {"status": "completed", "summary": "Saved final response"}}
            chat.params = json.dumps(params)
            db.commit()
        return True
    monkeypatch.setattr(updates, "_manager", lambda manager=SimpleNamespace(telegram_user_id=42, send_to_telegram=enqueue): manager)
    updates.flush_execution_updates()
    with factory() as db:
        params = json.loads(db.get(Chat, cid).params)
        assert params["development"]["execution"]["summary"] == "Saved final response"
        assert params[updates._KEY]["pending"]["offset"] == 0
        assert params[updates._KEY]["pending"]["awaiting"]["state"] == "queued"


def test_refused_enqueue_preserves_another_update_written_during_the_call(store, monkeypatch):
    factory, cid = store
    ticket = {"source_metadata": {"approval_source": "telegram"}}

    def enqueue(_text, **_kwargs):
        updates.queue_execution_update(ticket, cid, {"job_id": "later"}, "done")
        return False

    manager = SimpleNamespace(telegram_user_id=42, send_to_telegram=enqueue)
    monkeypatch.setattr(updates, "_manager", lambda: manager)
    updates.queue_execution_update(ticket, cid, {"job_id": "first"}, "testing")
    with factory() as db:
        receipt = json.loads(db.get(Chat, cid).params)[updates._KEY]
    assert receipt["pending"]["key"] == "first:testing"
    assert "awaiting" not in receipt["pending"]
    assert [row["key"] for row in receipt["backlog"]] == ["later:done"]
    send = Mock(return_value=True)
    manager.send_to_telegram = send
    updates.queue_execution_update(ticket, cid, {"job_id": "new"}, "development")
    with factory() as db:
        receipt = json.loads(db.get(Chat, cid).params)[updates._KEY]
    assert receipt["pending"]["key"] == "first:testing"
    assert [row["key"] for row in receipt["backlog"]] == ["later:done", "new:development"]
    first = send.call_args.kwargs["interaction_token"]
    assert updates.mark_delivery_attempt(first)
    assert updates.record_delivery(first, telegram_message_id=100, telegram_chat_id=42)
    assert "Completed." in send.call_args.args[0]


def test_ack_and_append_from_independent_writers_keep_both_updates(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    engine = create_engine(f"sqlite:///{tmp_path / 'telegram.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(updates, "get_session", factory)
    # Separate processes share the SQLite file, but do not share the Python lock.
    monkeypatch.setattr(updates, "_lock", contextlib.nullcontext())
    send = Mock(return_value=True)
    manager = SimpleNamespace(telegram_user_id=42, send_to_telegram=send)
    monkeypatch.setattr(updates, "_manager", lambda: manager)
    with factory() as db:
        chat = Chat(title="Proposal", params="{}")
        db.add(chat); db.flush(); cid = chat.id; db.commit()
    ticket = {"source_metadata": {"approval_source": "telegram"}}
    updates.queue_execution_update(ticket, cid, {"job_id": "first"}, "done")
    first_token = send.call_args.kwargs["interaction_token"]
    assert updates.mark_delivery_attempt(first_token)
    entered, release, ack_started, ack_finished = Event(), Event(), Event(), Event()
    write = updates._write_receipt

    def pause_append(db, chat_id, receipt):
        if (receipt.get("pending") or {}).get("key") == "first:done" and receipt.get("backlog"):
            entered.set()
            assert release.wait(5)
        write(db, chat_id, receipt)

    def ack():
        ack_started.set()
        try:
            return updates.record_delivery(first_token, telegram_message_id=100, telegram_chat_id=42)
        finally:
            ack_finished.set()

    monkeypatch.setattr(updates, "_write_receipt", pause_append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        append = pool.submit(updates.queue_execution_update, ticket, cid, {"job_id": "later"}, "done")
        try:
            assert entered.wait(5)
            acknowledged = pool.submit(ack)
            assert ack_started.wait(5)
            ack_finished.wait(.2)
        finally:
            release.set()
        append.result(timeout=5)
        assert acknowledged.result(timeout=5)
    with factory() as db:
        receipt = json.loads(db.get(Chat, cid).params)[updates._KEY]
    assert receipt["delivered_keys"] == ["first:done"]
    assert receipt["pending"]["key"] == "later:done"
    assert send.call_count == 2
    engine.dispose()
