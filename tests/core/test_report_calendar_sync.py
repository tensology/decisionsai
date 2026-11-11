from datetime import datetime

import pytest

from distr.core.reports import calendar_sync as sync
from distr.core.tensology_client import TensologyApiError


def instant(hour):
    return f"2026-11-11T{hour}:00:00+02:00"


@pytest.fixture
def calendar(monkeypatch, tmp_path):
    class Calendar:
        def __init__(self):
            self.rows = [{
                "id": 10, "title": "Saved title", "start_at": instant("10"), "end_at": instant("11"),
                "project_id": 1, "billable": False, "billing_excluded": False,
            }]
            self.writes = []

        def get(self, path, **kwargs):
            if path == "projects":
                return [{"id": 1, "name": "Project A"}, {"id": 2, "name": "Project B"}]
            return [dict(row) for row in self.rows]

        def post(self, path, body, *, idempotency_key):
            self.writes.append((path, body, idempotency_key))
            row = {"id": 11, "billing_excluded": False, **body}
            self.rows.append(row)
            return row

        def patch(self, path, changes, *, idempotency_key):
            self.writes.append((path, changes, idempotency_key))
            self.rows[0].update(changes)
            return dict(self.rows[0])

    client = Calendar()
    monkeypatch.setattr(sync, "_client", lambda: client)
    monkeypatch.setattr(sync, "review_path", lambda: tmp_path / "calendar_review.json")
    monkeypatch.setattr(sync, "load_review", lambda: {})
    monkeypatch.setattr(sync, "load_drafts", lambda: {"entries": [{"id": "test-draft"}]})
    return client


def save(**changes):
    return sync.save_entry(**{
        "entry_id": None, "draft_id": "test-draft", "version": None,
        "original_start": datetime.fromisoformat(instant("10")),
        "original_end": datetime.fromisoformat(instant("11")),
        "title": "Revised title", "start_at": datetime.fromisoformat(instant("10")),
        "end_at": datetime.fromisoformat(instant("11")), "project_id": 2, **changes,
    })


def test_draft_is_saved_once_and_stays_out_of_billing(calendar):
    assert save() == save() == {"id": 11}
    assert len(calendar.writes) == 1
    assert calendar.rows[-1]["billable"] is False
    assert calendar.rows[-1]["project_id"] == 2
    assert not sync.unsaved_drafts(sync.load_drafts(), calendar.rows)
    assert not sync.unsaved_drafts(sync.load_drafts(), [])


def test_existing_edit_is_targeted_and_requires_current_version(calendar):
    old = sync.edit_version(calendar.rows[0])
    assert save(entry_id=10, draft_id=None, version=old) == {"id": 10}
    assert calendar.writes[0][1] == {"title": "Revised title", "project_id": 2, "billable": False}
    with pytest.raises(sync.CalendarEditError, match="changed") as error:
        save(entry_id=10, draft_id=None, version=old)
    assert error.value.status == 409
    assert len(calendar.writes) == 1


@pytest.mark.parametrize("review", [
    {"invoiced_events": {"10": "invoice"}},
    {"evidence": {"10": {"allocation_estimate": {"approved_minutes": 60}}}},
])
def test_approved_allocations_cannot_be_edited(calendar, monkeypatch, review):
    monkeypatch.setattr(sync, "load_review", lambda: review)
    with pytest.raises(sync.CalendarEditError, match="approved billing allocation"):
        save(entry_id=10, draft_id=None, version=sync.edit_version(calendar.rows[0]))
    assert not calendar.writes


def test_denied_draft_save_can_be_corrected(calendar, monkeypatch):
    def reject(*args, **kwargs):
        raise TensologyApiError("Rejected", status=403)

    with monkeypatch.context() as scoped:
        scoped.setattr(calendar, "post", reject)
        with pytest.raises(TensologyApiError):
            save()
    assert save(title="Corrected title") == {"id": 11}
