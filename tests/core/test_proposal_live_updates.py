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
    monkeypatch.setattr(updates, "_manager", lambda: SimpleNamespace(telegram_user_id=42, send_to_telegram=send))
    updates.flush_execution_updates()
    updates.flush_execution_updates()
    updates.queue_execution_update(ticket, cid, state, "done")
    assert send.call_count == 2
    assert "Marker verified" in send.call_args.args[0]
    with factory() as db:
        assert json.loads(db.get(Chat, cid).params)[updates._KEY] == {"sent": "one:done", "pending": None}


def test_non_telegram_ticket_stays_quiet(store, monkeypatch):
    _, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, "_manager", lambda: SimpleNamespace(telegram_user_id=42, send_to_telegram=send))
    updates.queue_execution_update({"source_metadata": {}}, cid, {"summary": "Done"}, "done")
    send.assert_not_called()


def test_final_update_uses_public_work_result_over_workflow_status(store, monkeypatch):
    _, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, "_manager", lambda: SimpleNamespace(telegram_user_id=42, send_to_telegram=send))
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
    monkeypatch.setattr(updates, "_manager", lambda: SimpleNamespace(telegram_user_id=42, send_to_telegram=send))
    updates.queue_execution_update(
        {"source_metadata": {"approval_source": "telegram"}}, cid,
        {"id": 174, "output_packet": {"human_summary": "Earlier step succeeded.", "summary": "Final checks failed."}}, "failed",
    )
    assert "Final checks failed" in send.call_args.args[0]
    assert "Earlier step succeeded" not in send.call_args.args[0]


def test_stage_updates_and_long_final_response_are_preserved(store, monkeypatch):
    _, cid = store
    send = Mock(return_value=True)
    monkeypatch.setattr(updates, "_manager", lambda: SimpleNamespace(telegram_user_id=42, send_to_telegram=send))
    ticket = {"source_metadata": {"approval_source": "telegram"}, "reference": "PROBE-2"}
    updates.queue_execution_update(ticket, cid, {"job_id": "two"}, "testing")
    updates.queue_execution_update(ticket, cid, {"job_id": "two"}, "testing")
    assert send.call_count == 1
    output = "Verified result. " * 500
    updates.queue_execution_update(ticket, cid, {"job_id": "two", "output_packet": {"output": output}}, "done")
    assert output.strip() in "".join(call.args[0] for call in send.call_args_list)
    assert all(len(call.args[0]) <= 3500 for call in send.call_args_list)


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
    monkeypatch.setattr(updates, "_manager", lambda: SimpleNamespace(telegram_user_id=42, send_to_telegram=enqueue))
    updates.flush_execution_updates()
    with factory() as db:
        params = json.loads(db.get(Chat, cid).params)
        assert params["development"]["execution"]["summary"] == "Saved final response"
        assert params[updates._KEY] == {"sent": "concurrent:testing", "pending": None}
