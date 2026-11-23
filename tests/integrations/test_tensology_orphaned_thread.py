from unittest.mock import MagicMock, call

import pytest

from distr.core import tensology_client as tc
from distr.core.incoming import telegram_updates


@pytest.fixture
def state_sync(monkeypatch):
    with tc._TENSOLOGY_TICKETS_LOCK:
        monkeypatch.setattr(tc, "_TENSOLOGY_TICKETS", {})
        monkeypatch.setattr(tc, "_ORPHANED_TENSOLOGY_THREADS", {})
    monkeypatch.setattr(tc, "_LAST_STATE_SYNC", 0.0)
    monkeypatch.setattr(tc, "_ticket_push_paused", lambda: False)
    monkeypatch.setattr(telegram_updates, "sync_local_proposal_outcomes", lambda: None)
    monkeypatch.setattr(telegram_updates, "flush_execution_updates", lambda: None)
    client = MagicMock()
    monkeypatch.setattr(tc, "configured_tensology_client", lambda **kwargs: client)
    monkeypatch.setattr(tc, "_existing_tensology_thread", lambda ticket: int(ticket["decisions_thread_id"]))
    sync = MagicMock()
    monkeypatch.setattr(tc, "_sync_tensology_ticket_state", sync)
    return client, sync


@pytest.fixture
def orphan():
    return {"id": 81, "reference": "TEST-81", "decisions_thread_id": "42", "execution_status": "development"}


def test_missing_thread_warns_once_across_three_ticks(state_sync, orphan, caplog):
    client, sync = state_sync
    sync.side_effect = LookupError("Development thread not found.")
    tc._remember_tensology_tickets([orphan])

    for _ in range(3):
        assert tc.sync_tensology_execution_states(force=True) == 0

    sync.assert_called_once_with(client, orphan, 42)
    assert [(record.levelname, record.getMessage()) for record in caplog.records if record.name == tc.__name__] == [
        ("WARNING", "Tensology ticket TEST-81 points at missing Development thread 42; "
                    "skipping its state sync until the ticket is re-linked."),
    ]
    client.patch.assert_not_called()


def test_healthy_claimed_ticket_after_orphan_still_syncs(state_sync, orphan):
    client, sync = state_sync
    healthy = {**orphan, "id": 82, "reference": "TEST-82", "decisions_thread_id": "43"}
    sync.side_effect = [LookupError("Development thread not found."), True]
    tc._remember_tensology_tickets([orphan, healthy])

    assert tc.sync_tensology_execution_states(force=True) == 1
    assert sync.call_args_list == [call(client, orphan, 42), call(client, healthy, 43)]


def test_relinked_ticket_resumes_sync_and_clears_orphan(state_sync, orphan):
    client, sync = state_sync
    relinked = {**orphan, "decisions_thread_id": "43"}
    sync.side_effect = [LookupError("Development thread not found."), False]
    tc._remember_tensology_tickets([orphan])
    assert tc.sync_tensology_execution_states(force=True) == 0

    tc._remember_tensology_tickets([relinked])
    assert tc.sync_tensology_execution_states(force=True) == 0

    assert sync.call_args_list == [call(client, orphan, 42), call(client, relinked, 43)]
    with tc._TENSOLOGY_TICKETS_LOCK:
        assert orphan["id"] not in tc._ORPHANED_TENSOLOGY_THREADS
