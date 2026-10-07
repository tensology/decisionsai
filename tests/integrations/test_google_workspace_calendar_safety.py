from datetime import datetime, timedelta, timezone

from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector


def _connector(monkeypatch):
    connector = GoogleWorkspaceConnector.__new__(GoogleWorkspaceConnector)
    captured = {}
    monkeypatch.setattr(connector, "_ensure_valid_token", lambda: True)

    def request(method, url, **kwargs):
        captured.update(method=method, url=url, kwargs=kwargs)
        return {"id": "evt-1"}

    monkeypatch.setattr(connector, "_make_request", request)
    return connector, captured


def test_create_calendar_event_keeps_requested_local_wall_time(monkeypatch) -> None:
    connector, captured = _connector(monkeypatch)

    event_id = connector.create_calendar_event(
        "Meeting",
        datetime(2026, 10, 7, 12, 30),
        datetime(2026, 10, 7, 14, 30),
        time_zone="Africa/Johannesburg",
    )

    event = captured["kwargs"]["json"]
    assert event_id == "evt-1"
    assert event["start"] == {
        "dateTime": "2026-10-07T12:30:00+02:00",
        "timeZone": "Africa/Johannesburg",
    }
    assert event["end"]["dateTime"] == "2026-10-07T14:30:00+02:00"


def test_calendar_query_converts_offset_to_real_utc(monkeypatch) -> None:
    connector, captured = _connector(monkeypatch)
    start = datetime(2026, 10, 7, 12, 30, tzinfo=timezone(timedelta(hours=2)))

    connector.get_calendar_events(start, start + timedelta(hours=2))

    params = captured["kwargs"]["params"]
    assert params["timeMin"] == "2026-10-07T10:30:00Z"
    assert params["timeMax"] == "2026-10-07T12:30:00Z"


def test_update_calendar_event_patches_existing_event(monkeypatch) -> None:
    connector, captured = _connector(monkeypatch)

    updated = connector.update_calendar_event(
        "evt-1",
        start_time=datetime(2026, 10, 7, 12, 30),
        end_time=datetime(2026, 10, 7, 14, 30),
        time_zone="Africa/Johannesburg",
    )

    assert updated
    assert captured["method"] == "PATCH"
    assert captured["url"].endswith("/evt-1")
    assert captured["kwargs"]["json"]["start"]["dateTime"].endswith("+02:00")
