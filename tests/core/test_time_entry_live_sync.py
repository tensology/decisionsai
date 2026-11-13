"""Live time-entry sync: pushed Tensology changes and Decisions write-through refresh Reports."""
from datetime import datetime

import pytest

from distr.core import tensology_stream
from distr.core.reports import calendar_sync as sync


@pytest.fixture
def published(monkeypatch):
    sent = []
    import distr.gui.web.routes.chat as chat

    monkeypatch.setattr(chat, "publish_topic_event_threadsafe", lambda topic, payload: sent.append((topic, payload)))
    sync._recent_local_writes.clear()
    return sent


def test_pushed_tensology_change_notifies_reports(published):
    before = sync.time_entry_change_version()
    payload = tensology_stream.handle_time_entry_signal({"type": "decisions_time_entry", "entry_id": 652, "event": "updated"})
    assert published[-1][0] == "time_entries"
    assert published[-1][1]["entry_id"] == 652 and published[-1][1]["echo"] is False
    assert payload["origin"] == "tensology"
    assert sync.time_entry_change_version() == before + 1


def test_own_write_echo_is_flagged(published):
    sync._record_local_write(700)
    tensology_stream.handle_time_entry_signal({"entry_id": 700, "event": "updated"})
    assert published[-1][1]["echo"] is True
    tensology_stream.handle_time_entry_signal({"entry_id": 701, "event": "updated"})
    assert published[-1][1]["echo"] is False


def test_reconnect_resync_notice(published):
    tensology_stream.handle_time_entry_signal({"event": "resync"})
    assert published[-1][1]["event"] == "resync" and published[-1][1]["entry_id"] is None


@pytest.fixture
def calendar(monkeypatch, tmp_path, published):
    class Calendar:
        def __init__(self):
            self.rows, self.writes = [], []

        def get(self, path, **kwargs):
            if path == "projects":
                return [{"id": 1, "name": "Billable", "is_billable": True}, {"id": 2, "name": "Internal", "is_billable": False}]
            return [dict(row) for row in self.rows]

        def post(self, path, body, *, idempotency_key):
            self.writes.append(body)
            row = {"id": 90 + len(self.writes), **body}
            self.rows.append(row)
            return row

    client = Calendar()
    monkeypatch.setattr(sync, "_client", lambda: client)
    monkeypatch.setattr(sync, "review_path", lambda: tmp_path / "calendar_review.json")
    monkeypatch.setattr(sync, "load_review", lambda: {})
    monkeypatch.setattr(sync, "load_drafts", lambda: {"entries": [{"id": "a"}, {"id": "b"}]})
    return client


def _save(draft_id, project_id):
    start, end = datetime.fromisoformat("2026-11-11T10:00:00+02:00"), datetime.fromisoformat("2026-11-11T10:20:00+02:00")
    return sync.save_entry(entry_id=None, draft_id=draft_id, version=None, original_start=start, original_end=end,
                           title="Work", start_at=start, end_at=end, project_id=project_id)


def test_saved_draft_takes_project_billable_flag_and_notifies(calendar, published):
    _save("a", 1)
    _save("b", 2)
    assert [body["billable"] for body in calendar.writes] == [True, False]
    assert [payload["origin"] for _, payload in published[-2:]] == ["decisions", "decisions"]
    assert sync.is_local_echo(91) and sync.is_local_echo(92)
