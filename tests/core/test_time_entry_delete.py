"""Reports delete: Tensology entries are deleted through the API (version + lock checked); drafts stay local."""
import json
from datetime import datetime

import pytest

from distr.core import tensology_stream
from distr.core.reports import calendar_sync as sync
from distr.core.tensology_client import TensologyApiError

START = datetime.fromisoformat("2026-11-13T06:00:00+02:00")
END = datetime.fromisoformat("2026-11-13T06:18:00+02:00")
ROW = {"id": 653, "title": "SYNC TEST — safe to remove", "start_at": "2026-11-13T04:00:00Z",
       "end_at": "2026-11-13T04:18:00Z", "project_id": 2, "billable": False, "is_timer_running": False}


@pytest.fixture
def published(monkeypatch):
    sent = []
    import distr.gui.web.routes.chat as chat

    monkeypatch.setattr(chat, "publish_topic_event_threadsafe", lambda topic, payload: sent.append((topic, payload)))
    sync._recent_local_writes.clear()
    return sent


@pytest.fixture
def calendar(monkeypatch, tmp_path, published):
    class Calendar:
        def __init__(self):
            self.rows, self.deleted, self.fail = [dict(ROW)], [], None

        def get(self, path, **kwargs):
            if path == "projects":
                return [{"id": 2, "name": "Internal", "is_billable": False}]
            return [dict(row) for row in self.rows]

        def delete(self, path):
            if self.fail:
                raise self.fail
            self.deleted.append(path)
            self.rows = [row for row in self.rows if f"time-entries/{row['id']}" != path]
            return {"deleted": True}

    client = Calendar()
    monkeypatch.setattr(sync, "_client", lambda: client)
    monkeypatch.setattr(sync, "review_path", lambda: tmp_path / "calendar_review.json")
    monkeypatch.setattr(sync, "load_review", lambda: {})
    return client


def _delete(**kwargs):
    return sync.delete_entry(original_start=START, original_end=END, **kwargs)


def test_deletes_entry_in_tensology_and_flags_echo(calendar, published):
    result = _delete(entry_id=653, version=sync.edit_version(ROW))
    assert result == {"id": 653, "deleted": True}
    assert calendar.deleted == ["time-entries/653"]
    assert published[-1][1]["event"] == "deleted" and published[-1][1]["origin"] == "decisions"
    tensology_stream.handle_time_entry_signal({"entry_id": 653, "event": "deleted"})
    assert published[-1][1]["echo"] is True


def test_stale_version_is_refused(calendar):
    with pytest.raises(sync.CalendarEditError) as error:
        _delete(entry_id=653, version="stale")
    assert error.value.status == 409 and calendar.deleted == []


def test_locked_entry_is_refused(calendar, monkeypatch):
    monkeypatch.setattr(sync, "load_review", lambda: {"invoiced_events": {"653": "INV-1"}})
    with pytest.raises(sync.CalendarEditError) as error:
        _delete(entry_id=653, version=sync.edit_version(ROW))
    assert error.value.status == 403 and calendar.deleted == []


def test_tensology_refusal_surfaces(calendar):
    calendar.fail = TensologyApiError("This time entry is on an invoice and cannot be deleted.", status=409, code="time_entry_invoiced")
    with pytest.raises(TensologyApiError) as error:
        _delete(entry_id=653, version=sync.edit_version(ROW))
    assert error.value.status == 409


def test_already_gone_counts_as_deleted(calendar):
    calendar.fail = TensologyApiError("Time entry not found.", status=404, code="not_found")
    assert _delete(entry_id=653, version=sync.edit_version(ROW))["deleted"] is True


def test_draft_delete_only_rewrites_local_drafts(calendar, tmp_path, published):
    path = tmp_path / "time_drafts.json"
    path.write_text(json.dumps({"schema": 1, "days": ["2026-11-13"], "entries": [{"id": "keep"}, {"id": "drop"}]}))
    assert sync.delete_entry(draft_id="drop") == {"draft_id": "drop", "deleted": True}
    saved = json.loads(path.read_text())
    assert saved == {"schema": 1, "days": ["2026-11-13"], "entries": [{"id": "keep"}]}
    assert calendar.deleted == [] and published[-1][1]["event"] == "draft_deleted"
    with pytest.raises(sync.CalendarEditError):
        sync.delete_entry(draft_id="drop")


def test_sent_draft_is_not_silently_dropped(calendar, tmp_path):
    (tmp_path / "time_drafts.json").write_text(json.dumps({"days": [], "entries": [{"id": "sent"}]}))
    sync._write_receipt(sync._receipt_path("sent"), {"event_id": 900})
    with pytest.raises(sync.CalendarEditError) as error:
        sync.delete_entry(draft_id="sent")
    assert error.value.status == 409


def test_draft_delete_keeps_file_format(calendar, tmp_path):
    path = tmp_path / "time_drafts.json"
    keep = {"id": "keep", "title": "Résumé — work"}
    path.write_text(json.dumps({"schema": 1, "entries": [keep]}, indent=2, ensure_ascii=False) + "\n")
    expected = path.read_text()
    data = json.loads(expected)
    data["entries"].append({"id": "drop"})
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    sync.delete_entry(draft_id="drop")
    assert path.read_text() == expected
